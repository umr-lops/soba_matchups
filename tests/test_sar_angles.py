"""Tests of the SAR angles written on matched points, on a real WV footprint (10 imagettes)."""

import json
from pathlib import Path

import pytest

from soba_matchups import sar_angles
from soba_matchups.sar_angles import FIELDS, add_angles, footprint_from_frames

FOOTPRINT = json.loads((Path(__file__).parent / "data" / "wv_footprint.json").read_text())[
    "footprint"
]


def _centre(ring):
    """Return the mean (lon, lat) of a ring."""
    xs = [x for x, _ in ring[:-1]]
    ys = [y for _, y in ring[:-1]]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def test_angles_are_written_on_every_point_in_range():
    """Points at imagette centres get finite angles in the expected ranges."""
    points = []
    for ring in FOOTPRINT["coordinates"][:6]:
        lon, lat = _centre(ring[0])
        points.append({"ref_lon": lon, "ref_lat": lat})
    assert add_angles(FOOTPRINT, points) is None
    for point in points:
        assert 15 < point["sar_incidence_angle"] < 45
        assert 15 < point["sar_elevation_angle"] < 45
        assert 0 <= point["sar_ground_heading"] <= 360


def test_point_outside_the_footprint_is_null_and_others_stay():
    """A point far from the product gets null; a point inside it keeps its values."""
    lon, lat = _centre(FOOTPRINT["coordinates"][0][0])
    points = [{"ref_lon": lon, "ref_lat": lat}, {"ref_lon": lon + 40.0, "ref_lat": lat}]
    assert add_angles(FOOTPRINT, points) is None
    assert points[0]["sar_incidence_angle"] is not None
    assert all(points[1][key] is None for key in FIELDS)


def test_unusable_footprint_gives_null_and_a_message():
    """A footprint the library rejects leaves null on every point and returns the reason."""
    points = [{"ref_lon": 0.0, "ref_lat": 0.0}]
    error = add_angles(
        {"type": "MultiPolygon", "coordinates": FOOTPRINT["coordinates"][:2]}, points
    )
    assert error and "imagette" in error
    assert all(points[0][key] is None for key in FIELDS)
    assert add_angles(None, [{"ref_lon": 0.0, "ref_lat": 0.0}]) == "no footprint for this product"


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
    pytest.importorskip("s1_geometry")
