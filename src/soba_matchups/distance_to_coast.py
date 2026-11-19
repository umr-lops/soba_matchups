"""Distance to the nearest coast of a point, read from the NASA GSFC distance-to-coast grid.

The grid is a NetCDF file with the variables lat, lon and distance_to_coast(lat, lon) in km
(0.01 degree, 18000 x 36000). The two coordinate arrays are loaded; the 2.6 GB distance array
is never loaded, only the cells that are asked for are read. A point takes the value of the
nearest grid cell. A missing or unreadable file never stops a run: the lookup then returns
None for every point and states why.
"""

import logging
from pathlib import Path

import netCDF4
import numpy as np

logger = logging.getLogger(__name__)


class DistanceToCoast:
    """Nearest-cell lookup on the distance-to-coast grid, or an explained absence of it."""

    def __init__(self, path):
        """Open the grid at path (None when the recipe sets none); never raises."""
        self.path = None if path is None else Path(path)
        self.error = None
        self.identity = None
        self._lat = self._lon = None
        if self.path is None:
            self.status = "not_configured"
            return
        try:
            stat = self.path.stat()
            if stat.st_size == 0:
                raise OSError("file is empty")
            with netCDF4.Dataset(self.path, "r") as ds:
                self._lat = np.asarray(ds["lat"][:], dtype="float64")
                self._lon = np.asarray(ds["lon"][:], dtype="float64")
                shape = ds["distance_to_coast"].shape
            if shape != (len(self._lat), len(self._lon)) or min(shape) < 2:
                raise ValueError(f"distance_to_coast has shape {shape}, not (lat, lon)")
            self.identity = {
                "path": str(self.path.resolve()),
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
            self.status = "ok"
        except (OSError, KeyError, IndexError, ValueError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.status = "unavailable"
            self._lat = self._lon = None
            logger.error(
                "Distance to coast unavailable (%s): %s; sar_distance_to_coast will be null",
                self.path,
                self.error,
            )

    @property
    def ok(self):
        """True when the grid was opened and can answer."""
        return self.status == "ok"

    def summary(self):
        """Return what the manifest records about this lookup."""
        return {"status": self.status, "file": self.identity, "error": self.error}

    def at(self, lon, lat):
        """Return the distance in km at each (lon, lat), as a list with None where unknown."""
        lon = np.atleast_1d(np.asarray(lon, dtype="float64"))
        lat = np.atleast_1d(np.asarray(lat, dtype="float64"))
        out = [None] * len(lon)
        if not self.ok or not len(lon):
            return out
        # A grid that runs from 0 to 360 degrees takes longitudes folded the same way.
        if self._lon[0] >= 0:
            lon = lon % 360
        row = np.rint((lat - self._lat[0]) / (self._lat[-1] - self._lat[0]) * (len(self._lat) - 1))
        col = np.rint((lon - self._lon[0]) / (self._lon[-1] - self._lon[0]) * (len(self._lon) - 1))
        row = np.clip(row, 0, len(self._lat) - 1)
        col = np.clip(col, 0, len(self._lon) - 1)
        try:
            with netCDF4.Dataset(self.path, "r") as ds:
                var = ds["distance_to_coast"]
                cache = {}
                for k in range(len(lon)):
                    if not (np.isfinite(lon[k]) and np.isfinite(lat[k])):
                        continue
                    cell = (int(row[k]), int(col[k]))
                    if cell not in cache:
                        value = var[cell[0], cell[1]]
                        cache[cell] = None if np.ma.is_masked(value) else float(value)
                    value = cache[cell]
                    out[k] = value if value is not None and np.isfinite(value) else None
        except OSError as exc:
            # The file went away or broke during the run: keep going, say so once.
            self.error = f"{type(exc).__name__}: {exc}"
            self.status = "unavailable"
            logger.error("Distance to coast read failed (%s): %s", self.path, self.error)
            return [None] * len(lon)
        return out
