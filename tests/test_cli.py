"""Exercise the local archive through the real CLI, without network access."""

import json
import socket

import gzip
import shutil

import pytest
import xarray as xr

from soba_matchups.cli import main


def test_local_stac_run_groups_granules_and_preserves_output(local_recipe, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Local matching must not open a network connection")

    monkeypatch.setattr(socket, "create_connection", no_network)
    recipe = local_recipe
    assert main(["validate", "--recipe", str(recipe)]) == 0
    assert main(["run", "--recipe", str(recipe)]) == 0
    result = recipe.parent / "result"
    manifest = json.loads((result / "manifest.json").read_text())
    items = [json.loads(p.read_text()) for p in sorted((result / "items").glob("*.json"))]
    assert len(items) == manifest["stac_items"] == 2
    assert manifest["matchup_imagette_cell_rows"] == 8
    assert manifest["discovery"]["source"] == "local_ocn"
    points = []
    for item in items:
        assert item["type"] == "Feature"
        assert item["stac_version"] == "1.0.0"
        assert item["assets"]["sar"]["href"].startswith("file:")
        assert (
            item["properties"]["sarmatchup:mapping_policy"]
            == "local_ocn_geolocation_to_manifest_frame"
        )
        groups = item["properties"]["sarmatchup:matchups"]
        # Reference assets point at the configured absolute root, not a container mount.
        root = (local_recipe.parent / "reference").resolve().as_uri()
        assert item["assets"]["reference_001"]["href"].startswith(root + "/")
        assert len(groups) == 2
        points.extend(point for group in groups for point in group["points"])
    assert len({p["record_id"] for p in points}) == 8
    assert {p["ref_cell_0based"] for p in points} == {0, 1, 2, 3}
    assert any(p["quality_flag"] == 2048 for p in points)
    from soba_matchups import __version__

    for path in (result / "items").glob("*.json"):
        item = json.loads(path.read_text())
        props = item["properties"]
        assert props["sarmatchup:sar_platform"] == "sentinel-1a"
        assert props["sarmatchup:sar_instrument_mode"] == "WV"
        assert props["sarmatchup:sar_product_type"] == "OCN"
        assert props["sarmatchup:sar_safe"].endswith(".SAFE")
        assert not [key for key in props if key.startswith("soba:")]
        assert not {
            "platform",
            "sarmatchup:sea_percent",
            "sarmatchup:safe_id",
            "sarmatchup:safe_id_source",
            "sarmatchup:time_source",
            "sarmatchup:time_tolerance_minutes",
            "sarmatchup:sar_ocn_safe",
            "sarmatchup:sar_start_datetime",
            "sarmatchup:sar_stop_datetime",
        }.intersection(props)
        assert props["start_datetime"] < props["end_datetime"]
        assert props["sarmatchup:other_instrument"] == "HY-2B HSCAT"
        assert props["sarmatchup:other_type"] == "scatterometer"
        assert props["sarmatchup:other_geometry_type"] == "MultiPoint"
        assert props["sarmatchup:searching_window_hours"] == 1 / 60
        assert props["sarmatchup:lib_version"] == __version__
        groups = props["sarmatchup:matchups"]
        assert props["sarmatchup:n_coloc_points"] == sum(len(g["points"]) for g in groups)
        for group in groups:
            assert group["sarmatchup:other_id"] == group["reference_file"]
            assert "reference_geometry" not in group
            for point in group["points"]:
                assert point["delta_diff_time_minutes"] == -0.5 / 60
        assert props["sarmatchup:delta_diff_time_minutes_min"] == -0.5 / 60
        assert props["sarmatchup:delta_diff_time_minutes_max"] == -0.5 / 60
        assert "sarmatchup:delta_diff_time_minutes" not in props
        stem = props["sarmatchup:sar_safe"].removesuffix(".SAFE")
        index = props["sarmatchup:imagette_index"]
        assert path.name == f"{stem}__IMG{index:03d}__KNMI_HSCAT_HY2B_25KM.json"
        assert item["id"] != path.stem
    before = (result / "manifest.json").read_bytes()
    assert main(["run", "--recipe", str(recipe)]) == 2
    assert (result / "manifest.json").read_bytes() == before


def test_coarse_candidates_preserve_quantized_header_boundary_match(local_recipe):
    import xml.etree.ElementTree as ET
    import numpy as np
    import xarray as xr

    manifest = next(local_recipe.parent.glob("esa/**/manifest.safe"))
    root = ET.parse(manifest).getroot()
    next(
        e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "startTime"
    ).text = "2026-06-29T12:00:00.013725"
    ET.ElementTree(root).write(manifest, encoding="UTF-8", xml_declaration=True)
    for path in local_recipe.parent.glob("reference/**/*.nc"):
        with xr.open_dataset(path) as opened:
            data = opened.load()
        data["time"].values[0, 0] = np.datetime64("2026-06-29T11:59:00.005", "ns")
        data["time"].encoding = {}
        data.to_netcdf(path)
    assert main(["run", "--recipe", str(local_recipe)]) == 0
    result = json.loads((local_recipe.parent / "result/manifest.json").read_text())
    assert result["matchup_imagette_cell_rows"] == 8


@pytest.mark.parametrize(
    "sensor,platform,resolution,version,source,pixel,suffix",
    [
        ("ascat", "metop-b", "25km", "3202", "MetOp-B ASCAT", "25.0 km", "KNMI_ASCAT_METOPB_25KM"),
        (
            "ascat",
            "metop-b",
            "12.5km",
            "2101",
            "MetOp-B ASCAT",
            "12.5 km",
            "KNMI_ASCAT_METOPB_12P5KM",
        ),
        ("ascat", "metop-b", "5.7km", "3307", "MetOp-B ASCAT", "5.7 km", "KNMI_ASCAT_METOPB_5P7KM"),
        (
            "ascat",
            "metop-c",
            "12.5km",
            "3203",
            "MetOp-C ASCAT",
            "12.5 km",
            "KNMI_ASCAT_METOPC_12P5KM",
        ),
        ("hscat", "hy-2b", "25km", "4008", "HY-2B HSCAT", "25.0 km", "KNMI_HSCAT_HY2B_25KM"),
        ("hscat", "hy-2c", "25km", "4006", "HY-2C HSCAT", "25.0 km", "KNMI_HSCAT_HY2C_25KM"),
        ("hscat", "hy-2d", "25km", "4002", "HY-2D HSCAT", "25.0 km", "KNMI_HSCAT_HY2D_25KM"),
    ],
)
def test_knmi_cli_product_labels_and_actual_provenance(
    local_recipe, sensor, platform, resolution, version, source, pixel, suffix, monkeypatch
):
    def no_network(*args, **kwargs):
        raise AssertionError("Offline product integration must not use the network")

    monkeypatch.setattr(socket, "create_connection", no_network)
    text = local_recipe.read_text()
    for old, new in [
        ("hscat", sensor),
        ("hy-2b", platform),
        ("25km", resolution),
    ]:
        text = text.replace(f'"{old}"', f'"{new}"')
    local_recipe.write_text(text)
    for path in list(local_recipe.parent.glob("reference/**/*.nc")):
        with xr.open_dataset(path) as opened:
            ds = opened.load()
        ds.attrs.update(
            source=source, pixel_size_on_horizontal=pixel, software_identification_wind=int(version)
        )
        if version == "2101":
            del ds.wind_dir.attrs["standard_name"]
            ds.attrs["comment"] = (
                "Orbit period and inclination are constant values. "
                "All wind directions in oceanographic convention (0 deg. flowing North)"
            )
        target = (
            local_recipe.parent
            / "reference"
            / platform
            / sensor
            / resolution
            / "data/2026/180"
            / path.name.replace(".nc", "_ovw_l2.nc")
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        ds.to_netcdf(target)
        with target.open("rb") as incoming, gzip.open(str(target) + ".gz", "wb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        target.unlink()
        path.unlink()
    assert main(["run", "--recipe", str(local_recipe)]) == 0
    result = local_recipe.parent / "result"
    manifest = json.loads((result / "manifest.json").read_text())
    assert manifest["schema_version"] == 11
    assert manifest["reference_product"] == f"KNMI_{suffix.split('_', 1)[1]}"
    assert manifest["stac_items"] == 2
    assert manifest["matchup_imagette_cell_rows"] == 8
    for entry in manifest["reference_inputs"]:
        assert (entry["source"], entry["version"], entry["resolution"]) == (source, version, pixel)
    for path in (result / "items").glob("*.json"):
        assert path.name.endswith(f"__{suffix}.json")
        item = json.loads(path.read_text())
        assert item["properties"]["sarmatchup:other_instrument"] == source
        for group in item["properties"]["sarmatchup:matchups"]:
            assert (
                group["reference_source"],
                group["reference_version"],
                group["reference_resolution"],
            ) == (source, version, pixel)
            assert item["assets"][group["reference_asset"]]["type"] == "application/gzip"


def test_incomplete_local_safe_does_not_publish(local_recipe):
    recipe = local_recipe
    next(recipe.parent.glob("esa/**/measurement/*.nc")).unlink()
    assert main(["run", "--recipe", str(recipe)]) == 2
    assert not (recipe.parent / "result").exists()
