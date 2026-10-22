"""HY-2B HSCAT 25 km cells matched to full SAFE footprints and SAFE clocks."""

from contextlib import ExitStack
from datetime import datetime, timezone
import gzip
from pathlib import Path
import shutil
import tempfile

import numpy as np
import xarray as xr
from shapely import contains_xy
from shapely.geometry import shape


def match_cells(product, reference, tolerance_minutes):
    start = np.datetime64(product["ContentDate"]["Start"].removesuffix("Z"), "ns")
    end = np.datetime64(product["ContentDate"]["End"].removesuffix("Z"), "ns")
    if np.isnat(start) or np.isnat(end) or start > end:
        raise ValueError("Invalid SAFE acquisition interval")
    geometry = shape(product["GeoFootprint"])
    if not geometry.is_valid or geometry.is_empty:
        raise ValueError("Invalid SAFE footprint")
    delta = np.timedelta64(round(tolerance_minutes * 60 * 1e9), "ns")
    mask = (
        np.isfinite(reference["lon"])
        & np.isfinite(reference["lat"])
        & np.isfinite(reference["wind_speed"])
        & np.isfinite(reference["wind_dir"])
        & ~np.isnat(reference["time"])
        & (reference["time"] > start - delta)
        & (reference["time"] < end + delta)
    )
    indices = np.flatnonzero(mask)
    lon = (reference["lon"][indices] + 180) % 360 - 180
    return indices[contains_xy(geometry, lon, reference["lat"][indices])]


def read_reference(path, version):
    path = Path(path)
    with ExitStack() as stack:
        source = path
        if path.name.endswith(".nc.gz"):
            source = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "reference.nc"
            with gzip.open(path, "rb") as incoming, source.open("wb") as output:
                shutil.copyfileobj(incoming, output)
        ds = stack.enter_context(xr.open_dataset(source, engine="netcdf4"))
        if ds.attrs.get("source") != "HY-2B HSCAT":
            raise ValueError(f"Unsupported reference source in {path}")
        if str(ds.attrs.get("software_identification_wind")) != version:
            raise ValueError(f"Reference version mismatch in {path}")
        if ds.attrs.get("pixel_size_on_horizontal") != "25.0 km":
            raise ValueError(f"Reference resolution mismatch in {path}")
        keys = ["lon", "lat", "time", "wind_speed", "wind_dir", "wvc_quality_flag", "ice_prob"]
        if any(k not in ds or ds[k].dims != ("NUMROWS", "NUMCELLS") for k in keys):
            raise ValueError(f"Unsupported reference variables/dimensions in {path}")
        if (
            ds.wind_speed.attrs.get("units") != "m s-1"
            or ds.wind_dir.attrs.get("units") != "degree"
        ):
            raise ValueError(f"Unsupported wind units in {path}")
        raw = {key: ds[key].values.reshape(-1) for key in keys}
        if raw["time"].dtype.kind != "M":
            raise ValueError(f"Reference time is not CF-decoded in {path}")
        clocks = []
        for prefix in ["start", "stop"]:
            try:
                value = ds.attrs[prefix + "_date"] + "T" + ds.attrs[prefix + "_time"]
                clocks.append(datetime.fromisoformat(value).replace(tzinfo=timezone.utc))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Missing or invalid reference coverage in {path}") from exc
        if clocks[0] > clocks[1]:
            raise ValueError(f"Reversed reference coverage in {path}")
        times = raw["time"][~np.isnat(raw["time"])]
        if not times.size:
            raise ValueError(f"No readable reference times in {path}")
        valid = (
            np.isfinite(raw["lon"])
            & np.isfinite(raw["lat"])
            & (raw["lon"] >= -180)
            & (raw["lon"] <= 360)
            & (np.abs(raw["lat"]) <= 90)
            & ~np.isnat(raw["time"])
            & np.isfinite(raw["wind_speed"])
            & np.isfinite(raw["wind_dir"])
        )
        header_start, header_end = clocks
        eligible_times = raw["time"][valid]
        if eligible_times.size:
            observed = [
                t.astype("datetime64[us]").item().replace(tzinfo=timezone.utc)
                for t in [eligible_times.min(), eligible_times.max()]
            ]
            clocks = [min(clocks[0], observed[0]), max(clocks[1], observed[1])]
        result = {key: value[valid] for key, value in raw.items()}
        result.update(
            granule_start_day=header_start.date(), header_start=header_start, header_end=header_end
        )
        result.update(
            path=path,
            flat=np.flatnonzero(valid),
            columns=ds.sizes["NUMCELLS"],
            coverage_start=clocks[0],
            coverage_end=clocks[1],
        )
    return result
