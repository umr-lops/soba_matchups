import csv
import json
from uuid import UUID

import numpy as np
import xarray as xr


def test_cli_runs_real_reader_and_outputs_without_network(tmp_path, monkeypatch):
    from soba_matchups.cli import main
    import soba_matchups.pipeline as pipeline

    reference_dir = tmp_path / "references"
    reference_dir.mkdir()
    day_dir = reference_dir / "hy-2b/hscat/25km/data/2020/010"
    day_dir.mkdir(parents=True)
    ds = xr.Dataset(
        {
            k: (("NUMROWS", "NUMCELLS"), [[v]])
            for k, v in {
                "lon": 0.5,
                "lat": 0.5,
                "wind_speed": 5.0,
                "wind_dir": 90.0,
                "time": np.datetime64("2020-01-10T12:20:00", "ns"),
                "wvc_quality_flag": 2048.0,
                "ice_prob": 0.5,
            }.items()
        },
        attrs={
            "source": "HY-2B HSCAT",
            "software_identification_wind": "2202",
            "pixel_size_on_horizontal": "25.0 km",
            "start_date": "2020-01-10",
            "start_time": "12:00:00",
            "stop_date": "2020-01-10",
            "stop_time": "12:30:00",
        },
    )
    ds.wind_speed.attrs["units"] = "m s-1"
    ds.wind_dir.attrs["units"] = "degree"
    ds.to_netcdf(day_dir / "control.nc")
    recipe = tmp_path / "control.toml"
    recipe.write_text("""scat = "hscat"
met = "hy-2b"
resolution = "25km"
reference_version = "2202"
reference_root = "references"
start_date = "2020-01-10"
stop_date = "2020-01-11"
sar_missions = ["S1A"]
time_tolerance_minutes = 60
sar_cache_dir = "cache"
output_dir = "run"
""")
    calls = []
    product = {
        "Id": str(UUID(int=1)),
        "Name": "S1A_WV_OCN__2SSV_20200110T120000_20200110T123000_000001_000001_0001.SAFE",
        "ContentDate": {"Start": "2020-01-10T12:00:00Z", "End": "2020-01-10T12:30:00Z"},
        "GeoFootprint": {
            "type": "Polygon",
            "coordinates": [[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]],
        },
    }

    def discovery(*args, **kwargs):
        calls.append(args)
        return [product], {"source": "synthetic HTTP-boundary control", "complete": True}

    monkeypatch.setattr(pipeline, "discover", discovery)
    assert main(["validate", "--recipe", str(recipe)]) == 0
    assert not calls and not (tmp_path / "run").exists()
    assert main(["run", "--recipe", str(recipe)]) == 0
    with (tmp_path / "run/matchups.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1 and rows[0]["quality_flag"] == "2048.0"
    assert len(rows[0]["record_id"]) == 64
    assert not {"reference_sha256", "matching_policy"} & rows[0].keys()
    manifest = json.loads((tmp_path / "run/manifest.json").read_text())
    assert len(manifest["reference_inputs"][0]["sha256"]) == 64
    assert manifest["rules"]["policy"] == "scat_safe_v1"
    assert rows[0]["ref_row_0based"] == rows[0]["ref_cell_0based"] == "0"
    assert json.loads((tmp_path / "run/manifest.json").read_text())["status"] == "success"
    assert main(["run", "--recipe", str(recipe)]) == 2
    recipe.write_text(recipe.read_text().replace('output_dir = "run"', 'output_dir = "empty"'))
    product["GeoFootprint"]["coordinates"] = [[(2, 0), (3, 0), (3, 1), (2, 1), (2, 0)]]
    assert main(["run", "--recipe", str(recipe)]) == 0
    with (tmp_path / "empty/matchups.csv").open() as stream:
        assert list(csv.DictReader(stream)) == []
    empty = json.loads((tmp_path / "empty/manifest.json").read_text())
    assert empty["status"] == "no_matches" and empty["matchup_safe_cell_rows"] == 0
