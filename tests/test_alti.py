"""CCI Sea State L2P altimeter reader, file selection and end-to-end matching (synthetic)."""

from datetime import date
import json
from types import SimpleNamespace

import numpy as np
import pytest
import xarray as xr

STAMP = "20260629T120500"
FILE = f"ESACCI-SEASTATE-L2P-SWH-Jason-3-{STAMP}-fv01.nc"
SOURCE = "Jason-3 altimeter"
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
    assert (ref["source"], ref["resolution"]) == (SOURCE, "1 Hz")
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
        ("source", "requested mission"),
        ("all_missing", "No readable"),
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
        source = "SARAL altimeter"
    elif change == "all_missing":
        for key in ["swh", "swh_adjusted", "swh_denoised"]:
            ds[key].values[:] = np.nan
    with pytest.raises(ValueError, match=message):
        read(write(tmp_path, ds), source)


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


def tree(tmp_path, files, platform="jason-3"):
    """Create empty files at {relative folder: [names]} under a reference root; return a recipe."""
    root = tmp_path / "cci"
    for folder, names in files.items():
        (root / "data/satellite/altimeter/l2p" / folder).mkdir(parents=True, exist_ok=True)
        for name in names:
            (root / "data/satellite/altimeter/l2p" / folder / name).touch()
    return SimpleNamespace(reference_root=root, platform=platform, **DAY)


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
        platform="sentinel-3a",
    )
    assert [p.name for p in find_files(other)] == [
        "ESACCI-SEASTATE-L2P-SWH-Sentinel-3_A-20260629T030000-fv01.nc"
    ]


def test_find_files_reports_a_missing_mission_folder_and_empty_days(tmp_path):
    from soba_matchups.adapters.alti import find_files

    with pytest.raises(ValueError, match="No jason-3 folder|No mission folder"):
        find_files(tree(tmp_path, {"saral/2026/180": []}))
    with pytest.raises(ValueError, match="No Jason-3 files starting"):
        find_files(tree(tmp_path / "second", {"jason-3/2026/100": []}))


def test_products_are_registered_for_every_public_mission():
    from soba_matchups.adapters.alti import MISSIONS
    from soba_matchups.config import REFERENCE_PRODUCTS

    for platform, (_, _, attribute) in MISSIONS.items():
        source, sampling, label = REFERENCE_PRODUCTS[("alti", platform, "l2p")]
        assert source == f"{attribute} altimeter" and sampling == "1 Hz"
        assert label.startswith("ESACCI_SEASTATE_L2P_")
    assert len({REFERENCE_PRODUCTS[("alti", p, "l2p")][2] for p in MISSIONS}) == len(MISSIONS)


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
        .replace('platform = "hy-2b"', 'platform = "jason-3"')
        .replace('resolution = "25km"', 'resolution = "l2p"')
        .replace('reference_root = "reference"', 'reference_root = "cci"')
        .replace("time_tolerance_minutes = 1", "time_tolerance_minutes = 30")
        .replace('output_dir = "result"', 'output_dir = "alti_result"')
    )
    assert main(["run", "--recipe", str(recipe)]) == 0
    result = root / "alti_result"
    manifest = json.loads((result / "manifest.json").read_text())
    assert manifest["reference_product"] == "ESACCI_SEASTATE_L2P_JASON3"
    assert manifest["reference_versions"] == ["v4.0_fv01"]
    assert manifest["rules"]["policy"] == "alti_cci_local_ocn_imagette_v1"
    assert "swh_denoised" in manifest["units"] and "wind_speed" not in manifest["units"]
    (item_path,) = sorted((result / "items").glob("*__IMG001__ESACCI_SEASTATE_L2P_JASON3.json"))
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
