"""In situ wind from buoys: NDBC (NCEI), TAO, and the CMEMS platforms that are in neither.

Three sources, read as they are published:

* NDBC buoys: ``<reference_root>/ncei/ncei_concat/wind/<station>_<year>.parquet``, written by
  ``get_ncei_data``. Only buoys are read: NDBC numbers buoys with digits (``41001``) and names
  its coastal land stations with letters (``bura1``), which are left out.
* TAO moorings: ``<reference_root>/tao/tao_concat/<site>_<year>.parquet``, written by
  ``get_tao_data`` (delayed mode where it exists, real time otherwise).
* CMEMS in situ (INSITU_GLO_PHYBGCWAV_DISCRETE_MYNRT_013_030): the netCDF files under the
  recipe's ``cmems_insitu_root``, chosen from the product's own
  ``index_{history,monthly,latest}.txt``. Only buoys: files of the moored (MO) and drifting (DB)
  types whose ``source`` attribute is ``moored surface buoy`` or ``drifting surface float``; the
  MO type also holds platforms, coastal masts and lightships, which are left out. A platform whose
  code is an NDBC station or a TAO site or WMO number is left out too, since its records are
  already read from those sources.

Each file is one reference "granule": its records in the requested days (stop_date exclusive) are
the points. The values are written as the sources give them: the speed at the anemometer's
height (not 10 m), the direction the wind blows from, and every flag kept without filtering.
"""

import csv
from datetime import timedelta, timezone
from functools import lru_cache
import json
import logging
from pathlib import Path
import re

import netCDF4
import numpy as np
import pyarrow.parquet as pq
from shapely.geometry import shape

from soba_matchups.adapters import SkippableReference, scat

logger = logging.getLogger(__name__)

POLICY = "buoy_local_ocn_imagette_v1"
TYPE = "in_situ"
RETENTION = (
    "all records with a position, a time and a wind speed; QC flags, data modes, released and "
    "withdrawn marks are retained without filtering"
)
# Point fields written to the Items, in output order. Every source writes all of them, with null
# where it has no equivalent (the design document gives the mapping).
FIELDS = [
    "wind_speed",
    "wind_direction_from",
    "anemometer_height",
    "wind_speed_qc",
    "wind_direction_qc",
    "wind_speed_qc_detail",
    "wind_direction_qc_detail",
    "wind_speed_released",
    "wind_direction_released",
    "wind_speed_mode",
    "wind_direction_mode",
    "wind_speed_withdrawn",
    "wind_direction_withdrawn",
    "wind_speed_bias_suspect",
    "position_source",
    "position_qc",
    "position_offset_km",
]
INTEGER_FIELDS = {"wind_speed_qc", "wind_direction_qc", "position_qc"}
UNITS = {
    "wind_speed": "m s-1, at the anemometer height (not 10 m)",
    "wind_direction_from": "degree, where the wind blows from, clockwise from true north",
    "anemometer_height": "m above the surface",
    "position_offset_km": "km from the nominal (mooring) position",
}
LIMITATIONS = [
    "Buoy speeds are at the anemometer height, not 10 m; directions are 'from'.",
    "NDBC land stations (letter ids) are left out; NDBC buoys are those with digit ids.",
    "CMEMS platforms whose code is an NDBC station or a TAO site or WMO number are left out.",
    "CMEMS files are read only when their source attribute is 'moored surface buoy' or"
    " 'drifting surface float' (types MO and DB); other platforms are left out.",
    "CMEMS monthly files are read only after the platform's history file ends, latest files only"
    " after both; files the CMEMS index lists but the disk lacks are left out and listed.",
    "Stuck-calm runs (failed anemometers) are not flagged.",
]
NCEI_FOLDER = ("ncei", "ncei_concat", "wind")
TAO_FOLDER = ("tao", "tao_concat")
CMEMS_CHANNELS = ("history", "monthly", "latest")
# CMEMS platform types that can hold buoys, and the "source" attribute values that are buoys.
CMEMS_BUOY_TYPES = {"MO", "DB"}
CMEMS_BUOY_SOURCES = {"moored surface buoy", "drifting surface float"}
# <region>_<family>_<type>_<code>[_<YYYY>|_<YYYYMM>|_<YYYYMMDD>].nc: ship (FB) history files
# are split by year, monthly and latest files carry their month or day.
_CMEMS_NAME = re.compile(
    r"(?P<region>[A-Z]{2})_(?P<family>[A-Z]{2})_(?P<type>[A-Z]{2})_(?P<code>.+?)"
    r"(?:_(?P<date>\d{4}|\d{6}|\d{8}))?\.nc"
)
_STATION_YEAR = re.compile(r"(?P<station>.+)_(?P<year>\d{4})\.parquet")
_NCEI_COLUMNS = [
    "time",
    "lat",
    "lon",
    "position_source",
    "gps_qc",
    "nominal_lat",
    "nominal_lon",
    "anemometer_height",
    "wind_speed",
    "wind_speed_qc",
    "wind_speed_qc_detail",
    "wind_speed_released",
    "wind_direction",
    "wind_direction_qc",
    "wind_direction_qc_detail",
    "wind_direction_released",
]
_TAO_COLUMNS = [
    "time",
    "lat",
    "lon",
    "position_source",
    "nominal_lat",
    "nominal_lon",
    "anemometer_height",
    "wind_speed",
    "wind_speed_qc",
    "wind_speed_mode",
    "wind_speed_withdrawn",
    "wind_speed_bias_suspect",
    "wind_direction",
    "wind_direction_qc",
    "wind_direction_mode",
    "wind_direction_withdrawn",
]


def _utc(value):
    """Parse an ISO time ("...Z" or naive, UTC) as numpy datetime64[ns]."""
    return np.datetime64(str(value).removesuffix("Z"), "ns")


def _km(lon1, lat1, lon2, lat2):
    """Great-circle distance in km between points given in degrees (arrays broadcast)."""
    p = np.pi / 180
    a = (
        np.sin((lat2 - lat1) * p / 2) ** 2
        + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2
    )
    return 12742.0 * np.arcsin(np.sqrt(a))


def _wmo5(code):
    """Return the five-digit WMO number of a seven-digit CMEMS code (5200013 -> 52013), or None."""
    match = re.fullmatch(r"(\d\d)00(\d\d\d)", code)
    return match[1] + match[2] if match else None


def _window(recipe):
    """The requested days as [start, stop) datetime64[ns] bounds."""
    return _utc(recipe.start_date.isoformat()), _utc(recipe.stop_date.isoformat())


def _naive(value):
    """A datetime (aware or naive, UTC) as datetime64[ns]."""
    return np.datetime64(value.replace(tzinfo=None), "ns")


def _index_spans(folder, station_key):
    """Read a station-year index.parquet into {(station, year): (t0, t1)}; {} if it is absent."""
    path = folder / "index.parquet"
    if not path.is_file():
        return {}
    table = pq.read_table(path, columns=[station_key, "year", "t0", "t1"]).to_pydict()
    rows = zip(table[station_key], table["year"], table["t0"], table["t1"])
    return {
        (str(station), int(year)): (_naive(t0), _naive(t1))
        for station, year, t0, t1 in rows
        if t0 is not None and t1 is not None
    }


def _station_years(folder, station_key, recipe, keep):
    """List the <station>_<year>.parquet files of the requested years that reach the days."""
    start, stop = _window(recipe)
    spans = _index_spans(folder, station_key)
    last_year = (recipe.stop_date - timedelta(days=1)).year
    paths = []
    for year in range(recipe.start_date.year, last_year + 1):
        for path in sorted(folder.glob(f"*_{year}.parquet")):
            station = _STATION_YEAR.fullmatch(path.name)["station"]
            if not keep(station):
                continue
            span = spans.get((station, year))
            # The index, where there is one, spares opening files that miss the days.
            if span is not None and (span[1] < start or span[0] >= stop):
                continue
            paths.append(path)
    return paths


def _read_cmems_index(root, channel):
    """Yield the rows of one CMEMS index_<channel>.txt that carry wind, as dicts."""
    path = root / f"index_{channel}.txt"
    if not path.is_file():
        raise ValueError(f"CMEMS index not found: {path}")
    with path.open(newline="") as stream:
        header = None
        for row in csv.reader(stream):
            if not row:
                continue
            if row[0].startswith("#"):
                if "product_id" in row[0]:
                    header = [row[0].lstrip("# ").strip(), *(c.strip() for c in row[1:])]
                continue
            if header is None:
                raise ValueError(f"CMEMS index without a column header: {path}")
            entry = dict(zip(header, row))
            parameters = set(entry.get("parameters", "").split())
            if "WSPD" in parameters or {"WSPE", "WSPN"} <= parameters:
                yield entry


def _shared_platforms(ncei_folder, tao_folder):
    """The NDBC station ids and the TAO site codes and WMO numbers, over every year."""
    ncei_ids = {
        match["station"].lower()
        for path in ncei_folder.glob("*.parquet")
        if (match := _STATION_YEAR.fullmatch(path.name))
    }
    tao_sites, tao_wmos = set(), {}
    for path in sorted(tao_folder.glob("*.parquet")):
        match = _STATION_YEAR.fullmatch(path.name)
        if match is None or match["station"].upper() in tao_sites:
            continue
        site = match["station"].upper()
        tao_sites.add(site)
        meta = json.loads((pq.read_schema(path).metadata or {}).get(b"tao_meta", b"{}"))
        if meta.get("wmo"):
            tao_wmos[str(meta["wmo"])] = site
    return ncei_ids, tao_sites, tao_wmos


def _left_out(code, ncei_ids, tao_sites, tao_wmos):
    """Why a CMEMS platform is already read from NDBC or TAO, or None if it is not."""
    wmo = _wmo5(code)
    if code.lower() in ncei_ids:
        return "NDBC station in NCEI"
    if wmo is not None and wmo in ncei_ids:
        return "NDBC station in NCEI (WMO number)"
    if code.upper() in tao_sites:
        return "TAO site"
    for number in (code, wmo):
        if number in tao_wmos:
            return f"TAO site {tao_wmos[number]} (WMO number)"
    return None


def _cmems_source(path):
    """The "source" attribute of a CMEMS file (what the platform is), or None if unreadable."""
    try:
        with netCDF4.Dataset(path) as ds:
            return getattr(ds, "source", None)
    except OSError:
        return None


def _cmems_files(cmems_root, recipe, shared):
    """Choose the CMEMS files of the days; return them and what was left out."""
    start, stop = _window(recipe)
    left_out, missing, duplicates, other_types = {}, [], {}, {}
    entries, ends, regions = [], {}, {}
    for channel in CMEMS_CHANNELS:
        for entry in _read_cmems_index(cmems_root, channel):
            match = _CMEMS_NAME.fullmatch(entry["file_name"].rsplit("/", 1)[-1])
            if match is None:
                continue
            if match["type"] not in CMEMS_BUOY_TYPES:
                other_types.setdefault(match["type"], set()).add(match["code"])
                continue
            reason = _left_out(match["code"], *shared)
            if reason is not None:
                left_out[match["code"]] = reason
                continue
            try:
                t0 = _utc(entry["time_coverage_start"])
                t1 = _utc(entry["time_coverage_end"])
            except (KeyError, ValueError):
                continue
            key = (match["type"], match["code"])
            last = ends.setdefault(key, {})
            last[channel] = max(last.get(channel, t1), t1)
            regions.setdefault(key, set()).add(match["region"])
            entries.append((channel, key, match["region"], t0, t1, entry))
    files = {}
    for channel, key, region, t0, t1, entry in entries:
        # A platform distributed by two regions is read once, from GL where it is there.
        chosen = "GL" if "GL" in regions[key] else min(regions[key])
        if region != chosen:
            duplicates[key[1]] = f"{region}, read from {chosen}"
            continue
        # Each channel continues where the ones before it end, so no record is read twice.
        earlier = CMEMS_CHANNELS[: CMEMS_CHANNELS.index(channel)]
        previous = [ends[key][c] for c in earlier if c in ends[key]]
        after = max(previous) if previous else None
        if t1 < start or t0 >= stop or (after is not None and t1 <= after):
            continue
        # The index names the dataset's release folder; the files sit in the same tree here.
        relative = entry["file_name"].split("/", 2)[-1]
        path = cmems_root / relative
        if not path.is_file():
            missing.append(relative)
            continue
        files[path] = {
            "source": "cmems",
            "platform": key[1],
            "platform_type": key[0],
            "channel": channel,
            "after": after,
            "data_mode": entry.get("data_mode") or None,
        }
    if missing:
        logger.warning("%d CMEMS files listed in the index are not on disk", len(missing))
    # MO also holds oil platforms, coastal masts and lightships: each file says what it is. The
    # kind belongs to the platform, so one file of each is opened (one at a time: the netCDF
    # library is not safe to call from several threads).
    kinds = {}
    for path, info in files.items():
        key = (info["platform_type"], info["platform"])
        if key not in kinds:
            kinds[key] = _cmems_source(path)
    rejected = {key for key, kind in kinds.items() if kind not in CMEMS_BUOY_SOURCES}
    not_buoys = {code: f"source {kinds[(kind, code)]!r}" for kind, code in rejected}
    files = {
        path: info
        for path, info in files.items()
        if (info["platform_type"], info["platform"]) not in rejected
    }
    report = {
        "cmems_types_left_out": {t: len(codes) for t, codes in sorted(other_types.items())},
        "cmems_not_buoys_left_out": dict(sorted(not_buoys.items())),
        "cmems_platforms_left_out": dict(sorted(left_out.items())),
        "cmems_region_duplicates_left_out": dict(sorted(duplicates.items())),
        "cmems_index_files_missing": sorted(missing),
    }
    return files, report


@lru_cache(maxsize=4)
def _plan(recipe):
    """Choose every file a recipe reads, and record what was left out and why.

    Returns (files, selection): files maps each path to what its reader needs to know, in the
    order they are read; selection is the provenance written to the manifest.
    """
    root = recipe.reference_root
    ncei_folder, tao_folder = root.joinpath(*NCEI_FOLDER), root.joinpath(*TAO_FOLDER)
    for folder in (ncei_folder, tao_folder):
        if not folder.is_dir():
            raise ValueError(f"Buoy reference folder does not exist: {folder}")
    cmems_root = recipe.cmems_insitu_root
    if cmems_root is None or not cmems_root.is_dir():
        raise ValueError("Buoy recipes need an existing cmems_insitu_root")
    shared = _shared_platforms(ncei_folder, tao_folder)
    files = {}
    for path in _station_years(ncei_folder, "station", recipe, lambda s: s[:2].isdigit()):
        files[path] = {"source": "ncei", "platform": _STATION_YEAR.fullmatch(path.name)["station"]}
    for path in _station_years(tao_folder, "station_id", recipe, lambda s: True):
        files[path] = {"source": "tao", "platform": _STATION_YEAR.fullmatch(path.name)["station"]}
    cmems, report = _cmems_files(cmems_root, recipe, shared)
    files.update(cmems)
    selection = {
        "sources": {"ncei": str(ncei_folder), "tao": str(tao_folder), "cmems": str(cmems_root)},
        "files": {
            source: sum(info["source"] == source for info in files.values())
            for source in ("ncei", "tao", "cmems")
        },
        "ncei_land_stations_left_out": sorted(s for s in shared[0] if not s[:2].isdigit()),
        **report,
    }
    return files, selection


def find_files(recipe):
    """List every file the recipe reads: NDBC and TAO station-years, then CMEMS files."""
    files, _ = _plan(recipe)
    if not files:
        root = recipe.reference_root
        raise ValueError(f"No buoy file with records in the requested days under {root}")
    return list(files)


def selection(recipe):
    """Return what was read and what was left out, for the manifest."""
    return _plan(recipe)[1]


def reader(recipe):
    """Return the function the pipeline calls on each path, bound to the recipe's days."""
    files, _ = _plan(recipe)
    start, stop = _window(recipe)

    def read(path, *, expected_source=None, expected_resolution=None):
        """Read one file of the plan; the product checks are the readers' own."""
        return read_file(path, files[path], start, stop)

    return read


# ----------------------------------------------------------------------------- reading


def _nulls(n):
    """An object array of n nulls."""
    return np.full(n, None, dtype=object)


def _nans(n):
    """A float array of n NaNs."""
    return np.full(n, np.nan)


def _floats(values):
    """Convert to float64, with nulls as NaN."""
    if values.dtype == object:
        return np.array([np.nan if v is None else v for v in values], dtype="float64")
    return values.astype("float64")


def _objects(values):
    """Strings and booleans as an object array (Python values, None for nulls)."""
    return values.astype(object)


def _parquet(path, meta_key, stage, columns):
    """Read the needed columns of one station-year parquet and check what wrote it."""
    pf = pq.ParquetFile(path)
    meta = json.loads((pf.schema_arrow.metadata or {}).get(meta_key, b"{}"))
    if not str(meta.get("stage", "")).startswith(stage):
        raise ValueError(f"Not a {stage} file: {path}")
    absent = [c for c in columns if c not in pf.schema_arrow.names]
    if absent:
        raise ValueError(f"Missing columns {absent} in {path}")
    units = meta.get("columns", {})
    for key, unit in [("wind_speed", "m s-1"), ("wind_direction", "degree")]:
        if key in units and units[key].get("units") != unit:
            raise ValueError(f"Unsupported {key} units in {path}")
    table = pf.read(columns=columns)
    return meta, {c: table[c].to_numpy(zero_copy_only=False) for c in columns}


def _finish(path, info, raw, start, stop, *, source, version, platform_type):
    """Keep the records in the days (after the channel cut-off), sorted by time.

    raw holds one entry per file row: "time", "lon", "lat" and every point field, so that "flat"
    is the record's row in the file.
    """
    time = raw["time"].astype("datetime64[ns]")
    lon, lat = raw["lon"], raw["lat"]
    valid = (
        ~np.isnat(time)
        & (time >= start)
        & (time < stop)
        & np.isfinite(lon)
        & np.isfinite(lat)
        & (lon >= -180)
        & (lon <= 360)
        & (np.abs(lat) <= 90)
        & np.isfinite(raw["wind_speed"])
    )
    if info.get("after") is not None:
        valid &= time > info["after"]
    if not valid.any():
        raise SkippableReference(f"No wind record in the requested days: {path}")
    flat = np.flatnonzero(valid)
    flat = flat[np.argsort(time[flat], kind="stable")]
    values = {key: raw[key][flat] for key in FIELDS}
    times = time[flat]
    first, last = (
        t.astype("datetime64[us]").item().replace(tzinfo=timezone.utc) for t in times[[0, -1]]
    )

    def fields_at(indices):
        """Return the point fields of the records at these positions."""
        return {key: values[key][indices] for key in FIELDS}

    return {
        "lon": lon[flat].astype("float64"),
        "lat": lat[flat].astype("float64"),
        "time": times,
        "flat": flat,
        "columns": 1,
        "fields_at": fields_at,
        "granule_start_day": first.date(),
        "header_start": first,
        "header_end": last,
        "coverage_start": first,
        "coverage_end": last,
        "path": Path(path),
        "source": source,
        "version": version,
        "resolution": "point",
        "group_fields": {
            "reference_platform": info["platform"],
            "reference_platform_type": platform_type,
        },
    }


def _read_ncei(path, info, start, stop):
    """One NDBC buoy-year from get_ncei_data: the published wind, its QC and release marks."""
    meta, c = _parquet(path, b"ndbc_meta", "get_ncei_data", _NCEI_COLUMNS)
    if meta.get("product") != "wind":
        raise ValueError(f"Not an NCEI wind file: {path}")
    n = len(c["time"])
    lon, lat = _floats(c["lon"]), _floats(c["lat"])
    raw = {
        "time": c["time"],
        "lon": lon,
        "lat": lat,
        "wind_speed": _floats(c["wind_speed"]),
        "wind_direction_from": _floats(c["wind_direction"]),
        "anemometer_height": _floats(c["anemometer_height"]),
        "wind_speed_qc": _floats(c["wind_speed_qc"]),
        "wind_direction_qc": _floats(c["wind_direction_qc"]),
        "wind_speed_qc_detail": _objects(c["wind_speed_qc_detail"]),
        "wind_direction_qc_detail": _objects(c["wind_direction_qc_detail"]),
        "wind_speed_released": _objects(c["wind_speed_released"]),
        "wind_direction_released": _objects(c["wind_direction_released"]),
        "wind_speed_mode": _nulls(n),
        "wind_direction_mode": _nulls(n),
        "wind_speed_withdrawn": _nulls(n),
        "wind_direction_withdrawn": _nulls(n),
        "wind_speed_bias_suspect": _nulls(n),
        "position_source": _objects(c["position_source"]),
        "position_qc": _floats(c["gps_qc"]),
        "position_offset_km": _km(lon, lat, _floats(c["nominal_lon"]), _floats(c["nominal_lat"])),
    }
    version = f"get_ncei_data {meta.get('version', 'unknown')}"
    return _finish(
        path, info, raw, start, stop, source="NDBC buoy", version=version, platform_type="MO"
    )


def _read_tao(path, info, start, stop):
    """One TAO site-year from get_tao_data: delayed mode where it exists, with its marks."""
    meta, c = _parquet(path, b"tao_meta", "get_tao_data", _TAO_COLUMNS)
    n = len(c["time"])
    lon, lat = _floats(c["lon"]), _floats(c["lat"])
    raw = {
        "time": c["time"],
        "lon": lon,
        "lat": lat,
        "wind_speed": _floats(c["wind_speed"]),
        "wind_direction_from": _floats(c["wind_direction"]),
        "anemometer_height": _floats(c["anemometer_height"]),
        "wind_speed_qc": _floats(c["wind_speed_qc"]),
        "wind_direction_qc": _floats(c["wind_direction_qc"]),
        "wind_speed_qc_detail": _nulls(n),
        "wind_direction_qc_detail": _nulls(n),
        "wind_speed_released": _nulls(n),
        "wind_direction_released": _nulls(n),
        "wind_speed_mode": _objects(c["wind_speed_mode"]),
        "wind_direction_mode": _objects(c["wind_direction_mode"]),
        "wind_speed_withdrawn": _objects(c["wind_speed_withdrawn"]),
        "wind_direction_withdrawn": _objects(c["wind_direction_withdrawn"]),
        "wind_speed_bias_suspect": _objects(c["wind_speed_bias_suspect"]),
        "position_source": _objects(c["position_source"]),
        "position_qc": _nans(n),
        "position_offset_km": _km(lon, lat, _floats(c["nominal_lon"]), _floats(c["nominal_lat"])),
    }
    version = f"get_tao_data {meta.get('version', 'unknown')}"
    return _finish(
        path, info, raw, start, stop, source="TAO mooring", version=version, platform_type="MO"
    )


def _cmems_time(variable):
    """Decode a CMEMS TIME variable ("days since 1950-01-01T00:00:00Z") to datetime64[ns]."""
    units = getattr(variable, "units", "")
    if "since" not in units:
        raise ValueError("CMEMS TIME has no 'since' units")
    unit, origin = (part.strip() for part in units.split("since", 1))
    scale = {"days": 86400e9, "hours": 3600e9, "seconds": 1e9}.get(unit)
    if scale is None:
        raise ValueError(f"Unsupported CMEMS time unit: {unit}")
    base = np.datetime64(origin.removesuffix("Z").replace(" ", "T"), "ns")
    values = np.ma.filled(variable[:].astype("float64"), np.nan)
    out = np.full(values.shape, np.datetime64("NaT"), "datetime64[ns]")
    ok = np.isfinite(values)
    out[ok] = base + np.round(values[ok] * scale).astype("int64").astype("timedelta64[ns]")
    return out


def _array(ds, name, rows=slice(None)):
    """Read a variable (its record slice when it runs along TIME) as float64, fills as NaN."""
    variable = ds[name]
    data = variable[rows] if variable.dimensions[:1] == ("TIME",) else variable[:]
    return np.ma.filled(np.ma.asarray(data).astype("float64"), np.nan)


def _first_level(ds, name, rows):
    """Per record, the value at the first depth level holding one: (values, qc, level)."""
    if name not in ds.variables:
        return None, None, None
    values = _array(ds, name, rows)
    qc_name = f"{name}_QC"
    qc = _array(ds, qc_name, rows) if qc_name in ds.variables else np.full(values.shape, np.nan)
    if values.ndim == 1:
        values, qc = values[:, None], qc[:, None]
    ok = np.isfinite(values)
    level = ok.argmax(axis=1)
    index = np.arange(values.shape[0])
    any_ok = ok.any(axis=1)
    return (
        np.where(any_ok, values[index, level], np.nan),
        np.where(any_ok, qc[index, level], np.nan),
        np.where(any_ok, level, -1),
    )


def _cmems_wind(ds, rows, n):
    """Speed, 'from' direction, their QC and the level used, from WSPD/WDIR or their substitutes.

    WTODIR gives where the wind goes, so it is turned by 180 degrees; WSPE/WSPN fill records with
    no measured speed or direction.
    """
    speed, speed_qc, level = _first_level(ds, "WSPD", rows)
    east, east_qc, east_level = _first_level(ds, "WSPE", rows)
    north, north_qc, _ = _first_level(ds, "WSPN", rows)
    components = east is not None and north is not None
    if speed is None and not components:
        raise ValueError("CMEMS file without wind speed")
    if speed is None:
        speed, speed_qc, level = _nans(n), _nans(n), np.full(n, -1)
    if components:
        fill = ~np.isfinite(speed) & np.isfinite(east) & np.isfinite(north)
        speed = np.where(fill, np.hypot(east, north), speed)
        speed_qc = np.where(fill, np.fmax(east_qc, north_qc), speed_qc)
        level = np.where(fill, east_level, level)
    direction, direction_qc, _ = _first_level(ds, "WDIR", rows)
    if direction is None:
        direction, direction_qc = _nans(n), _nans(n)
    towards, towards_qc, _ = _first_level(ds, "WTODIR", rows)
    if towards is not None:
        fill = ~np.isfinite(direction) & np.isfinite(towards)
        direction = np.where(fill, (towards + 180) % 360, direction)
        direction_qc = np.where(fill, towards_qc, direction_qc)
    if components:
        fill = ~np.isfinite(direction) & np.isfinite(east) & np.isfinite(north)
        bearing = (270 - np.degrees(np.arctan2(north, east))) % 360
        direction = np.where(fill, bearing, direction)
        direction_qc = np.where(fill, np.fmax(east_qc, north_qc), direction_qc)
    return speed, speed_qc, direction, direction_qc, level


def _cmems_position(ds, rows, n):
    """Per record: lon, lat, how the position was given, and its QC.

    The record's precise fix comes first, then its own LATITUDE, then the file's one position
    (a moored buoy's latest one).
    """
    for lat_key, lon_key, label in [
        ("PRECISE_LATITUDE", "PRECISE_LONGITUDE", "precise"),
        ("LATITUDE", "LONGITUDE", "record"),
    ]:
        if lat_key in ds.variables and ds[lat_key].dimensions == ("TIME",):
            lon, lat = _array(ds, lon_key, rows), _array(ds, lat_key, rows)
            break
    else:
        label = "file"
        lon = np.full(n, _array(ds, "LONGITUDE").ravel()[0])
        lat = np.full(n, _array(ds, "LATITUDE").ravel()[0])
    qc = _nans(n)
    if "POSITION_QC" in ds.variables and ds["POSITION_QC"].dimensions == ("TIME",):
        qc = _array(ds, "POSITION_QC", rows)
    return lon, lat, label, qc


def _cmems_height(ds, rows, n, level):
    """Height of the level used, in m above the surface (DEPH is positive down).

    DEPH of 0 (height lost) and beyond 100 m (fill values) are left null.
    """
    if "DEPH" not in ds.variables:
        return _nans(n)
    deph = _array(ds, "DEPH", rows)
    if ds["DEPH"].dimensions[:1] == ("TIME",):
        deph = deph if deph.ndim == 2 else deph[:, None]
    else:
        deph = np.broadcast_to(np.atleast_1d(deph)[None, :], (n, np.atleast_1d(deph).size))
    picked = deph[np.arange(n), np.clip(level, 0, deph.shape[1] - 1)]
    height = np.where(level >= 0, -picked, np.nan)
    return np.where((height == 0) | (np.abs(height) > 100), np.nan, height)


def _read_cmems(path, info, start, stop):
    """One CMEMS 013_030 file: wind at the first level holding it, with its QC and data mode."""
    try:
        ds = netCDF4.Dataset(path)
    except OSError as exc:
        raise SkippableReference(f"Unreadable CMEMS file {path}: {exc}") from exc
    with ds:
        if "TIME" not in ds.variables:
            raise ValueError(f"CMEMS file without TIME: {path}")
        for name, unit in [("WSPD", "m s-1"), ("WSPE", "m s-1"), ("WDIR", "degree")]:
            if name in ds.variables and getattr(ds[name], "units", unit) != unit:
                raise ValueError(f"Unsupported {name} units in {path}")
        time = _cmems_time(ds["TIME"])
        keep = ~np.isnat(time) & (time >= start) & (time < stop)
        if info.get("after") is not None:
            keep &= time > info["after"]
        if not keep.any():
            raise SkippableReference(f"No wind record in the requested days: {path}")
        # Read only the slice of records that holds the days: history files span decades.
        first, last = (int(i) for i in np.flatnonzero(keep)[[0, -1]])
        rows, n = slice(first, last + 1), last - first + 1
        try:
            speed, speed_qc, direction, direction_qc, level = _cmems_wind(ds, rows, n)
        except ValueError as exc:
            raise ValueError(f"{exc}: {path}") from exc
        lon, lat, position_source, position_qc = _cmems_position(ds, rows, n)
        height = _cmems_height(ds, rows, n, level)
        mode = info.get("data_mode") or getattr(ds, "data_mode", None)
        format_version = getattr(ds, "format_version", "unknown")
    columns = {
        "lon": lon,
        "lat": lat,
        "wind_speed": speed,
        "wind_direction_from": direction,
        "anemometer_height": height,
        "wind_speed_qc": speed_qc,
        "wind_direction_qc": direction_qc,
        "wind_speed_qc_detail": _nulls(n),
        "wind_direction_qc_detail": _nulls(n),
        "wind_speed_released": _nulls(n),
        "wind_direction_released": _nulls(n),
        "wind_speed_mode": np.full(n, mode, dtype=object),
        "wind_direction_mode": np.full(n, mode, dtype=object),
        "wind_speed_withdrawn": _nulls(n),
        "wind_direction_withdrawn": _nulls(n),
        "wind_speed_bias_suspect": _nulls(n),
        "position_source": np.full(n, position_source, dtype=object),
        "position_qc": position_qc,
        "position_offset_km": _nans(n),
    }
    # Pad the slice back to whole-file rows, so "flat" is the record's index in the file.
    raw = {"time": time[: last + 1]}
    for key, value in columns.items():
        filler = _nulls(first) if value.dtype == object else _nans(first)
        raw[key] = np.concatenate([filler, value])
    version = f"013_030 {info['channel']} format {format_version}"
    return _finish(
        path,
        info,
        raw,
        start,
        stop,
        source="CMEMS in situ",
        version=version,
        platform_type=info["platform_type"],
    )


_READERS = {"ncei": _read_ncei, "tao": _read_tao, "cmems": _read_cmems}


def read_file(path, info, start, stop):
    """Read one planned file and keep its records in [start, stop) (datetime64[ns], UTC).

    info is the file's entry in the plan: its "source" ("ncei", "tao" or "cmems"), "platform",
    and for CMEMS "platform_type", "channel", "after" (the channel cut-off) and "data_mode".
    """
    return _READERS[info["source"]](Path(path), info, start, stop)


# ----------------------------------------------------------------------------- matching

_BOUNDS = {}


def _bounds(product):
    """The footprint bounds of a product, computed once per product."""
    key = product["Id"]
    if key not in _BOUNDS:
        _BOUNDS[key] = shape(product["GeoFootprint"]).bounds
    return _BOUNDS[key]


def match_safe(product, reference, tolerance_minutes):
    """Return the records inside the SAFE's footprint and time window: the cheap first pass.

    A run holds hundreds to thousands of station files against every SAFE, so the records in the
    window are found by bisection on the sorted times, and the footprint's bounding box rejects
    most stations before the exact test (which is the same as for every other reference).
    """
    start = np.datetime64(product["ContentDate"]["Start"].removesuffix("Z"), "ns")
    end = np.datetime64(product["ContentDate"]["End"].removesuffix("Z"), "ns")
    delta = np.timedelta64(round(tolerance_minutes * 60 * 1e9), "ns")
    times = reference["time"]
    low = np.searchsorted(times, start - delta, side="right")
    high = np.searchsorted(times, end + delta, side="left")
    indices = np.arange(low, high)
    if not len(indices):
        return indices
    west, south, east, north = _bounds(product)
    lon = (reference["lon"][indices] + 180) % 360 - 180
    lat = reference["lat"][indices]
    indices = indices[(lon >= west) & (lon <= east) & (lat >= south) & (lat <= north)]
    if not len(indices):
        return indices
    subset = {key: reference[key][indices] for key in ("lon", "lat", "time")}
    return indices[scat.match_cells(product, subset, tolerance_minutes)]
