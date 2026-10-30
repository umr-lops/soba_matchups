"""SWOT KaRIn reader, file selection and end-to-end matching on synthetic files."""

from datetime import date
import json
from types import SimpleNamespace

import numpy as np
import pytest
import xarray as xr

PASS = "SWOT_L2_LR_SSH_WindWave_044_108_20260629T115500_20260629T125000_PID0_01.nc"
DAY = {"start_date": date(2026, 6, 29), "stop_date": date(2026, 6, 30)}


def swot_dataset(lines=4, pixels=3, crid="PID0", cycle=44, pass_number=108, seconds0=0):
    """A small SWOT-like file: scaled integer coordinates and values, one time per line."""
    base = np.datetime64("2026-06-29T12:00:00") - np.datetime64("2000-01-01T00:00:00")
    start = int(base / np.timedelta64(1, "s")) + seconds0
    shape = (lines, pixels)
    lon = np.tile(np.array([0.5, 1.5, 359.5])[:pixels], (lines, 1))
    lat = np.tile(np.array([0.5, 0.5, 0.5])[:pixels], (lines, 1))
    ds = xr.Dataset(
        attrs={"cycle_number": cycle, "pass_number": pass_number, "crid": crid},
    )
    ds["time"] = xr.DataArray(
        start + np.arange(lines) * 1.0,
        dims=("num_lines",),
        attrs={"units": "seconds since 2000-01-01 00:00:00.0"},
    )
    for name, values, units in [
        ("longitude", lon, "degrees_east"),
        ("latitude", lat, "degrees_north"),
    ]:
        ds[name] = xr.DataArray(
            np.round(values * 1e6).astype("int32"),
            dims=("num_lines", "num_pixels"),
            attrs={"units": units, "scale_factor": 1e-6, "_FillValue": 2147483647},
        )
    for name, unit, value in [
        ("swh_karin", "m", 2.5),
        ("swh_karin_uncert", "m", 0.1),
        ("wind_speed_karin", "m/s", 7.0),
        ("wind_speed_karin_2", "m/s", 7.5),
    ]:
        ds[name] = xr.DataArray(
            np.full(shape, round(value * 1000), dtype="uint16"),
            dims=("num_lines", "num_pixels"),
            attrs={"units": unit, "scale_factor": 0.001, "_FillValue": 65535},
        )
    for name, value in [
        ("swh_karin_qual", 0),
        ("wind_speed_karin_qual", 0),
        ("wind_speed_karin_2_qual", 0),
        ("dynamic_ice_flag", 0),
        ("rain_flag", 0),
    ]:
        ds[name] = xr.DataArray(
            np.full(shape, value, dtype="uint32"),
            dims=("num_lines", "num_pixels"),
            attrs={"units": ""},
        )
    return ds


def write(tmp_path, ds, name=PASS):
    """Write a dataset under the file name and return the path."""
    path = tmp_path / name
    ds.to_netcdf(path)
    return path


def read(path):
    """Read a file with the SWOT adapter and the recipe's product values."""
    from soba_matchups.adapters.swot import read_reference

    return read_reference(path, expected_source="SWOT KaRIn", expected_resolution="2 km")


def test_reader_decodes_scaled_values_and_keeps_every_cell(tmp_path):
    ref = read(write(tmp_path, swot_dataset()))
    assert ref["columns"] == 3
    assert ref["flat"].tolist() == list(range(12))
    # Longitudes stay in the file's 0..360 range; matching folds them later.
    np.testing.assert_allclose(ref["lon"][:3], [0.5, 1.5, 359.5])
    np.testing.assert_allclose(ref["lat"], 0.5)
    # One time per line, repeated across its pixels.
    assert ref["time"][0] == ref["time"][2] != ref["time"][3]
    values = ref["fields_at"](np.array([0, 5]))
    np.testing.assert_allclose(values["swh_karin"], 2.5)
    np.testing.assert_allclose(values["swh_karin_uncert"], 0.1)
    np.testing.assert_allclose(values["wind_speed_karin"], 7.0)
    np.testing.assert_allclose(values["wind_speed_karin_2"], 7.5)
    assert values["swh_karin_qual"].tolist() == [0, 0]
    assert ref["version"] == "PID0_01"
    assert (ref["source"], ref["resolution"]) == ("SWOT KaRIn", "2 km")
    assert ref["granule_start_day"] == date(2026, 6, 29)


def test_reader_drops_cells_without_a_measurement_and_keeps_original_indices(tmp_path):
    ds = swot_dataset()
    for name in ["swh_karin", "wind_speed_karin", "wind_speed_karin_2"]:
        ds[name].values[1, 1] = 65535  # fill value: masked on reading
    ds["swh_karin"].values[2, 0] = 65535  # only the wave height is missing here
    ref = read(write(tmp_path, ds))
    assert 4 not in ref["flat"].tolist()
    assert 6 in ref["flat"].tolist()
    assert ref["flat"].tolist() == [0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11]
    values = ref["fields_at"](np.array([ref["flat"].tolist().index(6)]))
    assert np.isnan(values["swh_karin"][0])
    assert values["wind_speed_karin"][0] == pytest.approx(7.0)


def test_reader_keeps_flags_without_filtering(tmp_path):
    ds = swot_dataset()
    ds["dynamic_ice_flag"].values[0, 0] = 2
    ds["rain_flag"].values[0, 1] = 3
    ds["swh_karin_qual"].values[0, 2] = 1 << 5
    values = read(write(tmp_path, ds))["fields_at"](np.array([0, 1, 2]))
    assert values["dynamic_ice_flag"].tolist() == [2, 0, 0]
    assert values["rain_flag"].tolist() == [0, 3, 0]
    assert values["swh_karin_qual"].tolist() == [0, 0, 32]


@pytest.mark.parametrize(
    "change,message",
    [
        ("cycle", "cycle_number"),
        ("pass", "pass_number"),
        ("crid", "crid"),
        ("units", "units"),
        ("missing_variable", "variables/dimensions"),
        ("wrong_time_dims", "variables/dimensions"),
        ("all_fill", "No readable"),
    ],
)
def test_reader_rejects_incompatible_files(tmp_path, change, message):
    ds = swot_dataset()
    if change == "cycle":
        ds.attrs["cycle_number"] = 45
    elif change == "pass":
        ds.attrs["pass_number"] = 109
    elif change == "crid":
        ds.attrs["crid"] = "PIC2"
    elif change == "units":
        ds["swh_karin"].attrs["units"] = "cm"
    elif change == "missing_variable":
        ds = ds.drop_vars("rain_flag")
    elif change == "wrong_time_dims":
        ds["time"] = xr.DataArray(
            np.zeros((4, 3)),
            dims=("num_lines", "num_pixels"),
            attrs={"units": "seconds since 2000-01-01 00:00:00.0"},
        )
    elif change == "all_fill":
        for name in ["swh_karin", "wind_speed_karin", "wind_speed_karin_2"]:
            ds[name].values[:] = 65535
    with pytest.raises(ValueError, match=message):
        read(write(tmp_path, ds))


def test_reader_rejects_a_name_that_is_not_a_swot_file(tmp_path):
    with pytest.raises(ValueError, match="file name"):
        read(write(tmp_path, swot_dataset(), "other.nc"))


def test_reader_rejects_an_implausible_time_span(tmp_path):
    ds = swot_dataset()
    ds["time"].values[-1] += 3 * 86400
    with pytest.raises(ValueError, match="24 hours"):
        read(write(tmp_path, ds))


def tree(tmp_path, files):
    """Create empty files at {folder: [names]} under a reference root; return a recipe stand-in."""
    root = tmp_path / "swot"
    for folder, names in files.items():
        (root / folder).mkdir(parents=True, exist_ok=True)
        for name in names:
            (root / folder / name).touch()
    return SimpleNamespace(reference_root=root, **DAY)


def name(cycle, pass_number, start, crid="PID0", version="01"):
    """A SWOT file name starting at start (YYYYMMDDTHHMMSS) and lasting 51 minutes."""
    end = start[:9] + "235959"
    return (
        f"SWOT_L2_LR_SSH_WindWave_{cycle:03d}_{pass_number:03d}_{start}_{end}_{crid}_{version}.nc"
    )


def test_find_files_keeps_the_highest_version_of_each_pass(tmp_path):
    from soba_matchups.adapters.swot import find_files

    recipe = tree(
        tmp_path,
        {
            "PID0": [
                name(44, 108, "20260629T010000", version="01"),
                name(44, 108, "20260629T010000", version="04"),
                name(44, 109, "20260629T060000"),
            ]
        },
    )
    assert [p.name for p in find_files(recipe)] == [
        name(44, 108, "20260629T010000", version="04"),
        name(44, 109, "20260629T060000"),
    ]


def test_find_files_selects_the_days_and_ignores_folders_and_other_files(tmp_path):
    from soba_matchups.adapters.swot import find_files

    recipe = tree(
        tmp_path,
        {
            "PID0": [
                name(44, 108, "20260629T010000"),
                name(44, 100, "20260628T230000"),  # starts the day before
                name(44, 120, "20260630T000000"),  # starts the exclusive stop day
                "notes.txt",
                "SWOT_L2_LR_SSH_WindWave_bad_name.nc",
            ],
            "PGD0": [name(1, 5, "20260629T020000", crid="PGD0")],
            "PIC2": [name(44, 111, "20260629T070000", crid="PIC2")],  # not a D0 release
            "PID0/OLD": [name(44, 108, "20260629T010000")],  # sub-folder: ignored
            "PID0/PGC0": [name(44, 112, "20260629T080000", crid="PGC0")],
        },
    )
    names = [p.name for p in find_files(recipe)]
    assert names == sorted(
        [name(44, 108, "20260629T010000"), name(1, 5, "20260629T020000", crid="PGD0")]
    )


def test_find_files_rejects_a_pass_present_in_two_releases(tmp_path):
    from soba_matchups.adapters.swot import find_files

    recipe = tree(
        tmp_path,
        {
            "PID0": [name(44, 108, "20260629T010000")],
            "PGD0": [name(44, 108, "20260629T010000", crid="PGD0")],
        },
    )
    with pytest.raises(ValueError, match="both PGD0 and PID0|both PID0 and PGD0"):
        find_files(recipe)


def test_find_files_reports_missing_folders_and_empty_days(tmp_path):
    from soba_matchups.adapters.swot import find_files

    with pytest.raises(ValueError, match="No PGD0 or PID0 folder"):
        find_files(tree(tmp_path, {"PIC2": []}))
    with pytest.raises(ValueError, match="No SWOT files starting"):
        find_files(tree(tmp_path / "second", {"PID0": [name(44, 108, "20260101T010000")]}))


def test_swot_label_is_registered_in_the_product_table():
    from soba_matchups.config import REFERENCE_PRODUCTS

    assert REFERENCE_PRODUCTS[("karin", "swot", "2km")][2] == "PODAAC_SWOT_KARIN_L2_WINDWAVE_D0"


def test_swot_end_to_end_matches_cells_by_line_time_and_footprint(local_recipe):
    """The synthetic SAFE has imagette 1 on (0..1 deg, 0..1 deg) at 12:00:00-12:00:03."""
    from soba_matchups.cli import main

    root = local_recipe.parent
    folder = root / "swot" / "PID0"
    folder.mkdir(parents=True)
    # Three lines at 12:00:00, 12:00:01 and 12:30:00; the pixels sit at lon 0.5, 1.5 and 359.5.
    ds = swot_dataset(lines=3)
    ds["time"].values[2] = ds["time"].values[0] + 1800
    ds["rain_flag"].values[0, 0] = 2
    ds["swh_karin_qual"].values[1, 0] = 4
    ds.to_netcdf(folder / PASS)
    swot_recipe = root / "swot.toml"
    swot_recipe.write_text(
        local_recipe.read_text()
        .replace('instrument = "hscat"', 'instrument = "karin"')
        .replace('platform = "hy-2b"', 'platform = "swot"')
        .replace('resolution = "25km"', 'resolution = "2km"')
        .replace('reference_root = "reference"', 'reference_root = "swot"')
        .replace("time_tolerance_minutes = 1", "time_tolerance_minutes = 60")
        .replace('output_dir = "result"', 'output_dir = "swot_result"')
    )
    assert main(["run", "--recipe", str(swot_recipe)]) == 0
    result = root / "swot_result"
    manifest = json.loads((result / "manifest.json").read_text())
    assert manifest["reference_product"] == "PODAAC_SWOT_KARIN_L2_WINDWAVE_D0"
    assert manifest["reference_versions"] == ["PID0_01"]
    assert manifest["rules"]["policy"] == "swot_local_ocn_imagette_v1"
    assert "swh_karin" in manifest["units"] and "wind_direction_to" not in manifest["units"]
    items = sorted((result / "items").glob("*.json"))
    assert [i.name.endswith("__IMG001__PODAAC_SWOT_KARIN_L2_WINDWAVE_D0.json") for i in items] == [
        True
    ] * len(items)
    item = json.loads(items[0].read_text())
    props = item["properties"]
    assert props["sarmatchup:other_type"] == "altimeter"
    assert props["sarmatchup:other_instrument"] == "SWOT KaRIn"
    points = props["sarmatchup:matchups"][0]["points"]
    # Pixels 0 (lon 0.5) is inside imagette 1 on lines 0 and 1 (within 60 min of 12:00:00-12:00:03);
    # line 2 is 30 min later and still within the window; pixel 1 (lon 1.5) is outside.
    assert sorted((p["ref_row_0based"], p["ref_cell_0based"]) for p in points) == [
        (0, 0),
        (1, 0),
        (2, 0),
    ]
    first = min(points, key=lambda p: p["ref_row_0based"])
    assert first["rain_flag"] == 2 and first["swh_karin"] == pytest.approx(2.5)
    assert "wind_direction_to" not in first and "wind_speed" not in first
    flagged = next(p for p in points if p["ref_row_0based"] == 1)
    assert flagged["swh_karin_qual"] == 4 and isinstance(flagged["swh_karin_qual"], int)
