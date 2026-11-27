"""Buoy reference: NDBC (NCEI) and TAO station-years, CMEMS in situ files, selection and matching.

The synthetic SAR fixture (conftest.local_recipe) has imagette 1 on (0..1 deg, 0..1 deg) at
2026-06-29 12:00:00-12:00:03 and imagette 2 across the dateline (lat 10..11, lon 179.8..-179.8)
at 12:10:00-12:10:03.
"""

from datetime import date
import json
from types import SimpleNamespace

import netCDF4
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

DAY = "2026-06-29"
CMEMS_DATASET = (
    "INSITU_GLO_PHYBGCWAV_DISCRETE_MYNRT_013_030/cmems_obs-ins_glo_phybgcwav_mynrt_na_irr"
)
INDEX_COLUMNS = (
    "product_id,file_name,geospatial_lat_min,geospatial_lat_max,geospatial_lon_min,"
    "geospatial_lon_max,time_coverage_start,time_coverage_end,institution,date_update,data_mode,"
    "parameters"
)


def stamps(*clocks, day=DAY):
    """UTC timestamps of the day at HH:MM."""
    return [np.datetime64(f"{day}T{c}:00", "us") for c in clocks]


def utc_column(clocks, day=DAY):
    """An Arrow UTC timestamp column, as get_ncei_data and get_tao_data write it."""
    values = np.array(stamps(*clocks, day=day), dtype="datetime64[us]")
    return pa.array(values).cast(pa.timestamp("us", tz="UTC"))


def ncei_file(folder, station, clocks, lon=0.5, lat=0.5, day=DAY):
    """One get_ncei_data wind station-year with a record at each clock."""
    n = len(clocks)
    times = utc_column(clocks, day=day)
    table = pa.table(
        {
            "time": times,
            "station_id": [station] * n,
            "lat": [lat] * n,
            "lon": [lon] * n,
            "position_source": ["gps"] * n,
            "gps_qc": pa.array([1] * n, pa.int8()),
            "nominal_lat": [lat + 0.01] * n,
            "nominal_lon": [lon] * n,
            "anemometer_height": [3.8] * n,
            "wind_speed": [5.0 + i for i in range(n)],
            "wind_speed_qc": pa.array([1] * n, pa.int8()),
            "wind_speed_qc_detail": ["0"] * n,
            "wind_speed_released": [True] * n,
            "wind_direction": [200.0] * n,
            "wind_direction_qc": pa.array([1] * n, pa.int8()),
            "wind_direction_qc_detail": ["0"] * n,
            "wind_direction_released": [i != 0 for i in range(n)],
        }
    )
    meta = {"station": station, "product": "wind", "stage": "get_ncei_data.concat"}
    meta["version"] = "0.1.0"
    meta["columns"] = {"wind_speed": {"units": "m s-1"}, "wind_direction": {"units": "degree"}}
    table = table.replace_schema_metadata({"ndbc_meta": json.dumps(meta)})
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{station}_{day[:4]}.parquet"
    pq.write_table(table, path)
    return path


def tao_file(folder, site, wmo, clocks, lon, lat):
    """One get_tao_data site-year with a record at each clock."""
    n = len(clocks)
    times = utc_column(clocks)
    columns = {
        "time": times,
        "lat": [lat] * n,
        "lon": [lon] * n,
        "position_source": ["gps"] * n,
        "nominal_lat": [lat] * n,
        "nominal_lon": [lon] * n,
        "anemometer_height": [4.0] * n,
        "wind_speed": [7.0] * n,
        "wind_speed_qc": pa.array([1] * n, pa.int8()),
        "wind_speed_mode": ["R"] * n,
        "wind_speed_withdrawn": [False] * n,
        "wind_speed_bias_suspect": [True] * n,
        "wind_direction": [100.0] * n,
        "wind_direction_qc": pa.array([2] * n, pa.int8()),
        "wind_direction_mode": ["R"] * n,
        "wind_direction_withdrawn": [False] * n,
    }
    meta = {"station": site, "wmo": wmo, "stage": "get_tao_data.concat", "version": "0.1.0"}
    table = pa.table(columns).replace_schema_metadata({"tao_meta": json.dumps(meta)})
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{site}_{DAY[:4]}.parquet"
    pq.write_table(table, path)
    return path


def cmems_file(
    root, relative, clocks, lon, lat, *, day=DAY, towards=None, moving=False, source=None
):
    """One CMEMS 013_030 file (TIME x DEPTH) with WSPD and WDIR, or WTODIR when towards is set.

    The wind sits on the second depth level; the first holds nothing, as at a buoy whose upper
    sensor failed. A moving platform has LATITUDE/LONGITUDE on TIME, a moored one PRECISE_*.
    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    base = np.datetime64("1950-01-01T00:00:00", "s")
    days = [(t - base) / np.timedelta64(1, "D") for t in stamps(*clocks, day=day)]
    n = len(days)
    if source is None:
        source = "drifting surface float" if moving else "moored surface buoy"
    with netCDF4.Dataset(path, "w") as ds:
        ds.source = source
        ds.data_mode = "R"
        ds.format_version = "2.0"
        ds.createDimension("TIME", n)
        ds.createDimension("DEPTH", 2)
        time = ds.createVariable("TIME", "f8", ("TIME",))
        time.units = "days since 1950-01-01T00:00:00Z"
        time[:] = days
        deph = ds.createVariable("DEPH", "f4", ("DEPTH",))
        deph[:] = [-10.0, -4.0]
        speed = ds.createVariable("WSPD", "f4", ("TIME", "DEPTH"), fill_value=np.float32(99999))
        speed.units = "m s-1"
        speed[:] = np.ma.masked_array(
            np.column_stack([np.zeros(n), 6.0 + np.arange(n)]), mask=[[True, False]] * n
        )
        qc = ds.createVariable("WSPD_QC", "i1", ("TIME", "DEPTH"), fill_value=np.int8(-127))
        qc[:] = np.ma.masked_array(np.ones((n, 2)), mask=[[True, False]] * n)
        name = "WTODIR" if towards is not None else "WDIR"
        direction = ds.createVariable(name, "f4", ("TIME", "DEPTH"), fill_value=np.float32(99999))
        direction.units = "degree"
        value = towards if towards is not None else 30.0
        direction[:] = np.ma.masked_array(
            np.full((n, 2), value), mask=[[True, False]] * n
        )
        if moving:
            for key, value in [("LATITUDE", lat), ("LONGITUDE", lon)]:
                ds.createVariable(key, "f4", ("TIME",))[:] = np.full(n, value)
            ds.createVariable("POSITION_QC", "i1", ("TIME",))[:] = np.ones(n)
        else:
            ds.createVariable("LATITUDE", "f4", ())[:] = lat + 1.0  # the latest position
            ds.createVariable("LONGITUDE", "f4", ())[:] = lon + 1.0
            for key, value in [("PRECISE_LATITUDE", lat), ("PRECISE_LONGITUDE", lon)]:
                ds.createVariable(key, "f4", ("TIME",))[:] = np.full(n, value)
    return path


def index(root, channel, rows):
    """Write index_<channel>.txt; rows are (relative path, start, end, data_mode)."""
    lines = ["# Title : in-situ files catalog", f"# {INDEX_COLUMNS}"]
    for relative, start, end, mode in rows:
        parameters = "DEPH WSPD WDIR"
        lines.append(
            f"COP,{CMEMS_DATASET}_202311/{relative},0,1,0,1,{start},{end},Inst,2026-07-01T00:00:00Z,"
            f"{mode},{parameters}"
        )
    (root / f"index_{channel}.txt").write_text("\n".join(lines) + "\n")


@pytest.fixture
def archive(tmp_path):
    """Three sources sharing platforms: what is read, what is left out and why."""
    insitu = tmp_path / "insitu"
    ncei = insitu / "ncei/ncei_concat/wind"
    tao = insitu / "tao/tao_concat"
    cmems = tmp_path / "cmems"
    cmems.mkdir()
    # NDBC buoy inside imagette 1: 11:30 and 12:31 sit just outside the strict 30 min window.
    ncei_file(ncei, "41999", ["11:30", "11:40", "12:00", "12:30", "12:31"])
    # A record on the next day is outside the requested days.
    ncei_file(ncei, "41998", ["00:10"], day="2026-06-30")
    # A land station inside imagette 1 is never read.
    ncei_file(ncei, "bura1", ["12:00"])
    # get_ncei_data's index: 41998 starts after the requested day, so it is not even opened.
    spans = {"41999": ("11:30", "12:31", DAY), "41998": ("00:10", "00:10", "2026-06-30")}
    spans["bura1"] = ("12:00", "12:00", DAY)
    pq.write_table(
        pa.table(
            {
                "station": list(spans),
                "year": [2026] * len(spans),
                "t0": pa.concat_arrays([utc_column([a], day=d) for a, _, d in spans.values()]),
                "t1": pa.concat_arrays([utc_column([b], day=d) for _, b, d in spans.values()]),
            }
        ),
        ncei / "index.parquet",
    )
    # TAO mooring inside the dateline imagette 2.
    tao_file(tao, "T10N180W", "52999", ["12:10"], lon=179.9, lat=10.5)
    files = {
        # Already read from NCEI and TAO: left out.
        "history/MO/GL_TS_MO_41999.nc": (["12:00"], 0.5, 0.5, {}),
        "history/MO/GL_TS_MO_5200999.nc": (["12:10"], 179.9, 10.5, {}),
        # A drifter inside imagette 1 that gives the direction the wind goes to.
        "history/DB/GL_TS_DB_3100001.nc": (["12:05"], 0.6, 0.6, {"towards": 90.0, "moving": True}),
        # A mooring whose history file ends at 12:00; its monthly file holds 11:50 (already in
        # history) and 12:10 (after it).
        "history/MO/GL_TS_MO_6300001.nc": (["11:55"], 0.4, 0.4, {}),
        "monthly/MO/202606/GL_TS_MO_6300001_202606.nc": (["11:50", "12:10"], 0.4, 0.4, {}),
        # The same mooring distributed by a second region: read once, from GL.
        "history/MO/NO_TS_MO_6300001.nc": (["11:55"], 0.4, 0.4, {}),
        # Not buoys, though inside imagette 1: an oil platform filed as a mooring, and a ship.
        "history/MO/AR_TS_MO_Draugen.nc": (["12:00"], 0.7, 0.7, {"source": "subsurface mooring"}),
        "history/FB/BO_TS_FB_Oden_2026.nc": (["12:00"], 0.3, 0.3, {"source": "research vessel"}),
    }
    for relative, (clocks, lon, lat, options) in files.items():
        cmems_file(cmems, relative, clocks, lon, lat, **options)
    t = f"{DAY}T"
    index(
        cmems,
        "history",
        [
            ("history/MO/GL_TS_MO_41999.nc", f"{t}00:00:00Z", f"{t}23:00:00Z", "R"),
            ("history/MO/GL_TS_MO_5200999.nc", f"{t}00:00:00Z", f"{t}23:00:00Z", "R"),
            ("history/DB/GL_TS_DB_3100001.nc", f"{t}12:05:00Z", f"{t}12:05:00Z", "R"),
            ("history/MO/GL_TS_MO_6300001.nc", "2020-01-01T00:00:00Z", f"{t}12:00:00Z", "D"),
            ("history/MO/NO_TS_MO_6300001.nc", "2020-01-01T00:00:00Z", f"{t}12:00:00Z", "D"),
            ("history/MO/GL_TS_MO_6399999.nc", f"{t}00:00:00Z", f"{t}23:00:00Z", "R"),  # absent
            ("history/MO/AR_TS_MO_Draugen.nc", f"{t}00:00:00Z", f"{t}23:00:00Z", "R"),
            ("history/FB/BO_TS_FB_Oden_2026.nc", f"{t}00:00:00Z", f"{t}23:00:00Z", "R"),
        ],
    )
    index(
        cmems,
        "monthly",
        [
            (
                "monthly/MO/202606/GL_TS_MO_6300001_202606.nc",
                f"{DAY[:8]}01T00:00:00Z",
                f"{t}23:00:00Z",
                "R",
            )
        ],
    )
    index(cmems, "latest", [])
    return SimpleNamespace(insitu=insitu, cmems=cmems, ncei=ncei, tao=tao)


def recipe_for(archive, start="2026-06-29", stop="2026-06-30"):
    """A recipe-like object with the fields the adapter reads."""
    from soba_matchups.config import Recipe

    return Recipe(
        path=archive.insitu / "buoy.toml",
        reference_root=archive.insitu,
        cmems_insitu_root=archive.cmems,
        start_date=date.fromisoformat(start),
        stop_date=date.fromisoformat(stop),
        sar_missions=("S1A",),
        time_tolerance_minutes=30,
        output_dir=archive.insitu.parent / "out",
        instrument="buoy",
        platform="ndbc-tao-cmems",
        resolution="point",
        sar_archive_root=archive.insitu,
    )


def test_selection_keeps_buoys_only_and_lists_everything_left_out(archive):
    from soba_matchups.adapters import buoy

    recipe = recipe_for(archive)
    names = sorted(p.name for p in buoy.find_files(recipe))
    assert names == [
        "41999_2026.parquet",
        "GL_TS_DB_3100001.nc",
        "GL_TS_MO_6300001.nc",
        "GL_TS_MO_6300001_202606.nc",
        "T10N180W_2026.parquet",
    ]
    selection = buoy.selection(recipe)
    assert selection["files"] == {"ncei": 1, "tao": 1, "cmems": 3}
    assert selection["ncei_land_stations_left_out"] == ["bura1"]
    assert selection["cmems_platforms_left_out"] == {
        "41999": "NDBC station in NCEI",
        "5200999": "TAO site T10N180W (WMO number)",
    }
    assert selection["cmems_region_duplicates_left_out"] == {"6300001": "NO, read from GL"}
    assert selection["cmems_not_buoys_left_out"] == {"Draugen": "source 'subsurface mooring'"}
    assert selection["cmems_types_left_out"] == {"FB": 1}
    assert selection["cmems_index_files_missing"] == ["history/MO/GL_TS_MO_6399999.nc"]


def test_ship_history_files_split_by_year_are_one_platform_with_their_monthly_files():
    from soba_matchups.adapters.buoy import _CMEMS_NAME

    assert _CMEMS_NAME.fullmatch("BO_TS_FB_Oden_2021.nc")["code"] == "Oden"
    assert _CMEMS_NAME.fullmatch("BO_TS_FB_Oden_202101.nc")["code"] == "Oden"
    assert _CMEMS_NAME.fullmatch("AR_TS_MO_Draugen_20261101.nc")["code"] == "Draugen"
    assert _CMEMS_NAME.fullmatch("GL_TS_DB_3100001.nc")["code"] == "3100001"


def test_ncei_records_keep_flags_text_and_marks_and_their_file_rows(archive):
    from soba_matchups.adapters import buoy

    recipe = recipe_for(archive)
    read = buoy.reader(recipe)
    ref = read(archive.ncei / "41999_2026.parquet")
    assert ref["source"] == "NDBC buoy" and ref["version"] == "get_ncei_data 0.1.0"
    assert ref["group_fields"] == {"reference_platform": "41999", "reference_platform_type": "MO"}
    assert list(ref["flat"]) == [0, 1, 2, 3, 4]
    values = ref["fields_at"](np.array([0, 1]))
    assert list(values["wind_speed"]) == [5.0, 6.0]
    assert list(values["wind_direction_released"]) == [False, True]
    assert list(values["wind_speed_qc_detail"]) == ["0", "0"]
    assert values["wind_speed_mode"][0] is None
    assert values["position_offset_km"][0] == pytest.approx(1.11, abs=0.01)


def test_records_outside_the_requested_days_are_not_read(archive):
    from soba_matchups.adapters import SkippableReference, buoy

    recipe = recipe_for(archive)
    # 41998 has a record on 06-30 only: the index keeps it out of the plan, and read directly it
    # has nothing in the days.
    with pytest.raises(SkippableReference, match="No wind record"):
        buoy.read_file(
            archive.ncei / "41998_2026.parquet",
            {"source": "ncei", "platform": "41998"},
            *buoy._window(recipe),
        )


def test_cmems_direction_is_from_and_height_is_the_level_used(archive):
    from soba_matchups.adapters import buoy

    recipe = recipe_for(archive)
    read = buoy.reader(recipe)
    drifter = read(archive.cmems / "history/DB/GL_TS_DB_3100001.nc")
    values = drifter["fields_at"](np.array([0]))
    # WTODIR 90 (towards east) is a wind from the west.
    assert values["wind_direction_from"][0] == pytest.approx(270.0)
    assert values["wind_speed"][0] == pytest.approx(6.0)
    assert values["wind_speed_qc"][0] == 1
    # The wind is on the second level, DEPH -4 m: 4 m above the surface.
    assert values["anemometer_height"][0] == pytest.approx(4.0)
    assert values["position_source"][0] == "record" and values["position_qc"][0] == 1
    assert drifter["source"] == "CMEMS in situ"
    assert drifter["group_fields"]["reference_platform_type"] == "DB"
    mooring = read(archive.cmems / "history/MO/GL_TS_MO_6300001.nc")
    values = mooring["fields_at"](np.array([0]))
    # The record's precise position, not the file's latest one.
    assert (mooring["lon"][0], mooring["lat"][0]) == pytest.approx((0.4, 0.4))
    assert values["position_source"][0] == "precise"
    assert values["wind_speed_mode"][0] == "D"
    assert mooring["version"] == "013_030 history format 2.0"


def test_cmems_monthly_records_start_after_the_history_file(archive):
    from soba_matchups.adapters import buoy

    read = buoy.reader(recipe_for(archive))
    monthly = read(archive.cmems / "monthly/MO/202606/GL_TS_MO_6300001_202606.nc")
    # 11:50 is in the history file (which ends at 12:00); only 12:10 is new.
    assert [str(t)[11:16] for t in monthly["time"]] == ["12:10"]
    assert list(monthly["flat"]) == [1]


def test_the_safe_pass_agrees_with_the_exact_test_and_keeps_strict_bounds():
    from soba_matchups.adapters import buoy, scat

    product = {
        "Id": "test-safe",
        "ContentDate": {"Start": "2026-06-29T12:00:00Z", "End": "2026-06-29T12:00:03Z"},
        "GeoFootprint": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
    }
    clocks = ["11:29", "11:30", "11:31", "12:00", "12:30", "12:31", "13:00"]
    times = np.array(stamps(*clocks), dtype="datetime64[ns]")
    times[4] += np.timedelta64(3, "s")  # exactly end + 30 min: outside
    reference = {
        "time": times,
        "lon": np.array([0.5, 0.5, 0.5, 1.5, 0.5, 0.5, 0.5]),
        "lat": np.full(7, 0.5),
    }
    expected = scat.match_cells(product, reference, 30)
    assert list(buoy.match_safe(product, reference, 30)) == list(expected) == [2]


def test_buoy_recipe_needs_the_cmems_root_and_others_refuse_it(tmp_path):
    from soba_matchups.config import load_recipe

    for name in ["insitu", "cmems", "esa"]:
        (tmp_path / name).mkdir()
    text = """instrument = "buoy"
platform = "ndbc-tao-cmems"
resolution = "point"
reference_root = "insitu"
cmems_insitu_root = "cmems"
start_date = "2026-06-29"
stop_date = "2026-06-30"
sar_missions = ["S1A"]
time_tolerance_minutes = 30
sar_archive_root = "esa"
output_dir = "run"
"""
    recipe = tmp_path / "buoy.toml"
    recipe.write_text(text)
    assert load_recipe(recipe).cmems_insitu_root == tmp_path / "cmems"
    recipe.write_text(text.replace('cmems_insitu_root = "cmems"\n', ""))
    with pytest.raises(ValueError, match="cmems_insitu_root is required"):
        load_recipe(recipe)
    scat_text = text.replace('"buoy"', '"hscat"').replace('"ndbc-tao-cmems"', '"hy-2b"')
    recipe.write_text(scat_text.replace('"point"', '"25km"'))
    with pytest.raises(ValueError, match="only for it"):
        load_recipe(recipe)


def test_buoy_end_to_end_matches_every_source_and_records_the_selection(local_recipe, archive):
    from soba_matchups.cli import main

    root = local_recipe.parent
    recipe = root / "buoy.toml"
    recipe.write_text(
        local_recipe.read_text()
        .replace('instrument = "hscat"', 'instrument = "buoy"')
        .replace('platform = "hy-2b"', 'platform = "ndbc-tao-cmems"')
        .replace('resolution = "25km"', 'resolution = "point"')
        .replace('reference_root = "reference"', f'reference_root = "{archive.insitu}"')
        .replace("time_tolerance_minutes = 1", "time_tolerance_minutes = 30")
        .replace('output_dir = "result"', 'output_dir = "buoy_result"')
        + f'cmems_insitu_root = "{archive.cmems}"\n'
    )
    assert main(["run", "--recipe", str(recipe)]) == 0
    result = root / "buoy_result"
    manifest = json.loads((result / "manifest.json").read_text())
    assert manifest["schema_version"] == 20
    assert manifest["reference_product"] == "INSITU_NDBC_TAO_CMEMS_WIND"
    assert manifest["rules"]["policy"] == "buoy_local_ocn_imagette_v1"
    assert manifest["reference_selection"]["ncei_land_stations_left_out"] == ["bura1"]
    assert "Stuck-calm runs (failed anemometers) are not flagged." in manifest["limitations"]
    assert manifest["units"]["wind_direction_from"].startswith("degree, where the wind blows from")

    items = {
        "IMG" + p.name.split("__IMG")[1][:3]: json.loads(p.read_text())
        for p in (result / "items").glob("*__INSITU_NDBC_TAO_CMEMS_WIND.json")
    }
    assert sorted(items) == ["IMG001", "IMG002"]
    first = items["IMG001"]["properties"]
    assert first["sarmatchup:other_type"] == "in_situ"
    assert first["sarmatchup:other_instrument"] == "CMEMS in situ + NDBC buoy"
    # The land station and the CMEMS copy of 41999 are not there; 6300001 has one group per file.
    files = sorted(g["reference_file"] for g in first["sarmatchup:matchups"])
    assert files == [
        "41999_2026.parquet",
        "GL_TS_DB_3100001.nc",
        "GL_TS_MO_6300001.nc",
        "GL_TS_MO_6300001_202606.nc",
    ]
    (ndbc,) = [g for g in first["sarmatchup:matchups"] if g["reference_platform"] == "41999"]
    times = [p["ref_time_utc"][11:16] for p in ndbc["points"]]
    assert times == ["11:40", "12:00", "12:30"]
    point = ndbc["points"][0]
    # Row 0 (11:30, the withheld direction) is outside the window; row 1 is the first point.
    assert point["ref_row_0based"] == 1 and point["wind_direction_released"] is True
    assert point["wind_speed_qc_detail"] == "0"
    assert point["wind_speed_mode"] is None and isinstance(point["wind_speed_qc"], int)
    asset = items["IMG001"]["assets"][ndbc["reference_asset"]]
    assert asset["type"] == "application/vnd.apache.parquet"
    cmems_times = sorted(
        p["ref_time_utc"][11:16]
        for g in first["sarmatchup:matchups"]
        if g["reference_platform"] == "6300001"
        for p in g["points"]
    )
    assert cmems_times == ["11:55", "12:10"]

    second = items["IMG002"]["properties"]
    (tao,) = second["sarmatchup:matchups"]
    assert tao["reference_source"] == "TAO mooring" and tao["reference_platform"] == "T10N180W"
    (point,) = tao["points"]
    assert point["wind_speed_mode"] == "R" and point["wind_speed_bias_suspect"] is True
    assert point["ref_lon"] == pytest.approx(179.9)
