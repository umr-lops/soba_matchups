"""Output-profile semantics and collision-safe publication."""

import copy

import pytest

from soba_matchups.output import imagette_item, item_filename, publish


def item():
    product = {"Id": "parent", "Name": "S1A_WV_OCN__2SSV_EXAMPLE.SAFE"}
    imagette = {
        "Id": "imagette",
        "Name": "synthetic.nc",
        "index": 1,
        "href": "file:///synthetic.nc",
        "ContentDate": {"Start": "2026-06-29T12:00:00Z", "End": "2026-06-29T12:00:04Z"},
        "GeoFootprint": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
        "mapping_policy": "synthetic unit fixture",
    }
    groups = [
        {
            "reference_file": "synthetic-reference.nc",
            "reference_path": "/synthetic-reference.nc",
            "reference_sha256": "synthetic",
            "reference_source": "HY-2B HSCAT",
            "reference_type": "scatterometer",
            "reference_version": "2202",
            "reference_resolution": "25.0 km",
            "points": [
                {"record_id": str(i), "ref_time_utc": t, "ref_lon": 0.5, "ref_lat": 0.5}
                for i, t in enumerate(["2026-06-29T12:00:01Z", "2026-06-29T12:00:03Z"])
            ],
        }
    ]
    return imagette_item(
        product, imagette, {"polarization": "VV", "producttype": "OCN"}, groups, 60
    )


def test_signed_time_differences_and_summary_preserve_observations():
    value = item()
    props = value["properties"]
    points = props["sarmatchup:matchups"][0]["points"]
    assert [p["delta_diff_time_minutes"] for p in points] == [-1 / 60, 1 / 60]
    assert props["sarmatchup:delta_diff_time_minutes_min"] == -1 / 60
    assert props["sarmatchup:delta_diff_time_minutes_max"] == 1 / 60
    assert props["sarmatchup:n_coloc_points"] == 2


def test_filename_ends_with_the_product_label():
    name = item_filename(item(), "KNMI_HSCAT_HY2B_25KM")
    assert name == "S1A_WV_OCN__2SSV_EXAMPLE__IMG001__KNMI_HSCAT_HY2B_25KM"


def test_filename_collision_does_not_publish_partial_output(tmp_path):
    first = item()
    second = copy.deepcopy(first)
    second["id"] = "different_identity"
    for point in second["properties"]["sarmatchup:matchups"][0]["points"]:
        point["record_id"] += "different"
    output = tmp_path / "result"
    with pytest.raises(ValueError, match="duplicate STAC filename"):
        publish(
            output,
            [first, second],
            [],
            [],
            {
                "stac_items": 2,
                "matchup_imagette_cell_rows": 4,
                "reference_product": "KNMI_HSCAT_HY2B_25KM",
            },
        )
    assert not output.exists()
