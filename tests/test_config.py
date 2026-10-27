from pathlib import Path

import pytest


def test_toml_recipe_preserves_legacy_keys_and_resolves_relative_paths(tmp_path):
    from soba_matchups.config import load_recipe

    (tmp_path / "reference").mkdir()
    (tmp_path / "esa").mkdir()
    (tmp_path / "land.geojson").write_text("{}")
    recipe = tmp_path / "hscat.toml"
    recipe.write_text("""scat = "hscat"
met = "hy-2b"
resolution = "25km"
reference_version = "2202"
reference_root = "reference"
start_date = "2020-01-10"
stop_date = "2020-01-11"
sar_missions = ["S1A", "S1B"]
time_tolerance_minutes = 60
sar_archive_root = "esa"
output_dir = "run"
land_mask = "land.geojson"
""")
    config = load_recipe(recipe)
    assert (config.scat, config.met, config.resolution) == ("hscat", "hy-2b", "25km")
    assert config.reference_root == tmp_path / "reference"
    assert config.output_dir == tmp_path / "run"
    assert config.sar_archive_root == tmp_path / "esa"
    assert config.land_mask_source == "local file; origin not asserted"
    recipe.write_text(recipe.read_text() + 'satellite = "hy-2b"\n')
    with pytest.raises(ValueError, match="satellite"):
        load_recipe(recipe)
    recipe.write_text(recipe.read_text().replace('satellite = "hy-2b"', "").replace("60", "nan"))
    with pytest.raises(ValueError, match="tolerance"):
        load_recipe(recipe)


def test_unsupported_product_is_not_silently_accepted(tmp_path):
    from soba_matchups.config import load_recipe

    example = Path(__file__).parents[1] / "examples/hscat.toml"
    recipe = tmp_path / "other.toml"
    recipe.write_text(example.read_text().replace('scat = "hscat"', 'scat = "ascat"'))
    with pytest.raises(ValueError, match="supported"):
        load_recipe(recipe)
