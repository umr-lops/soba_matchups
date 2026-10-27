import gzip
import shutil

import numpy as np
import pytest
import xarray as xr


def test_safe_matching_preserves_components_and_strict_boundaries():
    from soba_matchups.adapters.scat import match_cells

    product = {
        "ContentDate": {"Start": "2020-01-10T12:00:00Z", "End": "2020-01-10T12:30:00Z"},
        "GeoFootprint": {
            "type": "MultiPolygon",
            "coordinates": [
                [[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]],
                [[(2, 0), (3, 0), (3, 1), (2, 1), (2, 0)]],
                [[(179.8, 0), (180, 0), (180, 1), (179.8, 1), (179.8, 0)]],
                [[(-180, 0), (-179.8, 0), (-179.8, 1), (-180, 1), (-180, 0)]],
            ],
        },
    }
    ref = {
        "lon": np.array([0.5, 2.5, 0, 0.5, 0.5, 179.9, -179.9]),
        "lat": np.full(7, 0.5),
        "wind_speed": np.array([5, 5, 5, 5, np.nan, 5, 5]),
        "wind_dir": np.full(7, 90.0),
        "time": np.array(["2020-01-10T12:20:00"] * 7, dtype="datetime64[ns]"),
    }
    ref["time"][3] = np.datetime64("2020-01-10T13:30:00")
    assert match_cells(product, ref, 60).tolist() == [0, 1, 5, 6]


def test_dateline_seam_is_interior_not_outer_boundary():
    from soba_matchups.adapters.scat import match_cells
    from shapely.geometry import MultiPolygon, box, mapping

    product = {
        "ContentDate": {"Start": "2026-06-29T12:00:00Z", "End": "2026-06-29T12:00:03Z"},
        "GeoFootprint": mapping(MultiPolygon([box(179.8, 10, 180, 11), box(-180, 10, -179.8, 11)])),
    }
    ref = {
        "lon": np.array([179.9, -179.9, 180, -180, 179.8, 0]),
        "lat": np.full(6, 10.5),
        "time": np.full(6, np.datetime64("2026-06-29T12:00:01", "ns")),
        "wind_speed": np.full(6, 5.0),
        "wind_dir": np.full(6, 90.0),
    }
    assert match_cells(product, ref, 1).tolist() == [0, 1, 2, 3]


def test_hscat_reader_keeps_original_indices_and_checks_product(tmp_path):
    from soba_matchups.adapters.scat import read_reference

    data = xr.Dataset(
        {
            key: (("NUMROWS", "NUMCELLS"), value)
            for key, value in {
                "lon": [[0.5, 0.5], [2.5, 2.5]],
                "lat": [[0.5, 0.5], [0.5, 0.5]],
                "time": np.full((2, 2), np.datetime64("2020-01-10T12:20:00", "ns")),
                "wind_speed": [[5.0, np.nan], [5.0, 5.0]],
                "wind_dir": [[90.0, 90.0], [90.0, 90.0]],
                "wvc_quality_flag": np.zeros((2, 2)),
                "ice_prob": np.zeros((2, 2)),
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
    data.wind_speed.attrs["units"] = "m s-1"
    data.wind_dir.attrs["units"] = "degree"
    path = tmp_path / "control.nc"
    data["time"].values[1, 1] = np.datetime64("2020-01-10T12:31:00", "ns")
    data.to_netcdf(path)
    compressed = tmp_path / "control.nc.gz"
    with path.open("rb") as src, gzip.open(compressed, "wb") as dst:
        shutil.copyfileobj(src, dst)
    ref = read_reference(compressed, "2202")
    assert ref["flat"].tolist() == [0, 2, 3]
    assert ref["columns"] == 2
    assert ref["coverage_end"].minute == 31
    with pytest.raises(ValueError, match="version"):
        read_reference(path, "other")
