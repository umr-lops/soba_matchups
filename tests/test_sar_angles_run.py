"""CLI runs of the synthetic recipe: the angles are written, or null with a manifest entry."""

import json

from soba_matchups.cli import main


def _points(recipe):
    """Run the recipe and return the manifest and all points."""
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
    return manifest, points


def test_every_point_has_the_three_angle_fields(local_recipe):
    """The three fields exist on every point; the run goes on if the library cannot place them."""
    manifest, points = _points(local_recipe)
    assert points and manifest["schema_version"] == 22
    for point in points:
        for key in (
            "sar_incidence_angle",
            "sar_elevation_angle",
            "sar_ground_heading",
            "sar_ground_heading_uncertainty",
            "sar_wv_subswath",
        ):
            assert key in point
    info = manifest["sar_angles"]
    assert info["library"] == "s1-geometry" and info["version"]
    # The synthetic SAFE has two imagettes, too few for the model: null plus an entry per SAFE.
    assert all(p["sar_incidence_angle"] is None for p in points)
    assert info["errors"] and "imagette" in info["errors"][0]["error"]
    assert manifest["status"] == "success"
