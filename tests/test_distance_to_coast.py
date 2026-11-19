"""Tests of the distance-to-coast lookup on a small synthetic grid."""

import logging

import netCDF4
import numpy as np

from soba_matchups.distance_to_coast import DistanceToCoast


def _grid(path, lat):
    """Write a 5 x 9 grid (lon -4..4) whose value is 10 * row + column."""
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("lat", len(lat))
        ds.createDimension("lon", 9)
        ds.createVariable("lat", "f4", ("lat",))[:] = lat
        ds.createVariable("lon", "f4", ("lon",))[:] = np.arange(-4, 5)
        values = 10 * np.arange(len(lat))[:, None] + np.arange(9)[None, :]
        ds.createVariable("distance_to_coast", "f4", ("lat", "lon"))[:] = values


def test_nearest_cell_and_unknown_points(tmp_path):
    """A point takes its nearest cell, whichever way latitude runs; NaN gives None."""
    for name, lat in [("up.nc", [0, 1, 2, 3, 4]), ("down.nc", [4, 3, 2, 1, 0])]:
        _grid(tmp_path / name, lat)
        coast = DistanceToCoast(tmp_path / name)
        assert coast.ok and coast.summary()["status"] == "ok"
        # Lat 2.2 rounds to lat 2 (row 2 in both files) and lon -1.4 to column 3: 10 * 2 + 3.
        assert coast.at([-1.4, np.nan], [2.2, 1.0]) == [23.0, None]


def test_missing_file_is_flagged_and_does_not_stop(tmp_path, caplog):
    """A missing file logs one error and answers None for every point."""
    with caplog.at_level(logging.ERROR):
        coast = DistanceToCoast(tmp_path / "absent.nc")
    assert not coast.ok and coast.summary()["status"] == "unavailable"
    assert "absent.nc" in caplog.text
    assert coast.at([0.0, 1.0], [0.0, 1.0]) == [None, None]


def test_not_configured_is_silent(caplog):
    """With no file in the recipe the lookup answers None without an error."""
    with caplog.at_level(logging.ERROR):
        coast = DistanceToCoast(None)
    assert coast.summary()["status"] == "not_configured" and not caplog.text
    assert coast.at([0.0], [0.0]) == [None]


def _run(recipe, extra):
    """Run the CLI on the synthetic recipe with an extra recipe line; return points and manifest."""
    import json

    from soba_matchups.cli import main

    text = recipe.read_text()
    recipe.write_text(text + extra)
    assert main(["run", "--recipe", str(recipe)]) == 0
    result = recipe.parent / "result"
    manifest = json.loads((result / "manifest.json").read_text())
    items = [json.loads(p.read_text()) for p in (result / "items").glob("*.json")]
    points = [
        p
        for item in items
        for group in item["properties"]["sarmatchup:matchups"]
        for p in group["points"]
    ]
    return points, manifest


def test_run_writes_distance_on_every_point(local_recipe):
    """Every matched point carries sar_distance_to_coast, and the manifest names the grid."""
    grid = local_recipe.parent / "coast.nc"
    with netCDF4.Dataset(grid, "w") as ds:
        ds.createDimension("lat", 181)
        ds.createDimension("lon", 361)
        ds.createVariable("lat", "f4", ("lat",))[:] = np.arange(-90, 91)
        ds.createVariable("lon", "f4", ("lon",))[:] = np.arange(-180, 181)
        # The value is the latitude plus 100, so each point has a known answer.
        ds.createVariable("distance_to_coast", "f4", ("lat", "lon"))[:] = np.repeat(
            np.arange(-90, 91)[:, None] + 100.0, 361, axis=1
        )
    points, manifest = _run(local_recipe, 'distance_to_coast_file = "coast.nc"\n')
    assert points and manifest["schema_version"] == 12
    assert manifest["distance_to_coast"]["status"] == "ok"
    for point in points:
        assert point["sar_distance_to_coast"] == round(point["ref_lat"]) + 100.0


def test_run_goes_on_when_the_grid_is_missing(local_recipe):
    """A missing grid leaves null on every point and the run still publishes."""
    points, manifest = _run(local_recipe, 'distance_to_coast_file = "absent.nc"\n')
    assert points and all(p["sar_distance_to_coast"] is None for p in points)
    assert manifest["status"] == "success"
    assert manifest["distance_to_coast"]["status"] == "unavailable"
