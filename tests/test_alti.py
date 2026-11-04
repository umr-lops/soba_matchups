"""CCI Sea State L2P altimeter reader, file selection and end-to-end matching (synthetic)."""

from datetime import date
import json
from types import SimpleNamespace

import numpy as np
import pytest
import xarray as xr

STAMP = "20260629T120500"
FILE = f"ESACCI-SEASTATE-L2P-SWH-Jason-3-{STAMP}-fv01.nc"
SOURCE = "CCI altimeters"
FILE_SOURCE = "Jason-3 altimeter"
DAY = {"start_date": date(2026, 6, 29), "stop_date": date(2026, 6, 30)}


def alti_dataset(n=5, platform="Jason-3", start="2026-06-29T12:05:00Z", seconds0=0):
    """A small L2P-like file: points one second apart along the equator at lon 0.5, 0.6, ..."""
    base = np.datetime64("2026-06-29T12:05:00") - np.datetime64("1981-01-01T00:00:00")
    first = int(base / np.timedelta64(1, "s")) + seconds0
    ds = xr.Dataset(
        attrs={
            "platform": platform,
            "processing_level": "L2P",
            "time_coverage_start": start,
            "references": "CCI Sea State Product Specification Document (PSD), v4.0",
        }
    )
    ds["time"] = xr.DataArray(
        first + np.arange(n) * 1.0, dims=("time",), attrs={"units": "seconds since 1981-01-01"}
    )
    ds["lon"] = xr.DataArray(
        0.5 + 0.01 * np.arange(n), dims=("time",), attrs={"units": "degrees_east"}
    )
    ds["lat"] = xr.DataArray(np.full(n, 0.5), dims=("time",), attrs={"units": "degrees_north"})
    for key, value, unit in [
        ("swh", 2.0, "m"),
        ("swh_adjusted", 2.1, "m"),
        ("swh_denoised", 2.2, "m"),
        ("swh_uncertainty", 0.05, "m"),
        ("sea_ice_fraction", 0.0, "1"),
    ]:
        ds[key] = xr.DataArray(np.full(n, value), dims=("time",), attrs={"units": unit})
    ds["swh_quality_level"] = xr.DataArray(np.full(n, 3, dtype="int8"), dims=("time",))
    ds["swh_rejection_flags"] = xr.DataArray(np.zeros(n, dtype="int8"), dims=("time",))
    return ds


def write(tmp_path, ds, name=FILE):
    """Write a dataset under the file name and return the path."""
    path = tmp_path / name
    ds.to_netcdf(path)
    return path


def read(path, source=SOURCE):
    """Read a file with the altimeter adapter and the recipe's product values."""
    from soba_matchups.adapters.alti import read_reference

    return read_reference(path, expected_source=source, expected_resolution="1 Hz")


def test_reader_keeps_every_point_with_a_wave_height_and_its_flags(tmp_path):
    ds = alti_dataset()
    ds["swh_quality_level"].values[1] = 1
    ds["swh_rejection_flags"].values[1] = 5
    ds["swh"].values[2] = np.nan
    ds["swh_adjusted"].values[2] = np.nan  # swh_denoised is still there: the point stays
    ref = read(write(tmp_path, ds))
    assert ref["columns"] == 1 and ref["flat"].tolist() == [0, 1, 2, 3, 4]
    np.testing.assert_allclose(ref["lon"], 0.5 + 0.01 * np.arange(5))
    assert ref["time"][1] - ref["time"][0] == np.timedelta64(1, "s")
    values = ref["fields_at"](np.array([0, 1, 2]))
    assert values["swh_quality_level"].tolist() == [3, 1, 3]
    assert values["swh_rejection_flags"].tolist() == [0, 5, 0]
    assert np.isnan(values["swh"][2]) and values["swh_denoised"][2] == pytest.approx(2.2)
    assert (ref["source"], ref["resolution"]) == (FILE_SOURCE, "1 Hz")
    assert ref["version"] == "v4.0_fv01"
    assert ref["granule_start_day"] == date(2026, 6, 29)


def test_reader_drops_points_without_any_wave_height_and_keeps_original_rows(tmp_path):
    ds = alti_dataset()
    for key in ["swh", "swh_adjusted", "swh_denoised"]:
        ds[key].values[3] = np.nan
    ds["lon"].values[1] = np.nan
    ref = read(write(tmp_path, ds))
    assert ref["flat"].tolist() == [0, 2, 4]
    assert ref["fields_at"](np.array([2]))["swh_denoised"][0] == pytest.approx(2.2)


@pytest.mark.parametrize(
    "change,message",
    [
        ("platform", "platform or level"),
        ("level", "platform or level"),
        ("start", "disagree"),
        ("units", "units"),
        ("missing_variable", "variables/dimensions"),
        ("source", "Unsupported altimeter product"),
    ],
)
def test_reader_rejects_incompatible_files(tmp_path, change, message):
    ds = alti_dataset()
    source = SOURCE
    if change == "platform":
        ds.attrs["platform"] = "Sentinel-3 A"
    elif change == "level":
        ds.attrs["processing_level"] = "L3"
    elif change == "start":
        ds.attrs["time_coverage_start"] = "2026-06-29T12:06:00Z"
    elif change == "units":
        ds["swh"].attrs["units"] = "cm"
    elif change == "missing_variable":
        ds = ds.drop_vars("swh_quality_level")
    elif change == "source":
        source = "Jason-3 altimeter"
    with pytest.raises(ValueError, match=message):
        read(write(tmp_path, ds), source)


def test_reader_keeps_a_pass_without_any_wave_height_as_a_file_with_no_points(tmp_path):
    ds = alti_dataset()
    ds.attrs["time_coverage_end"] = "2026-06-29T12:05:04Z"
    for key in ["swh", "swh_adjusted", "swh_denoised"]:
        ds[key].values[:] = np.nan
    ref = read(write(tmp_path, ds))
    assert len(ref["flat"]) == 0 and len(ref["lon"]) == 0
    assert ref["coverage_start"] == ref["header_start"] < ref["coverage_end"]
    # Without a stated end the pass cannot be placed in time.
    del ds.attrs["time_coverage_end"]
    other = tmp_path / "b"
    other.mkdir()
    with pytest.raises(ValueError, match="coverage"):
        read(write(other, ds))


def test_reader_rejects_a_name_that_is_not_a_cci_file(tmp_path):
    with pytest.raises(ValueError, match="file name"):
        read(write(tmp_path, alti_dataset(), "other.nc"))


def test_reader_rejects_an_implausible_time_span(tmp_path):
    ds = alti_dataset()
    times = ds["time"].values.copy()
    times[-1] += 3 * 86400
    ds["time"] = xr.DataArray(times, dims=("time",), attrs={"units": "seconds since 1981-01-01"})
    with pytest.raises(ValueError, match="24 hours"):
        read(write(tmp_path, ds))


def tree(tmp_path, files, layout="l2p"):
    """Create empty files at {relative folder: [names]} under a reference root; return a recipe."""
    root = tmp_path / "cci"
    for folder, names in files.items():
        (root / "data/satellite/altimeter" / layout / folder).mkdir(parents=True, exist_ok=True)
        for name in names:
            (root / "data/satellite/altimeter" / layout / folder / name).touch()
    return SimpleNamespace(reference_root=root, platform="cci", **DAY)


def test_find_files_selects_the_mission_and_the_days(tmp_path):
    from soba_matchups.adapters.alti import find_files

    recipe = tree(
        tmp_path,
        {
            "jason-3/2026/180": [
                "ESACCI-SEASTATE-L2P-SWH-Jason-3-20260629T010000-fv01.nc",
                "ESACCI-SEASTATE-L2P-SWH-Jason-3-20260629T120500-fv01.nc",
                "ESACCI-SEASTATE-L2P-SWH-Sentinel-3_A-20260629T030000-fv01.nc",  # other mission
                "notes.txt",
            ],
            "jason-3/2026/179": ["ESACCI-SEASTATE-L2P-SWH-Jason-3-20260628T230000-fv01.nc"],
            "jason-3/2026/181": ["ESACCI-SEASTATE-L2P-SWH-Jason-3-20260630T000100-fv01.nc"],
        },
    )
    assert [p.name for p in find_files(recipe)] == [
        "ESACCI-SEASTATE-L2P-SWH-Jason-3-20260629T010000-fv01.nc",
        "ESACCI-SEASTATE-L2P-SWH-Jason-3-20260629T120500-fv01.nc",
    ]
    # The folder for Sentinel-3 A is named differently from the recipe's platform value.
    other = tree(
        tmp_path / "b",
        {"sentinel-3_a/2026/180": ["ESACCI-SEASTATE-L2P-SWH-Sentinel-3_A-20260629T030000-fv01.nc"]},
    )
    assert [p.name for p in find_files(other)] == [
        "ESACCI-SEASTATE-L2P-SWH-Sentinel-3_A-20260629T030000-fv01.nc"
    ]


def test_find_files_reports_a_missing_mission_folder_and_empty_days(tmp_path):
    from soba_matchups.adapters.alti import find_files

    with pytest.raises(ValueError, match="No altimeter files"):
        find_files(tree(tmp_path, {"saral/2026/180": []}))
    with pytest.raises(ValueError, match="No altimeter files"):
        find_files(tree(tmp_path / "second", {"jason-3/2026/100": []}))


def test_find_files_reads_the_v5_layout_and_the_sentinel_6_folder_names(tmp_path):
    from soba_matchups.adapters.alti import find_files

    name = "ESACCI-SEASTATE-L2P-SWH-Jason-3-20260629T010000-fv01.nc"
    v5 = tree(tmp_path / "v5", {"jason-3/2026/180": [name]}, layout="l2p-swh")
    assert [p.name for p in find_files(v5)] == [name]
    six = "ESACCI-SEASTATE-L2P-SWH-Sentinel-6_A-20260629T010000-fv01.nc"
    for folder, layout in (("sentinel-6", "l2p"), ("sentinel-6_a", "l2p-swh")):
        recipe = tree(tmp_path / folder, {f"{folder}/2026/180": [six]}, layout=layout)
        assert [p.name for p in find_files(recipe)] == [six]


def test_find_files_refuses_two_layouts_at_once(tmp_path):
    from soba_matchups.adapters.alti import find_files

    name = "ESACCI-SEASTATE-L2P-SWH-Jason-3-20260629T010000-fv01.nc"
    tree(tmp_path, {"jason-3/2026/180": [name]}, layout="l2p")
    both = tree(tmp_path, {"jason-3/2026/180": [name]}, layout="l2p-swh")
    with pytest.raises(ValueError, match="Several folders"):
        find_files(both)


def test_two_altimeter_products_are_registered_and_cci_missions_have_unique_names():
    from soba_matchups.adapters.alti import MISSIONS
    from soba_matchups.config import REFERENCE_PRODUCTS

    assert REFERENCE_PRODUCTS[("alti", "cci", "l2p")] == (
        "CCI altimeters",
        "1 Hz",
        "ESACCI_SEASTATE_L2P",
    )
    assert REFERENCE_PRODUCTS[("alti", "cmems", "l3")] == (
        "CMEMS altimeters",
        "1 Hz",
        "CMEMS_WAV_L3_ALTI_VAVH",
    )
    assert {k[1] for k in REFERENCE_PRODUCTS if k[0] == "alti"} == {"cci", "cmems"}
    assert len({m[1] for m in MISSIONS.values()}) == len(MISSIONS)
    assert len({m[2] for m in MISSIONS.values()}) == len(MISSIONS)


def test_find_files_reads_all_missions_of_the_root_in_one_call(tmp_path):
    from soba_matchups.adapters.alti import find_files

    names = {
        "jason-3/2026/180": ["ESACCI-SEASTATE-L2P-SWH-Jason-3-20260629T010000-fv01.nc"],
        "sentinel-3_a/2026/180": ["ESACCI-SEASTATE-L2P-SWH-Sentinel-3_A-20260629T030000-fv01.nc"],
        "saral/2026/181": ["ESACCI-SEASTATE-L2P-SWH-SARAL-20260630T030000-fv01.nc"],  # next day
    }
    recipe = tree(tmp_path, names)
    assert sorted(p.name.split("-SWH-")[1][:8] for p in find_files(recipe)) == [
        "Jason-3-",
        "Sentinel",
    ]


def test_alti_end_to_end_matches_points_by_time_and_footprint(local_recipe):
    """The synthetic SAFE has imagette 1 on (0..1 deg, 0..1 deg) at 12:00:00-12:00:03."""
    from soba_matchups.cli import main

    root = local_recipe.parent
    folder = root / "cci" / "data/satellite/altimeter/l2p/jason-3/2026/180"
    folder.mkdir(parents=True)
    # Five points at 12:05:00..12:05:04, 5 min after the imagette, at lon 0.5..0.54 (inside).
    ds = alti_dataset()
    ds["lon"].values[4] = 1.5  # outside the footprint
    ds["swh_quality_level"].values[1] = 1
    ds.to_netcdf(folder / FILE)
    recipe = root / "alti.toml"
    recipe.write_text(
        local_recipe.read_text()
        .replace('instrument = "hscat"', 'instrument = "alti"')
        .replace('platform = "hy-2b"', 'platform = "cci"')
        .replace('resolution = "25km"', 'resolution = "l2p"')
        .replace('reference_root = "reference"', 'reference_root = "cci"')
        .replace("time_tolerance_minutes = 1", "time_tolerance_minutes = 30")
        .replace('output_dir = "result"', 'output_dir = "alti_result"')
    )
    assert main(["run", "--recipe", str(recipe)]) == 0
    result = root / "alti_result"
    manifest = json.loads((result / "manifest.json").read_text())
    assert manifest["reference_product"] == "ESACCI_SEASTATE_L2P_V4"
    assert manifest["reference_versions"] == ["v4.0_fv01"]
    assert manifest["rules"]["policy"] == "alti_local_ocn_imagette_v2"
    assert "swh_denoised" in manifest["units"] and "wind_speed" not in manifest["units"]
    (item_path,) = sorted((result / "items").glob("*__IMG001__ESACCI_SEASTATE_L2P_V4.json"))
    props = json.loads(item_path.read_text())["properties"]
    assert props["sarmatchup:other_type"] == "altimeter"
    assert props["sarmatchup:other_instrument"] == "Jason-3 altimeter"
    assert props["sarmatchup:searching_window_hours"] == 0.5
    points = props["sarmatchup:matchups"][0]["points"]
    assert sorted(p["ref_row_0based"] for p in points) == [0, 1, 2, 3]
    assert all(p["ref_cell_0based"] == 0 for p in points)
    flagged = next(p for p in points if p["ref_row_0based"] == 1)
    assert flagged["swh_quality_level"] == 1 and isinstance(flagged["swh_quality_level"], int)
    assert flagged["swh_denoised"] == pytest.approx(2.2)
    assert "wind_speed" not in flagged and "swh_karin" not in flagged


def test_one_recipe_matches_two_missions_and_names_both_in_the_item(local_recipe):
    from soba_matchups.cli import main

    root = local_recipe.parent
    stamp = {"Jason-3": ("jason-3", "Jason-3"), "Sentinel-3_A": ("sentinel-3_a", "Sentinel-3 A")}
    for token, (folder, platform) in stamp.items():
        directory = root / "cci" / f"data/satellite/altimeter/l2p/{folder}/2026/180"
        directory.mkdir(parents=True)
        name = f"ESACCI-SEASTATE-L2P-SWH-{token}-20260629T120500-fv01.nc"
        alti_dataset(platform=platform).to_netcdf(directory / name)
    recipe = root / "alti2.toml"
    recipe.write_text(
        local_recipe.read_text()
        .replace('instrument = "hscat"', 'instrument = "alti"')
        .replace('platform = "hy-2b"', 'platform = "cci"')
        .replace('resolution = "25km"', 'resolution = "l2p"')
        .replace('reference_root = "reference"', 'reference_root = "cci"')
        .replace("time_tolerance_minutes = 1", "time_tolerance_minutes = 30")
        .replace('output_dir = "result"', 'output_dir = "alti2_result"')
    )
    assert main(["run", "--recipe", str(recipe)]) == 0
    result = root / "alti2_result"
    (item,) = sorted((result / "items").glob("*__IMG001__*.json"))
    props = json.loads(item.read_text())["properties"]
    assert props["sarmatchup:other_instrument"] == "Jason-3 altimeter + Sentinel-3 A altimeter"
    sources = sorted(m["reference_source"] for m in props["sarmatchup:matchups"])
    assert sources == ["Jason-3 altimeter", "Sentinel-3 A altimeter"]


def cci(version):
    """A minimal CCI reference record, as the reader returns it."""
    return {"version": version, "release_in_label": True}


def test_product_label_carries_the_release_of_the_files():
    from soba_matchups.adapters.alti import product_suffix

    assert product_suffix([cci("v5.0_fv01"), cci("v5.0_fv02")]) == "_V5"
    assert product_suffix([cci("v4.0_fv01")]) == "_V4"


@pytest.mark.parametrize(
    "versions",
    [["v4.0_fv01", "v5.0_fv01"], ["vunknown_fv01"], ["v5.0_fv01", "vunknown_fv01"]],
)
def test_product_label_refuses_mixed_or_unknown_releases(versions):
    from soba_matchups.adapters.alti import product_suffix

    with pytest.raises(ValueError, match="not a single one"):
        product_suffix([cci(v) for v in versions])


def test_cfosat_files_are_found_and_read_as_their_own_mission(tmp_path):
    from soba_matchups.adapters.alti import find_files, read_reference

    name = "ESACCI-SEASTATE-L2P-SWH-CFOSAT-20260629T120500-fv01.nc"
    recipe = tree(tmp_path / "ref", {"cfosat/2026/180": []}, layout="l2p-swh")
    directory = recipe.reference_root / "data/satellite/altimeter/l2p-swh/cfosat/2026/180"
    path = write(directory, alti_dataset(platform="CFOSAT"), name)
    assert find_files(recipe) == [path]
    ref = read_reference(path, expected_source="CCI altimeters", expected_resolution="1 Hz")
    assert ref["source"] == "CFOSAT altimeter" and ref["version"] == "v4.0_fv01"
    # A file named for CFOSAT whose attributes say another platform is refused.
    other = write(tmp_path, alti_dataset(platform="Jason-3"), name)
    with pytest.raises(ValueError, match="platform or level"):
        read_reference(other, expected_source="CCI altimeters", expected_resolution="1 Hz")


KM = 1 / 111.19  # degrees of latitude per km


def imagette_record(
    half_km=10.0, lat=0.0, start="2026-06-29T12:00:00Z", end="2026-06-29T12:00:03Z"
):
    """A SAR imagette at longitude 0: a square footprint of half_km around its centre."""
    h = half_km * KM
    ring = [[-h, lat - h], [h, lat - h], [h, lat + h], [-h, lat + h], [-h, lat - h]]
    return {
        "ContentDate": {"Start": start, "End": end},
        "GeoFootprint": {"type": "Polygon", "coordinates": [ring]},
        "Centre": [0.0, lat],
    }


def track(north_km, seconds, east_km=0.0):
    """Reference arrays as the reader returns them: points north_km north of the centre."""
    base = np.datetime64("2026-06-29T12:00:00", "ns")
    return {
        "lon": np.array(east_km, dtype="float64").reshape(-1) * KM + np.zeros(len(north_km)),
        "lat": np.array(north_km, dtype="float64") * KM,
        "time": base + (np.array(seconds) * 1e9).astype("int64").astype("timedelta64[ns]"),
    }


def test_fallback_candidates_are_the_points_in_radius_and_window_with_their_time_gaps():
    from soba_matchups.adapters.alti import fallback_imagette

    # Footprint half width 10 km; points 15, 20 and 25 km north, then one beyond 30 km, then one
    # near but 3 h late. Gaps are seconds from the imagette midpoint (12:00:01.5).
    ref = track([15, 20, 25, 31, 15], [900, 600, 1200, 600, 3 * 3600])
    candidates, gaps = fallback_imagette(imagette_record(), ref, 30)
    assert candidates.tolist() == [0, 1, 2]
    assert gaps.tolist() == pytest.approx([898.5, 598.5, 1198.5])


def test_the_fallback_radius_and_window_are_strict():
    from soba_matchups.adapters.alti import fallback_imagette

    for north_km, seconds, expected in [
        (29.0, 600, [0]),  # inside 30 km, inside the window
        (31.0, 600, []),  # outside 30 km
        (15.0, 3 * 3600, []),  # near, but 3 h after the imagette
    ]:
        ref = track([north_km], [seconds])
        assert fallback_imagette(imagette_record(), ref, 30)[0].tolist() == expected


def test_the_safe_pass_keeps_only_points_near_one_of_its_imagettes():
    from soba_matchups.adapters.alti import match_safe

    far = imagette_record()
    far["Centre"] = [10.0, 0.0]
    product = {
        "ContentDate": {"Start": "2026-06-29T12:00:00Z", "End": "2026-06-29T12:10:03Z"},
        "Imagettes": [imagette_record(), far],
    }
    ref = track([25, 25, 25, 25], [60, 60, 60, 4 * 3600])
    ref["lon"] = np.array([0.0, 10.0, 5.0, 0.0])
    # Row 0 is 25 km from the first centre, row 1 25 km from the second, row 2 is 550 km from
    # both, and row 3 is 4 hours late.
    assert match_safe(product, ref, 30).tolist() == [0, 1]


def run_alti_synthetic(local_recipe, points, name):
    """Run the tool on the synthetic SAFE; points are (folder, token, platform, dataset)."""
    from soba_matchups.cli import main

    root = local_recipe.parent
    for mission_folder, mission_token, mission_platform, ds in points:
        folder = root / "cci" / f"data/satellite/altimeter/l2p/{mission_folder}/2026/180"
        folder.mkdir(parents=True, exist_ok=True)
        ds.to_netcdf(folder / f"ESACCI-SEASTATE-L2P-SWH-{mission_token}-20260629T120500-fv01.nc")
    recipe = root / f"{name}.toml"
    recipe.write_text(
        local_recipe.read_text()
        .replace('instrument = "hscat"', 'instrument = "alti"')
        .replace('platform = "hy-2b"', 'platform = "cci"')
        .replace('resolution = "25km"', 'resolution = "l2p"')
        .replace('reference_root = "reference"', 'reference_root = "cci"')
        .replace("time_tolerance_minutes = 1", "time_tolerance_minutes = 30")
        .replace('output_dir = "result"', f'output_dir = "{name}_result"')
    )
    assert main(["run", "--recipe", str(recipe)]) == 0
    return root / f"{name}_result"


def moved(ds, north_km, platform):
    """The synthetic file moved so that its points sit north_km north of the imagette centre."""
    ds["lon"].values[:] = 0.5
    ds["lat"].values[:] = 0.5 + north_km * KM
    ds.attrs["platform"] = platform
    return ds


def test_points_beyond_footprint_and_radius_give_no_item(local_recipe):
    # Imagette 1 is a 1 degree square centred on (0.5, 0.5); its edge is 55 km from the centre.
    # Points 60 km north are outside the footprint and beyond 30 km: no match.
    far = run_alti_synthetic(
        local_recipe,
        [("jason-3", "Jason-3", "Jason-3", moved(alti_dataset(), 60, "Jason-3"))],
        "far",
    )
    assert not list((far / "items").glob("*__IMG001__*.json"))


def test_points_inside_the_footprint_are_all_kept_for_every_mission(local_recipe):
    # Two missions with five points each, 5 km from the centre: all ten are inside the footprint.
    # The fallback itself is covered above; a footprint this wide cannot leave a gap before 30 km.
    result = run_alti_synthetic(
        local_recipe,
        [
            ("jason-3", "Jason-3", "Jason-3", moved(alti_dataset(), 5, "Jason-3")),
            (
                "sentinel-3_a",
                "Sentinel-3_A",
                "Sentinel-3 A",
                moved(alti_dataset(), 5, "Sentinel-3 A"),
            ),
        ],
        "inside",
    )
    (item,) = sorted((result / "items").glob("*__IMG001__*.json"))
    props = json.loads(item.read_text())["properties"]
    assert props["sarmatchup:n_coloc_points"] == 10
    assert "closest in time" in json.loads((result / "manifest.json").read_text())["rules"]["space"]
