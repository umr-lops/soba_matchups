"""Shared KNMI L2B cells matched to full SAFE footprints and SAFE clocks."""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import gzip
from pathlib import Path
import shutil
import tempfile

import numpy as np
import xarray as xr
from shapely import contains_xy
from shapely.affinity import translate
from shapely.geometry import shape
from shapely.ops import unary_union


def match_cells(product, reference, tolerance_minutes):
    """Return indices of reference cells inside the footprint and strictly within the time window.

    The window is the product's start/end widened by the tolerance on each side.
    """
    start = np.datetime64(product["ContentDate"]["Start"].removesuffix("Z"), "ns")
    end = np.datetime64(product["ContentDate"]["End"].removesuffix("Z"), "ns")
    if np.isnat(start) or np.isnat(end) or start > end:
        raise ValueError("Invalid SAFE acquisition interval")
    geometry = shape(product["GeoFootprint"])
    if not geometry.is_valid or geometry.is_empty:
        raise ValueError("Invalid SAFE footprint")
    delta = np.timedelta64(round(tolerance_minutes * 60 * 1e9), "ns")
    # Time filter first: it is cheap and leaves few cells for the geometry test.
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
    # Fold longitudes into [-180, 180) to match the footprint.
    lon = (reference["lon"][indices] + 180) % 360 - 180
    inside = contains_xy(geometry, lon, reference["lat"][indices])
    if (
        geometry.geom_type == "MultiPolygon"
        and geometry.bounds[0] == -180
        and geometry.bounds[2] == 180
    ):
        # Rejoin the artificial map cut; the real footprint edge stays strict.
        unwrapped = unary_union(
            [translate(part, xoff=360) if part.bounds[2] <= 0 else part for part in geometry.geoms]
        )
        inside |= contains_xy(
            unwrapped, np.where(lon < 0, lon + 360, lon), reference["lat"][indices]
        )
    return indices[inside]


def read_reference(path, *, expected_source="HY-2B HSCAT", expected_resolution="25.0 km"):
    """Load one KNMI L2B granule (.nc or .nc.gz), check its metadata and keep valid wind cells."""
    path = Path(path)
    with ExitStack() as stack:
        # Gzipped granules are unpacked to a temporary file that is removed afterwards.
        source = path
        if path.name.endswith(".nc.gz"):
            source = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "reference.nc"
            with gzip.open(path, "rb") as incoming, source.open("wb") as output:
                shutil.copyfileobj(incoming, output)
        ds = stack.enter_context(xr.open_dataset(source, engine="netcdf4"))
        # Confirm this is the expected product before trusting its values.
        if ds.attrs.get("source") != expected_source:
            raise ValueError(f"Unsupported reference source in {path}")
        if ds.attrs.get("pixel_size_on_horizontal") != expected_resolution:
            raise ValueError(f"Reference resolution mismatch in {path}")
        keys = ["lon", "lat", "time", "wind_speed", "wind_dir", "wvc_quality_flag", "ice_prob"]
        if any(k not in ds or ds[k].dims != ("NUMROWS", "NUMCELLS") for k in keys):
            raise ValueError(f"Unsupported reference variables/dimensions in {path}")
        if (
            ds.wind_speed.attrs.get("units") != "m s-1"
            or ds.wind_dir.attrs.get("units") != "degree"
        ):
            raise ValueError(f"Unsupported wind units in {path}")
        for key, unit in [("lon", "degrees_east"), ("lat", "degrees_north"), ("ice_prob", "1")]:
            if ds[key].attrs.get("units") != unit:
                raise ValueError(f"Unsupported {key} units in {path}")
        direction = ds.wind_dir.attrs.get("standard_name")
        # Early ASCAT 2101 declares the convention in its global comment instead.
        declared_to = "All wind directions in oceanographic convention (0 deg. flowing North)"
        if direction != "wind_to_direction" and not (
            direction is None and declared_to in ds.attrs.get("comment", "")
        ):
            raise ValueError(f"Unknown or unsupported wind direction convention in {path}")
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
        observed_times = [
            t.astype("datetime64[us]").item().replace(tzinfo=timezone.utc)
            for t in [times.min(), times.max()]
        ]
        if max(clocks + observed_times) - min(clocks + observed_times) > timedelta(days=1):
            raise ValueError(f"Implausible reference time span (over 24 hours) in {path}")
        # Keep only cells with a finite position, time and wind vector.
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
        # Coverage grows to include the cell times actually observed.
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
            source=ds.attrs["source"],
            # Recorded for provenance only; the version is not used to accept or reject a file.
            version=str(ds.attrs.get("software_identification_wind", "unknown")),
            resolution=ds.attrs["pixel_size_on_horizontal"],
            flat=np.flatnonzero(valid),
            columns=ds.sizes["NUMCELLS"],
            coverage_start=clocks[0],
            coverage_end=clocks[1],
        )
    return result
