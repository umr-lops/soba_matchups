"""ESA CCI Sea State L2P altimeter wave heights, read like the KNMI and SWOT cells."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

import numpy as np
import xarray as xr

POLICY = "alti_cci_local_ocn_imagette_v1"
TYPE = "altimeter"
RETENTION = (
    "all points with a position, a time and a wave height; "
    "quality level and rejection flags retained without filtering"
)
# Point fields written to the Items, in output order.
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
# Mission -> (mission folders, name token in the files, "platform" attribute).
# Exactly one folder must exist; v4 names the Sentinel-6 folder "sentinel-6", v5 "sentinel-6_a".
MISSIONS = {
    "cryosat-2": (("cryosat-2",), "CryoSat-2", "CryoSat-2"),
    "jason-2": (("jason-2",), "Jason-2", "Jason-2"),
    "jason-3": (("jason-3",), "Jason-3", "Jason-3"),
    "saral": (("saral",), "SARAL", "SARAL"),
    "sentinel-3a": (("sentinel-3_a",), "Sentinel-3_A", "Sentinel-3 A"),
    "sentinel-3b": (("sentinel-3_b",), "Sentinel-3_B", "Sentinel-3 B"),
    "sentinel-6a": (("sentinel-6", "sentinel-6_a"), "Sentinel-6_A", "Sentinel-6 A"),
}
# Where the L2P files sit under the recipe's reference_root: v4 uses "l2p", v5 "l2p-swh".
LAYOUTS = (
    ("data", "satellite", "altimeter", "l2p"),
    ("data", "satellite", "altimeter", "l2p-swh"),
)
_NAME = re.compile(
    r"ESACCI-SEASTATE-L2P-SWH-(?P<token>.+)-(?P<start>\d{8}T\d{6})-fv(?P<fv>\d{2})\.nc"
)
_FILE_UNITS = {
    "lon": "degrees_east",
    "lat": "degrees_north",
    "swh": "m",
    "swh_adjusted": "m",
    "swh_denoised": "m",
    "swh_uncertainty": "m",
    "sea_ice_fraction": "1",
}
_MEASURED = ["swh", "swh_adjusted", "swh_denoised"]


def _clock(token):
    """Parse a YYYYMMDDTHHMMSS file-name stamp as UTC."""
    return datetime.strptime(token, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def product_suffix(refs):
    """Return "_V<major release>" for the product label, for example "_V5".

    The release comes from the files themselves. One label cannot name files of two releases, so
    the run stops if they differ or if a file states none.
    """
    majors = set()
    for ref in refs:
        match = re.match(r"v(\d+)\.", ref["version"])
        majors.add(match[1] if match else None)
    if len(majors) != 1 or None in majors:
        found = sorted({ref["version"] for ref in refs})
        raise ValueError(f"Cannot name the product: CCI releases {found} are not a single one")
    return f"_V{majors.pop()}"


def find_files(recipe):
    """List the files of every mission that start in the requested days (stop_date exclusive).

    The files sit in one folder per mission, year and day of year. A file belongs to the day it
    starts on, so points after midnight stay with their file and a file starting the day before
    the first requested day is not read.
    """
    paths = []
    for folders, token, _ in MISSIONS.values():
        candidates = [
            recipe.reference_root.joinpath(*layout, f) for layout in LAYOUTS for f in folders
        ]
        existing = [c for c in candidates if c.is_dir()]
        if len(existing) > 1:
            raise ValueError(f"Several folders for one mission: {[str(c) for c in existing]}")
        # A mission that is absent from the root is skipped; the root must hold at least one.
        if not existing:
            continue
        day = recipe.start_date
        while day < recipe.stop_date:
            directory = existing[0] / str(day.year) / day.strftime("%j")
            for path in sorted(directory.glob("ESACCI-SEASTATE-L2P-SWH-*.nc")):
                match = _NAME.fullmatch(path.name)
                if match is not None and match["token"] == token and path.is_file():
                    paths.append(path)
            day += timedelta(days=1)
    if not paths:
        raise ValueError(
            f"No CCI altimeter files starting in the requested days under {recipe.reference_root}"
        )
    return paths


def read_reference(path, *, expected_source, expected_resolution):
    """Load one L2P file: keep the position and time of every point that has a wave height.

    The recipe's source is "CCI altimeters"; the file's own source is "<mission> altimeter",
    taken from its name and checked against its platform attribute. The values are not kept in
    memory; "fields_at" reads them back for the points that matched. The file has no cell grid,
    so each point is its own row. A pass with no wave height at all (over land or ice) is kept
    as a file with no points, so that the run records it as read.
    """
    path = Path(path)
    match = _NAME.fullmatch(path.name)
    mission = None
    if match is not None:
        mission = next((m for m in MISSIONS.values() if m[1] == match["token"]), None)
    if mission is None:
        raise ValueError(f"Unsupported CCI altimeter file name: {path.name}")
    if expected_source != "CCI altimeters":
        raise ValueError(f"CCI altimeter file read for another product: {path}")
    keys = ["time", "lon", "lat", *FIELDS]
    with xr.open_dataset(path, engine="netcdf4") as ds:
        if any(k not in ds or ds[k].dims != ("time",) for k in keys):
            raise ValueError(f"Unsupported CCI altimeter variables/dimensions in {path}")
        for key, unit in _FILE_UNITS.items():
            if ds[key].attrs.get("units") != unit:
                raise ValueError(f"Unsupported {key} units in {path}")
        if ds.attrs.get("platform") != mission[2] or ds.attrs.get("processing_level") != "L2P":
            raise ValueError(f"CCI altimeter platform or level does not match its name: {path}")
        if ds["time"].dtype.kind != "M":
            raise ValueError(f"Reference time is not CF-decoded in {path}")
        lon = ds["lon"].values.astype("float64")
        lat = ds["lat"].values.astype("float64")
        time = ds["time"].values
        measured = np.zeros(lon.shape, dtype=bool)
        for key in _MEASURED:
            measured |= np.isfinite(ds[key].values.astype("float64"))
        stated = ds.attrs.get("time_coverage_start", "")
        stated_end = ds.attrs.get("time_coverage_end", "")
        # The software release of the whole dataset (for example "v4.0") is only in the references.
        release = re.search(r"v(\d+\.\d+)", str(ds.attrs.get("references", "")))
    header_start = _clock(match["start"])
    try:
        stated_start = datetime.strptime(stated, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"Missing or invalid reference coverage in {path}") from exc
    if stated_start != header_start:
        raise ValueError(f"File name and time_coverage_start disagree in {path}")
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
    else:
        # A pass over land or ice can hold no wave height: keep the file with no points.
        try:
            end = datetime.strptime(stated_end, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ValueError(f"Missing or invalid reference coverage in {path}") from exc
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
            return {k: ds[k].values[rows].astype("float64") for k in FIELDS}

    return {
        "lon": lon[valid],
        "lat": lat[valid],
        "time": times,
        "flat": flat,
        "columns": 1,
        "fields_at": fields_at,
        "granule_start_day": header_start.date(),
        "header_start": header_start,
        "header_end": header_end,
        "coverage_start": min(header_start, observed[0]),
        "coverage_end": max(header_end, observed[1]),
        "path": path,
        "source": f"{mission[2]} altimeter",
        "version": f"v{release[1] if release else 'unknown'}_fv{match['fv']}",
        "resolution": expected_resolution,
    }
