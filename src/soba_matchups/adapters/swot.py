"""SWOT KaRIn L2 wave and wind cells (AVISO), read in the same shape as the KNMI cells."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re

import numpy as np
import xarray as xr

POLICY = "swot_local_ocn_imagette_v1"
TYPE = "altimeter"
RETENTION = (
    "all cells with a position, a time and a wave or wind value; "
    "quality, ice and rain flags retained without filtering"
)
# Point fields written to the Items, in output order, with the unit of each measured value.
FIELDS = [
    "swh_karin",
    "swh_karin_qual",
    "swh_karin_uncert",
    "wind_speed_karin",
    "wind_speed_karin_qual",
    "wind_speed_karin_2",
    "wind_speed_karin_2_qual",
    "dynamic_ice_flag",
    "rain_flag",
]
INTEGER_FIELDS = {k for k in FIELDS if k.endswith(("_qual", "_flag"))}
UNITS = {
    "swh_karin": "m",
    "swh_karin_uncert": "m",
    "wind_speed_karin": "m s-1",
    "wind_speed_karin_2": "m s-1",
}
# Processing levels accepted: the D0 releases. The files sit flat inside each folder.
CRIDS = ("PGD0", "PID0")
_NAME = re.compile(
    r"SWOT_L2_LR_SSH_WindWave_(?P<cycle>\d{3})_(?P<pass>\d{3})_(?P<start>\d{8}T\d{6})"
    r"_(?P<end>\d{8}T\d{6})_(?P<crid>[A-Z0-9]{4})_(?P<version>\d{2})\.nc"
)
_FILE_UNITS = {
    "swh_karin": "m",
    "swh_karin_uncert": "m",
    "wind_speed_karin": "m/s",
    "wind_speed_karin_2": "m/s",
}
_MEASURED = ["swh_karin", "wind_speed_karin", "wind_speed_karin_2"]


def _clock(token):
    """Parse a YYYYMMDDTHHMMSS file-name stamp as UTC."""
    return datetime.strptime(token, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def find_files(recipe):
    """List the SWOT files to read: one per (cycle, pass), the highest version, in the days.

    Only regular files directly inside the PGD0 and PID0 folders are looked at; sub-folders and
    names that do not follow the SWOT naming are ignored. A pass found in both folders is an error.
    """
    folders = [recipe.reference_root / crid for crid in CRIDS]
    if not any(folder.is_dir() for folder in folders):
        raise ValueError(f"No {' or '.join(CRIDS)} folder in {recipe.reference_root}")
    chosen = {}
    for folder in filter(Path.is_dir, folders):
        with os.scandir(folder) as entries:
            for entry in entries:
                match = _NAME.fullmatch(entry.name)
                if match is None or match["crid"] != folder.name or not entry.is_file():
                    continue
                key = (match["cycle"], match["pass"])
                known = chosen.get(key)
                if known is not None and known[0] != folder.name:
                    raise ValueError(
                        f"SWOT cycle {key[0]} pass {key[1]} is in both {known[0]} and {folder.name}"
                    )
                if known is None or match["version"] > known[1]:
                    chosen[key] = (folder.name, match["version"], Path(entry.path), match["start"])
    selected = sorted(
        path
        for _, _, path, start in chosen.values()
        if recipe.start_date <= _clock(start).date() < recipe.stop_date
    )
    if not selected:
        raise ValueError(
            f"No SWOT files starting in the requested days under {recipe.reference_root}"
        )
    return selected


def read_reference(path, *, expected_source, expected_resolution):
    """Load one SWOT file: keep the position and time of every cell that carries a measurement.

    The file has no source or resolution attribute, so both come from the recipe's product. Cells
    are flattened line by line and every pixel of a line shares the line's time. The values are
    not kept in memory; "fields_at" reads them back for the cells that matched.
    """
    path = Path(path)
    match = _NAME.fullmatch(path.name)
    if match is None:
        raise ValueError(f"Unsupported SWOT file name: {path.name}")
    grid = ("num_lines", "num_pixels")
    with xr.open_dataset(path, engine="netcdf4") as ds:
        if any(k not in ds or ds[k].dims != grid for k in ["longitude", "latitude", *FIELDS]):
            raise ValueError(f"Unsupported SWOT variables/dimensions in {path}")
        if "time" not in ds or ds["time"].dims != ("num_lines",):
            raise ValueError(f"Unsupported SWOT variables/dimensions in {path}")
        for key, unit in [
            *_FILE_UNITS.items(),
            ("longitude", "degrees_east"),
            ("latitude", "degrees_north"),
        ]:
            if ds[key].attrs.get("units") != unit:
                raise ValueError(f"Unsupported {key} units in {path}")
        # The file must be the pass its name says.
        for attribute, expected in [
            ("cycle_number", int(match["cycle"])),
            ("pass_number", int(match["pass"])),
            ("crid", match["crid"]),
        ]:
            value = ds.attrs.get(attribute)
            if value is None or str(value).strip() != str(expected):
                raise ValueError(f"SWOT {attribute} does not match the file name in {path}")
        if ds["time"].dtype.kind != "M":
            raise ValueError(f"Reference time is not CF-decoded in {path}")
        n_pixels = ds.sizes["num_pixels"]
        lon = ds["longitude"].values.reshape(-1).astype("float64")
        lat = ds["latitude"].values.reshape(-1).astype("float64")
        time = np.repeat(ds["time"].values, n_pixels)
        measured = np.zeros(lon.shape, dtype=bool)
        for key in _MEASURED:
            measured |= np.isfinite(ds[key].values.reshape(-1).astype("float64"))
    valid = (
        np.isfinite(lon)
        & np.isfinite(lat)
        & (lon >= -180)
        & (lon <= 360)
        & (np.abs(lat) <= 90)
        & ~np.isnat(time)
        & measured
    )
    if not valid.any():
        raise ValueError(f"No readable SWOT cells in {path}")
    header_start, header_end = _clock(match["start"]), _clock(match["end"])
    if header_start > header_end:
        raise ValueError(f"Reversed reference coverage in {path}")
    times = time[valid]
    observed = [
        t.astype("datetime64[us]").item().replace(tzinfo=timezone.utc)
        for t in [times.min(), times.max()]
    ]
    span = [header_start, header_end, *observed]
    if max(span) - min(span) > timedelta(days=1):
        raise ValueError(f"Implausible reference time span (over 24 hours) in {path}")
    flat = np.flatnonzero(valid)

    def fields_at(indices):
        """Read the point fields of the cells at these positions (in the arrays above)."""
        cells = flat[indices]
        with xr.open_dataset(path, engine="netcdf4") as ds:
            return {k: ds[k].values.reshape(-1)[cells].astype("float64") for k in FIELDS}

    return {
        "lon": lon[valid],
        "lat": lat[valid],
        "time": times,
        "flat": flat,
        "columns": n_pixels,
        "fields_at": fields_at,
        "granule_start_day": header_start.date(),
        "header_start": header_start,
        "header_end": header_end,
        "coverage_start": min(header_start, observed[0]),
        "coverage_end": max(header_end, observed[1]),
        "path": path,
        "source": expected_source,
        "version": f"{match['crid']}_{match['version']}",
        "resolution": expected_resolution,
    }
