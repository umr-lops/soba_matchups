from pathlib import Path

import pytest


def test_toml_recipe_preserves_legacy_keys_and_resolves_relative_paths(tmp_path):
    from soba_matchups.config import load_recipe

    (tmp_path / "reference").mkdir()
    (tmp_path / "esa").mkdir()
    recipe = tmp_path / "hscat.toml"
    recipe.write_text("""scat = "hscat"
met = "hy-2b"
resolution = "25km"
reference_root = "reference"
start_date = "2020-01-10"
stop_date = "2020-01-11"
sar_missions = ["S1A", "S1B"]
time_tolerance_minutes = 60
sar_archive_root = "esa"
output_dir = "run"
""")
    config = load_recipe(recipe)
    assert (config.scat, config.met, config.resolution) == ("hscat", "hy-2b", "25km")
    assert config.reference_root == tmp_path / "reference"
    assert config.output_dir == tmp_path / "run"
    assert config.sar_archive_root == tmp_path / "esa"
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


PRODUCTS = [
    ("ascat", "metop-b", "25km", "3202"),
    ("ascat", "metop-b", "12.5km", "3301"),
    ("ascat", "metop-b", "5.7km", "3307"),
    ("ascat", "metop-c", "12.5km", "3203"),
    ("hscat", "hy-2b", "25km", "4008"),
    ("hscat", "hy-2c", "25km", "2202"),
    ("hscat", "hy-2d", "25km", "4006"),
]


@pytest.mark.parametrize("sensor,platform,resolution,version", PRODUCTS)
def test_available_knmi_products(local_recipe, sensor, platform, resolution, version):
    from soba_matchups.config import load_recipe

    text = local_recipe.read_text()
    for old, new in [
        ("hscat", sensor),
        ("hy-2b", platform),
        ("25km", resolution),
        ("2202", version),
    ]:
        text = text.replace(f'"{old}"', f'"{new}"')
    local_recipe.write_text(text)
    config = load_recipe(local_recipe)
    assert (config.scat, config.met, config.resolution) == (sensor, platform, resolution)


@pytest.mark.parametrize(
    "old,new",
    [
        ('met = "hy-2b"', 'met = "metop-d"'),
        ('resolution = "25km"', 'resolution = "12.5km"'),
        ('resolution = "25km"', 'resolution = "25km"\nreference_version = "2202"'),
    ],
)
def test_invalid_knmi_selection(local_recipe, old, new):
    from soba_matchups.config import load_recipe

    local_recipe.write_text(local_recipe.read_text().replace(old, new))
    with pytest.raises(ValueError, match="supported|reference_version"):
        load_recipe(local_recipe)
