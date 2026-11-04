"""Altimeter wave heights (CCI Sea State L2P, CMEMS wave L3), read like the KNMI and SWOT cells."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

import numpy as np
import xarray as xr

POLICY = "alti_local_ocn_imagette_v2"
TYPE = "altimeter"
# Radius around the OSW cell centre of the imagette, for imagettes that no point falls inside.
FALLBACK_RADIUS_KM = 30.0
SPACE = (
    "every point strictly inside the imagette footprint; when none is, the one point within "
    f"{FALLBACK_RADIUS_KM:g} km of the OSW cell centre that is closest in time"
)
RETENTION = (
    "all points with a position, a time and a wave height; "
    "any quality fields of the file are retained without filtering"
)
# Point fields written to the Items, in output order (CCI; a CMEMS file states its own, see below).
FIELDS = [
    "swh",
    "swh_adjusted",
    "swh_denoised",
    "swh_uncertainty",
    "swh_quality_level",
    "swh_rejection_flags",
    "sea_ice_fraction",
]
INTEGER_FIELDS = {"swh_quality_level", "swh_rejection_flags"}
UNITS = {
    "swh": "m",
    "swh_adjusted": "m",
    "swh_denoised": "m",
    "swh_uncertainty": "m",
    "sea_ice_fraction": "1",
}
# Mission -> (mission folders, name token in the files, "platform" attribute). Exactly one folder
# must exist; CCI v4 names the Sentinel-6 folder "sentinel-6", v5 "sentinel-6_a".
CCI_MISSIONS = {
    "cfosat": (("cfosat",), "CFOSAT", "CFOSAT"),
    "cryosat-2": (("cryosat-2",), "CryoSat-2", "CryoSat-2"),
    "jason-2": (("jason-2",), "Jason-2", "Jason-2"),
    "jason-3": (("jason-3",), "Jason-3", "Jason-3"),
    "saral": (("saral",), "SARAL", "SARAL"),
    "sentinel-3a": (("sentinel-3_a",), "Sentinel-3_A", "Sentinel-3 A"),
    "sentinel-3b": (("sentinel-3_b",), "Sentinel-3_B", "Sentinel-3 B"),
    "sentinel-6a": (("sentinel-6", "sentinel-6_a"), "Sentinel-6_A", "Sentinel-6 A"),
}
MISSIONS = CCI_MISSIONS
# CMEMS wave L3 (WAVE_GLO_PHY_SWH_L3_NRT_014_001): mission -> (folder, name token, "platform"
# attribute). The folder is cmems_obs-wave_glo_phy-swh_nrt_<folder>-l3_PT1S under the root.
CMEMS_MISSIONS = {
    "saral": (("al",), "al", "SARAL-Altika"),
    "cryosat-2": (("c2",), "c2", "Cryosat-2"),
    "cfosat": (("cfo",), "cfo", "CFOSAT"),
    "hy-2b": (("h2b",), "h2b", "HaiYang-2B"),
    "hy-2c": (("h2c",), "h2c", "HaiYang-2C"),
    "jason-3": (("j3",), "j3", "Jason-3"),
    "sentinel-3a": (("s3a",), "s3a", "Sentinel-3A"),
    "sentinel-3b": (("s3b",), "s3b", "Sentinel-3B"),
    "sentinel-6a": (("s6a",), "s6a_lr", "Sentinel-6A"),
    "swot-nadir": (("swon",), "swot", "SWOT Nadir"),
}
CMEMS_FIELDS = ["VAVH", "VAVH_UNFILTERED", "WIND_SPEED"]
CMEMS_UNITS = {"VAVH": "m", "VAVH_UNFILTERED": "m", "WIND_SPEED": "m s-1"}
# Where the CCI L2P files sit under the recipe's reference_root: v4 uses "l2p", v5 "l2p-swh".
LAYOUTS = (
    ("data", "satellite", "altimeter", "l2p"),
    ("data", "satellite", "altimeter", "l2p-swh"),
)
_CCI_NAME = re.compile(
    r"ESACCI-SEASTATE-L2P-SWH-(?P<token>.+)-(?P<start>\d{8}T\d{6})-fv(?P<fv>\d{2})\.nc"
)
_CMEMS_NAME = re.compile(
    r"global_vavh_l3_rt_(?P<token>.+?)_(?P<start>\d{8}T\d{6})_(?P<end>\d{8}T\d{6})"
    r"_(?P<generated>\d{8}T\d{6})\.nc"
)
# What differs between the products; the reading steps are shared.
_PRODUCTS = {
    "CCI altimeters": {
        "missions": CCI_MISSIONS,
        "name": _CCI_NAME,
        "level": "L2P",
        "lon": "lon",
        "lat": "lat",
        "fields": FIELDS,
        "integer": INTEGER_FIELDS,
        "units": UNITS,
        "measured": ["swh", "swh_adjusted", "swh_denoised"],
        "release_in_label": True,
    },
    "CMEMS altimeters": {
        "missions": CMEMS_MISSIONS,
        "name": _CMEMS_NAME,
        "level": "L3",
        "lon": "longitude",
        "lat": "latitude",
        "fields": CMEMS_FIELDS,
        "integer": set(),
        "units": CMEMS_UNITS,
        "measured": ["VAVH", "VAVH_UNFILTERED"],
        "release_in_label": False,
    },
}


def _clock(token):
    """Parse a YYYYMMDDTHHMMSS file-name stamp as UTC."""
    return datetime.strptime(token, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def product_suffix(refs):
    """Return "_V<major release>" for the product label, for example "_V5".

    The release comes from the files themselves. One label cannot name files of two releases, so
    the run stops if they differ or if a file states none. CMEMS files carry no release in the
    label: generations of one slot are of release 5_0 or 6_0 within the same day, and each file's
    release is recorded as its "reference_version".
    """
    if not refs or not refs[0]["release_in_label"]:
        return ""
    majors = set()
    for ref in refs:
        match = re.match(r"v(\d+)\.", ref["version"])
        majors.add(match[1] if match else None)
    if len(majors) != 1 or None in majors:
        found = sorted({ref["version"] for ref in refs})
        raise ValueError(f"Cannot name the product: releases {found} are not a single one")
    return f"_V{majors.pop()}"


def _mission_folder(root, folders, layouts):
    """Return the one existing folder of a mission under the root, or None if it has none."""
    candidates = [root.joinpath(*layout, f) for layout in layouts for f in folders]
    existing = [c for c in candidates if c.is_dir()]
    if len(existing) > 1:
        raise ValueError(f"Several folders for one mission: {[str(c) for c in existing]}")
    return existing[0] if existing else None


def _km(lon1, lat1, lon2, lat2):
    """Great-circle distance in km between points given in degrees (arrays broadcast)."""
    p = np.pi / 180
    a = (
        np.sin((lat2 - lat1) * p / 2) ** 2
        + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2
    )
    return 12742.0 * np.arcsin(np.sqrt(a))


def _in_window(product, reference, tolerance_minutes):
    """Mask of the points strictly inside the product's time span widened by the tolerance."""
    start = np.datetime64(product["ContentDate"]["Start"].removesuffix("Z"), "ns")
    end = np.datetime64(product["ContentDate"]["End"].removesuffix("Z"), "ns")
    delta = np.timedelta64(round(tolerance_minutes * 60 * 1e9), "ns")
    time = reference["time"]
    return ~np.isnat(time) & (time > start - delta) & (time < end + delta)


def match_safe(product, reference, tolerance_minutes):
    """Return the points in the SAFE's time window and within the radius of one of its imagettes.

    This is the cheap first pass: a point that no imagette could take is dropped here.
    """
    inside = _in_window(product, reference, tolerance_minutes)
    indices = np.flatnonzero(inside & np.isfinite(reference["lon"]) & np.isfinite(reference["lat"]))
    centres = np.array([row["Centre"] for row in product["Imagettes"]], dtype="float64")
    if not len(indices) or not len(centres):
        return indices[:0]
    lon, lat = reference["lon"][indices, None], reference["lat"][indices, None]
    near = _km(lon, lat, centres[None, :, 0], centres[None, :, 1]).min(axis=1) < FALLBACK_RADIUS_KM
    return indices[near]


def fallback_imagette(imagette, reference, tolerance_minutes):
    """Return the candidate points for an imagette that no point falls inside, with time gaps.

    The candidates are the points in the imagette's time window (its span widened by the
    tolerance) within FALLBACK_RADIUS_KM of its OSW cell centre. The gap is the absolute time
    from the imagette's midpoint, in seconds. The caller keeps the one point with the smallest
    gap over all the reference files.
    """
    window = _in_window(imagette, reference, tolerance_minutes)
    centre_lon, centre_lat = imagette["Centre"]
    near = _km(reference["lon"], reference["lat"], centre_lon, centre_lat) < FALLBACK_RADIUS_KM
    candidates = np.flatnonzero(window & near)
    start = np.datetime64(imagette["ContentDate"]["Start"].removesuffix("Z"), "ns")
    end = np.datetime64(imagette["ContentDate"]["End"].removesuffix("Z"), "ns")
    gap = np.abs(reference["time"][candidates] - (start + (end - start) // 2))
    return candidates, gap / np.timedelta64(1, "s")


def find_files(recipe):
    """List the files of every mission that start in the requested days (stop_date exclusive).

    CCI files sit in one folder per mission, year and day of year; CMEMS files in one folder per
    mission, year and month. A file belongs to the day it starts on, so points after midnight
    stay with their file and a file starting the day before the first requested day is not read.
    """
    cmems = recipe.platform == "cmems"
    spec = _PRODUCTS["CMEMS altimeters" if cmems else "CCI altimeters"]
    paths = []
    for folders, token, _ in spec["missions"].values():
        if cmems:
            folders = tuple(f"cmems_obs-wave_glo_phy-swh_nrt_{f}-l3_PT1S" for f in folders)
        base = _mission_folder(recipe.reference_root, folders, [()] if cmems else LAYOUTS)
        # A mission that is absent from the root is skipped; the root must hold at least one.
        if base is None:
            continue
        day = recipe.start_date
        while day < recipe.stop_date:
            if cmems:
                directory = base / str(day.year) / day.strftime("%m")
                pattern = f"global_vavh_l3_rt_{token}_{day:%Y%m%d}T*.nc"
            else:
                directory = base / str(day.year) / day.strftime("%j")
                pattern = "ESACCI-SEASTATE-L2P-SWH-*.nc"
            for path in sorted(directory.glob(pattern)):
                match = spec["name"].fullmatch(path.name)
                if match is not None and match["token"] == token and path.is_file():
                    paths.append(path)
            day += timedelta(days=1)
    if cmems:
        # A time slot can be generated several times; the latest generation is the one kept.
        latest = {}
        for path in paths:
            match = spec["name"].fullmatch(path.name)
            slot = (match["token"], match["start"], match["end"])
            if slot not in latest or match["generated"] > latest[slot][0]:
                latest[slot] = (match["generated"], path)
        paths = sorted(path for _, path in latest.values())
    if not paths:
        root = recipe.reference_root
        raise ValueError(f"No altimeter files starting in the requested days under {root}")
    return paths


def read_reference(path, *, expected_source, expected_resolution):
    """Load one altimeter file: keep the position and time of every point that has a wave height.

    The recipe's source is "CCI altimeters" or "CMEMS altimeters"; the file's own source is
    "<mission> altimeter", taken from its name and checked against its platform attribute. The
    values are not kept in memory; "fields_at" reads them back for the points that matched. The
    file has no cell grid, so each point is its own row. A pass with no wave height at all (over
    land or ice) is kept as a file with no points, so that the run records it as read.
    """
    path = Path(path)
    spec = _PRODUCTS.get(expected_source)
    if spec is None:
        raise ValueError(f"Unsupported altimeter product: {expected_source}")
    match = spec["name"].fullmatch(path.name)
    if match is None:
        raise ValueError(f"Unsupported altimeter file name: {path.name}")
    mission = next((m for m in spec["missions"].values() if m[1] == match["token"]), None)
    if mission is None:
        raise ValueError(f"Unsupported altimeter file name: {path.name}")
    fields, lon_key, lat_key = spec["fields"], spec["lon"], spec["lat"]
    units = {lon_key: "degrees_east", lat_key: "degrees_north", **spec["units"]}
    with xr.open_dataset(path, engine="netcdf4") as ds:
        if any(k not in ds or ds[k].dims != ("time",) for k in ["time", lon_key, lat_key, *fields]):
            raise ValueError(f"Unsupported altimeter variables/dimensions in {path}")
        for key, unit in units.items():
            if ds[key].attrs.get("units") != unit:
                raise ValueError(f"Unsupported {key} units in {path}")
        if (
            ds.attrs.get("platform") != mission[2]
            or ds.attrs.get("processing_level") != spec["level"]
        ):
            raise ValueError(f"Altimeter platform or level does not match its name: {path}")
        if ds["time"].dtype.kind != "M":
            raise ValueError(f"Reference time is not CF-decoded in {path}")
        lon = ds[lon_key].values.astype("float64")
        lat = ds[lat_key].values.astype("float64")
        time = ds["time"].values
        measured = np.zeros(lon.shape, dtype=bool)
        for key in spec["measured"]:
            measured |= np.isfinite(ds[key].values.astype("float64"))
        attrs = dict(ds.attrs)
    header_start = _clock(match["start"])
    if "end" in match.groupdict():
        # A CMEMS file names its own 3 hour slot and carries no coverage attributes.
        end = _clock(match["end"])
        release = str(attrs.get("product_version", "unknown")).replace("_", ".")
        version = f"v{release}_g{match['generated']}"
    else:
        # A CCI file states its coverage; the release of the dataset is in the references.
        try:
            stated = datetime.strptime(attrs.get("time_coverage_start", ""), "%Y-%m-%dT%H:%M:%SZ")
            end_text = attrs.get("time_coverage_end", "")
        except ValueError as exc:
            raise ValueError(f"Missing or invalid reference coverage in {path}") from exc
        if stated.replace(tzinfo=timezone.utc) != header_start:
            raise ValueError(f"File name and time_coverage_start disagree in {path}")
        try:
            end = datetime.strptime(end_text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            end = None
        release = re.search(r"v(\d+\.\d+)", str(attrs.get("references", "")))
        version = f"v{release[1] if release else 'unknown'}_fv{match['fv']}"
    valid = (
        np.isfinite(lon)
        & np.isfinite(lat)
        & (lon >= -180)
        & (lon <= 360)
        & (np.abs(lat) <= 90)
        & ~np.isnat(time)
        & measured
    )
    times = time[valid]
    if valid.any():
        observed = [
            t.astype("datetime64[us]").item().replace(tzinfo=timezone.utc)
            for t in [times.min(), times.max()]
        ]
    elif end is None:
        # A pass with no wave height needs a stated end to be placed in time.
        raise ValueError(f"Missing or invalid reference coverage in {path}")
    else:
        observed = [header_start, max(header_start, end)]
    header_end = max(header_start, observed[1])
    span = [header_start, *observed]
    if max(span) - min(span) > timedelta(days=1):
        raise ValueError(f"Implausible reference time span (over 24 hours) in {path}")
    flat = np.flatnonzero(valid)

    def fields_at(indices):
        """Read the point fields of the points at these positions (in the arrays above)."""
        rows = flat[indices]
        with xr.open_dataset(path, engine="netcdf4") as ds:
            return {k: ds[k].values[rows].astype("float64") for k in fields}

    return {
        "lon": lon[valid],
        "lat": lat[valid],
        "time": times,
        "flat": flat,
        "columns": 1,
        "fields_at": fields_at,
        "fields": fields,
        "release_in_label": spec["release_in_label"],
        "units": spec["units"],
        "integer_fields": spec["integer"],
        "granule_start_day": header_start.date(),
        "header_start": header_start,
        "header_end": header_end,
        "coverage_start": min(header_start, observed[0]),
        "coverage_end": max(header_end, observed[1]),
        "path": path,
        "source": f"{mission[2]} altimeter",
        "version": version,
        "resolution": expected_resolution,
    }
