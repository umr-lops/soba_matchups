"""Tests of the SAR angles written on matched points, on a real WV footprint (10 imagettes)."""

import json
import logging
from pathlib import Path

from soba_matchups import sar_angles
from soba_matchups.sar_angles import FIELDS, add_angles, footprint_from_frames, subswath_of

FOOTPRINT = json.loads((Path(__file__).parent / "data" / "wv_footprint.json").read_text())[
    "footprint"
]
NAME = "S1D_WV_OCN__2SSV_TEST.SAFE"


def _centre(ring):
    """Return the mean (lon, lat) of a ring."""
    xs = [x for x, _ in ring[:-1]]
    ys = [y for _, y in ring[:-1]]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _points(count=6):
    """Return one point at the centre of each of the first imagettes."""
    points = []
    for ring in FOOTPRINT["coordinates"][:count]:
        lon, lat = _centre(ring[0])
        points.append({"ref_lon": lon, "ref_lat": lat})
    return points


def test_angles_uncertainty_and_subswath_are_written_in_range():
    """Points at imagette centres get finite angles, the 0.4 degree heading error, a subswath."""
    points = _points()
    assert add_angles(NAME, FOOTPRINT, points) is None
    for point in points:
        assert 15 < point["sar_incidence_angle"] < 45
        assert 15 < point["sar_elevation_angle"] < 45
        assert 0 <= point["sar_ground_heading"] <= 360
        assert point["sar_ground_heading_uncertainty"] == 0.4
        assert point["sar_wv_subswath"] in (1, 2)
    # The two chains alternate: neighbouring imagettes sit on different subswaths.
    assert {p["sar_wv_subswath"] for p in points} == {1, 2}


def test_imagette_number_and_subswath_must_agree_with_the_model():
    """A point the model places on another imagette or subswath gets null and is reported."""
    points = _points(4)
    truth = [(k + 1, p["sar_wv_subswath"]) for k, p in enumerate(_ok(points))]
    assert add_angles(NAME, FOOTPRINT, _points(4), expected=truth) is None
    wrong = list(truth)
    wrong[1] = (wrong[1][0] + 5, wrong[1][1])
    checked = _points(4)
    error = add_angles(NAME, FOOTPRINT, checked, expected=wrong)
    assert error and "disagree" in error
    assert checked[1]["sar_incidence_angle"] is None and checked[0]["sar_incidence_angle"]
    flipped = [(i, 1 if s == 2 else 2) for i, s in truth]
    other = _points(4)
    assert "disagree" in add_angles(NAME, FOOTPRINT, other, expected=flipped)
    assert all(p["sar_incidence_angle"] is None for p in other)


def _ok(points):
    """Fill the points with angles and return them."""
    assert add_angles(NAME, FOOTPRINT, points) is None
    return points


def test_point_outside_the_footprint_is_null_and_others_stay():
    """A point far from the product gets null; a point inside it keeps its values."""
    lon, lat = _centre(FOOTPRINT["coordinates"][0][0])
    points = [{"ref_lon": lon, "ref_lat": lat}, {"ref_lon": lon + 40.0, "ref_lat": lat}]
    assert add_angles(NAME, FOOTPRINT, points) is None
    assert points[0]["sar_incidence_angle"] is not None
    assert all(points[1][key] is None for key in FIELDS)


def test_short_product_is_a_warning_not_an_error(caplog):
    """A product of two imagettes (end of a datatake) is logged as a warning and left null."""
    points = [{"ref_lon": 0.0, "ref_lat": 0.0}]
    short = {"type": "MultiPolygon", "coordinates": FOOTPRINT["coordinates"][:2]}
    with caplog.at_level(logging.WARNING):
        error = add_angles(NAME, short, points)
    assert error and "imagette" in error
    assert all(points[0][key] is None for key in FIELDS)
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert add_angles(NAME, None, [{"ref_lon": 0.0, "ref_lat": 0.0}]) == (
        "no footprint for this product"
    )


def test_subswath_comes_from_the_measurement_file_name():
    """The wv1 or wv2 token of the file name gives the subswath; anything else gives None."""
    assert subswath_of("s1d-wv1-ocn-hh-20260330t161208-20260330t161211-002126-0035f4-001.nc") == 1
    assert subswath_of("s1a-wv2-ocn-vv-20250722t040232-20250722t040235-060191-077ac1-039.nc") == 2
    assert subswath_of("something.nc") is None


def test_frames_become_one_ring_each_and_dateline_pieces_stay_adjacent():
    """The ring list follows the frame order; a dateline frame gives two neighbouring rings."""
    square = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    split = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[179, 0], [180, 0], [180, 1], [179, 1], [179, 0]]],
            [[[-180, 0], [-179, 0], [-179, 1], [-180, 1], [-180, 0]]],
        ],
    }
    result = footprint_from_frames([square, split])
    assert result["type"] == "MultiPolygon" and len(result["coordinates"]) == 3
    assert result["coordinates"][0] == square["coordinates"]


def test_library_version_is_reported():
    """The manifest records which s1-geometry produced the angles."""
    assert sar_angles.library_version() is not None
