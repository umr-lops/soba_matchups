import gzip
import shutil

import numpy as np
import pytest
import xarray as xr


# Header-derived variants from all 14 surveyed files. Arrays are synthetic, not observations.
KNMI_CASES = [
    ("HY-2B HSCAT", "25.0 km", "2202"),
    ("HY-2B HSCAT", "25.0 km", "4008"),
    ("HY-2C HSCAT", "25.0 km", "2202"),
    ("HY-2C HSCAT", "25.0 km", "4006"),
    ("HY-2D HSCAT", "25.0 km", "4002"),
    ("HY-2D HSCAT", "25.0 km", "4006"),
    ("MetOp-B ASCAT", "12.5 km", "2101"),
    ("MetOp-B ASCAT", "12.5 km", "3301"),
    ("MetOp-B ASCAT", "25.0 km", "3202"),
    ("MetOp-B ASCAT", "25.0 km", "3301"),
    ("MetOp-B ASCAT", "5.7 km", "3201"),
    ("MetOp-B ASCAT", "5.7 km", "3307"),
    ("MetOp-C ASCAT", "12.5 km", "3203"),
    ("MetOp-C ASCAT", "12.5 km", "3301"),
]


def knmi_dataset(case):
    source, resolution, version = case
    seconds = int(
        (np.datetime64("2026-06-29T12:00:01") - np.datetime64("1990-01-01"))
        / np.timedelta64(1, "s")
    )
    raw = {
        "lon": [[50000, 35950000, 50000], [50000, 50000, 50000]],
        "lat": [[50000] * 3] * 2,
        "time": [[seconds] * 3] * 2,
        "wind_speed": [[500, -32767, 600], [700, 800, 900]],
        "wind_dir": [[900] * 3] * 2,
        "wvc_quality_flag": [[2048, 0, 4194304], [0, -2147483647, 0]],
        "ice_prob": [[500] * 3] * 2,
    }
    ds = xr.Dataset(
        attrs={
            "source": source,
            "pixel_size_on_horizontal": resolution,
            "software_identification_wind": int(version),
            "start_date": "2026-06-29",
            "start_time": "12:00:00",
            "stop_date": "2026-06-29",
            "stop_time": "12:30:00",
            "synthetic_fixture": "true; real header encodings, manufactured arrays",
            "comment": (
                "Orbit period and inclination are constant values. "
                "All wind directions in oceanographic convention (0 deg. flowing North)"
            ),
        }
    )
    scales = {"lon": 1e-5, "lat": 1e-5, "wind_speed": 0.01, "wind_dir": 0.1, "ice_prob": 0.001}
    units = {
        "lon": "degrees_east",
        "lat": "degrees_north",
        "time": "seconds since 1990-01-01 00:00:00",
        "wind_speed": "m s-1",
        "wind_dir": "degree",
        "ice_prob": "1",
    }
    for key, values in raw.items():
        short = key in {"wind_speed", "wind_dir", "ice_prob"}
        fill = -32767 if short else -2147483647
        attrs = {"_FillValue": fill, "missing_value": fill}
        if key in units:
            attrs["units"] = units[key]
        if key in scales or (key == "time" and version not in {"2101", "3201"}):
            attrs.update(scale_factor=scales.get(key, 1.0), add_offset=0.0)
        if key == "wind_dir" and version != "2101":
            attrs["standard_name"] = "wind_to_direction"
        ds[key] = xr.DataArray(
            np.array(values, dtype="int16" if short else "int32"),
            dims=("NUMROWS", "NUMCELLS"),
            attrs=attrs,
        )
    return ds


@pytest.mark.parametrize("case", KNMI_CASES)
@pytest.mark.parametrize("compressed", [False, True])
def test_knmi_reader_decodes_surveyed_products(tmp_path, case, compressed):
    from soba_matchups.adapters.scat import read_reference

    ds = knmi_dataset(case)
    path = tmp_path / "synthetic_ovw_l2.nc"
    ds.to_netcdf(path)
    if compressed:
        zipped = path.with_suffix(".nc.gz")
        with path.open("rb") as src, gzip.open(zipped, "wb") as dst:
            shutil.copyfileobj(src, dst)
        path = zipped
    ref = read_reference(path, expected_source=case[0], expected_resolution=case[1])
    assert ref["flat"].tolist() == [0, 2, 3, 4, 5]
    assert ref["columns"] == 3
    np.testing.assert_allclose(ref["wind_speed"], [5, 6, 7, 8, 9])
    np.testing.assert_allclose(ref["wind_dir"], 90)
    np.testing.assert_allclose(ref["ice_prob"], 0.5)
    assert ref["wvc_quality_flag"][0] == 2048
    assert ref["wvc_quality_flag"][1] == 4194304
    assert np.isnan(ref["wvc_quality_flag"][3])
    assert ref["time"][0] == np.datetime64("2026-06-29T12:00:01")
    assert ref["source"] == case[0]
    assert ref["version"] == case[2]
    assert ref["resolution"] == case[1]


@pytest.mark.parametrize(
    "change,message",
    [
        ("source", "source"),
        ("resolution", "resolution"),
        ("unknown_direction", "direction"),
        ("contradictory_direction", "direction"),
        ("longitude_units", "units"),
        ("latitude_units", "units"),
        ("ice_units", "units"),
        ("missing_variable", "variables/dimensions"),
        ("wrong_dimensions", "variables/dimensions"),
        ("header_multiyear", "span"),
        ("cell_multiyear", "span"),
        ("no_times", "times"),
        ("numeric_time", "CF-decoded"),
    ],
)
def test_knmi_reader_rejects_incompatible_files(tmp_path, change, message):
    from soba_matchups.adapters.scat import read_reference

    ds = knmi_dataset(KNMI_CASES[0])
    if change == "source":
        ds.attrs["source"] = "HY-2D HSCAT"
    elif change == "resolution":
        ds.attrs["pixel_size_on_horizontal"] = "12.5 km"
    elif change == "unknown_direction":
        del ds.wind_dir.attrs["standard_name"]
        del ds.attrs["comment"]
    elif change == "contradictory_direction":
        ds.wind_dir.attrs["standard_name"] = "wind_from_direction"
        ds.attrs["comment"] = (
            "All wind directions in oceanographic convention (0 deg. flowing North)"
        )
    elif change.endswith("_units"):
        key = {"longitude_units": "lon", "latitude_units": "lat", "ice_units": "ice_prob"}[change]
        ds[key].attrs["units"] = "unknown"
    elif change == "missing_variable":
        ds = ds.drop_vars("ice_prob")
    elif change == "wrong_dimensions":
        ds["wind_speed"] = ds.wind_speed.transpose()
    elif change == "header_multiyear":
        ds.attrs["start_date"] = "2000-01-01"
        ds.attrs["stop_date"] = "2018-07-31"
    elif change == "cell_multiyear":
        ds.time.values[1, 2] = 0
    elif change == "no_times":
        ds.time.values[:] = -2147483647
    elif change == "numeric_time":
        del ds.time.attrs["units"]
    path = tmp_path / "incompatible.nc"
    ds.to_netcdf(path)
    with pytest.raises(ValueError, match=message):
        read_reference(path)


def test_safe_matching_preserves_components_and_strict_boundaries():
    from soba_matchups.adapters.scat import match_cells

    product = {
        "ContentDate": {"Start": "2020-01-10T12:00:00Z", "End": "2020-01-10T12:30:00Z"},
        "GeoFootprint": {
            "type": "MultiPolygon",
            "coordinates": [
                [[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]],
                [[(2, 0), (3, 0), (3, 1), (2, 1), (2, 0)]],
                [[(179.8, 0), (180, 0), (180, 1), (179.8, 1), (179.8, 0)]],
                [[(-180, 0), (-179.8, 0), (-179.8, 1), (-180, 1), (-180, 0)]],
            ],
        },
    }
    ref = {
        "lon": np.array([0.5, 2.5, 0, 0.5, 0.5, 179.9, -179.9]),
        "lat": np.full(7, 0.5),
        "wind_speed": np.array([5, 5, 5, 5, np.nan, 5, 5]),
        "wind_dir": np.full(7, 90.0),
        "time": np.array(["2020-01-10T12:20:00"] * 7, dtype="datetime64[ns]"),
    }
    ref["time"][3] = np.datetime64("2020-01-10T13:30:00")
    assert match_cells(product, ref, 60).tolist() == [0, 1, 5, 6]


def test_dateline_seam_is_interior_not_outer_boundary():
    from soba_matchups.adapters.scat import match_cells
    from shapely.geometry import MultiPolygon, box, mapping

    product = {
        "ContentDate": {"Start": "2026-06-29T12:00:00Z", "End": "2026-06-29T12:00:03Z"},
        "GeoFootprint": mapping(MultiPolygon([box(179.8, 10, 180, 11), box(-180, 10, -179.8, 11)])),
    }
    ref = {
        "lon": np.array([179.9, -179.9, 180, -180, 179.8, 0]),
        "lat": np.full(6, 10.5),
        "time": np.full(6, np.datetime64("2026-06-29T12:00:01", "ns")),
        "wind_speed": np.full(6, 5.0),
        "wind_dir": np.full(6, 90.0),
    }
    assert match_cells(product, ref, 1).tolist() == [0, 1, 2, 3]


def test_hscat_reader_keeps_original_indices_and_checks_product(tmp_path):
    from soba_matchups.adapters.scat import read_reference

    data = xr.Dataset(
        {
            key: (("NUMROWS", "NUMCELLS"), value)
            for key, value in {
                "lon": [[0.5, 0.5], [2.5, 2.5]],
                "lat": [[0.5, 0.5], [0.5, 0.5]],
                "time": np.full((2, 2), np.datetime64("2020-01-10T12:20:00", "ns")),
                "wind_speed": [[5.0, np.nan], [5.0, 5.0]],
                "wind_dir": [[90.0, 90.0], [90.0, 90.0]],
                "wvc_quality_flag": np.zeros((2, 2)),
                "ice_prob": np.zeros((2, 2)),
            }.items()
        },
        attrs={
            "source": "HY-2B HSCAT",
            "software_identification_wind": "2202",
            "pixel_size_on_horizontal": "25.0 km",
            "start_date": "2020-01-10",
            "start_time": "12:00:00",
            "stop_date": "2020-01-10",
            "stop_time": "12:30:00",
        },
    )
    data.wind_speed.attrs["units"] = "m s-1"
    data.wind_dir.attrs["units"] = "degree"
    data.wind_dir.attrs["standard_name"] = "wind_to_direction"
    data.lon.attrs["units"] = "degrees_east"
    data.lat.attrs["units"] = "degrees_north"
    data.ice_prob.attrs["units"] = "1"
    path = tmp_path / "control.nc"
    data["time"].values[1, 1] = np.datetime64("2020-01-10T12:31:00", "ns")
    data.to_netcdf(path)
    compressed = tmp_path / "control.nc.gz"
    with path.open("rb") as src, gzip.open(compressed, "wb") as dst:
        shutil.copyfileobj(src, dst)
    ref = read_reference(compressed)
    assert ref["flat"].tolist() == [0, 2, 3]
    assert ref["columns"] == 2
    assert ref["coverage_end"].minute == 31
    # A different processing version is accepted and recorded, never rejected.
    assert read_reference(path)["version"] == "2202"


def test_any_processing_version_is_accepted_and_recorded(tmp_path):
    from soba_matchups.adapters.scat import read_reference

    for version in (4008, 9999):
        ds = knmi_dataset(KNMI_CASES[0])
        ds.attrs["software_identification_wind"] = version
        path = tmp_path / f"v{version}.nc"
        ds.to_netcdf(path)
        assert read_reference(path)["version"] == str(version)


def test_missing_version_attribute_is_recorded_as_unknown(tmp_path):
    from soba_matchups.adapters.scat import read_reference

    ds = knmi_dataset(KNMI_CASES[0])
    del ds.attrs["software_identification_wind"]
    path = tmp_path / "no_version.nc"
    ds.to_netcdf(path)
    assert read_reference(path)["version"] == "unknown"
