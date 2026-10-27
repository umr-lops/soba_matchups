"""Exercise the local archive through the real CLI, without network access."""

import json
import socket

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
            item["properties"]["soba:mapping_policy"] == "local_ocn_geolocation_to_manifest_frame"
        )
        groups = item["properties"]["soba:matchups"]
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
        assert not {
            "platform",
            "soba:safe_name",
            "soba:producttype",
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
        groups = props["soba:matchups"]
        assert props["sarmatchup:n_coloc_points"] == sum(len(g["points"]) for g in groups)
        for group in groups:
            assert group["sarmatchup:other_id"] == group["reference_file"]
            assert group["reference_geometry"] == {
                "type": "MultiPoint",
                "coordinates": [[p["ref_lon"], p["ref_lat"]] for p in group["points"]],
            }
            for point in group["points"]:
                assert point["delta_diff_time_minutes"] == -0.5 / 60
        assert props["sarmatchup:delta_diff_time_minutes_min"] == -0.5 / 60
        assert props["sarmatchup:delta_diff_time_minutes_max"] == -0.5 / 60
        assert "sarmatchup:delta_diff_time_minutes" not in props
        stem = props["sarmatchup:sar_safe"].removesuffix(".SAFE")
        assert path.name == f"{stem}__IMG{props['soba:imagette_index']:03d}__HY2B_HSCAT.json"
        assert item["id"] != path.stem
    before = (result / "manifest.json").read_bytes()
    assert main(["run", "--recipe", str(recipe)]) == 2
    assert (result / "manifest.json").read_bytes() == before


def test_incomplete_local_safe_does_not_publish(local_recipe):
    recipe = local_recipe
    next(recipe.parent.glob("esa/**/measurement/*.nc")).unlink()
    assert main(["run", "--recipe", str(recipe)]) == 2
    assert not (recipe.parent / "result").exists()
