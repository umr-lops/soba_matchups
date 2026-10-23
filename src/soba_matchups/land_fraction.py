"""Full SAFE footprint sea fractions from an explicitly supplied local land mask."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pyogrio.raw
from pyogrio import read_info
from pyproj import CRS, Transformer
from shapely import from_wkb, get_coordinates, transform, union_all
from shapely.geometry import shape


def _polygons(geometry, label):
    if (
        geometry.geom_type not in {"Polygon", "MultiPolygon"}
        or geometry.is_empty
        or not geometry.is_valid
        or geometry.area <= 0
    ):
        raise ValueError(f"Invalid {label}: expected valid, nonzero-area polygons")
    coordinates = get_coordinates(geometry)
    if (
        not np.isfinite(coordinates).all()
        or (np.abs(coordinates[:, 0]) > 180).any()
        or (np.abs(coordinates[:, 1]) > 90).any()
    ):
        raise ValueError(f"Invalid {label}: coordinates must be WGS84 longitude/latitude")
    parts = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
    for part in parts:
        for ring in [part.exterior, *part.interiors]:
            longitudes = np.asarray(ring.coords)[:, 0]
            if (np.abs(np.diff(longitudes)) > 180).any():
                raise ValueError(f"Invalid {label}: split antimeridian-crossing rings first")
    return parts


class LandMask:
    """Read candidate land per footprint component; never load the world mask.

    Metadata is validated at construction, geometry when spatially read. No
    geometry repair or alternative dataset is used for invalid input.
    """

    def __init__(self, path, *, source=None):
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file() or self.path.suffix.lower() not in {".shp", ".geojson", ".json"}:
            raise ValueError(f"Invalid local land mask: {self.path}")
        if self.path.suffix.lower() == ".shp":
            for suffix in (".shx", ".dbf", ".prj"):
                if not self.path.with_suffix(suffix).is_file():
                    raise ValueError(f"Invalid land mask: missing {suffix} sidecar")
        try:
            info = read_info(self.path)
            crs = CRS.from_user_input(info["crs"])
            if self.path.suffix.lower() != ".shp":
                # GDAL silently treats unrecognized GeoJSON CRS names as 4326.
                with self.path.open(encoding="utf-8-sig") as stream:
                    declared = json.load(stream).get("crs")
                if declared is not None:
                    if declared["type"] != "name":
                        raise ValueError("unsupported GeoJSON CRS declaration")
                    crs = CRS.from_user_input(declared["properties"]["name"])
        except Exception as exc:
            raise ValueError(f"Invalid land mask or CRS: {self.path}") from exc
        if not crs.equals(CRS.from_epsg(4326), ignore_axis_order=True):
            raise ValueError("Land mask CRS must be WGS84 longitude/latitude (EPSG:4326)")
        if info["features"] == 0 or info["geometry_type"] not in {"Polygon", "MultiPolygon"}:
            raise ValueError("Invalid land mask: expected nonempty polygon dataset")
        self._transformer = Transformer.from_crs(4326, 6933, always_xy=True)
        paths = self._input_paths()
        self._identities = {path: self._identity(path) for path in paths}
        files = []
        for path in paths:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            files.append({"path": str(path), "sha256": digest})
        self.provenance = {
            "path": str(self.path),
            "files": files,
            "source": source if source is not None else "local file; origin not asserted",
            "expected_dataset": "OSM land-polygons-split-4326",
            "equal_area_crs": "EPSG:6933",
            "calculation_rule": (
                "100 * (1 - area(union(land intersections)) / area(full SAFE footprint)); "
                "EPSG:4326 to EPSG:6933, always_xy=True; no quality filtering"
            ),
        }
        self.verify_unchanged()

    def _input_paths(self):
        if self.path.suffix.lower() != ".shp":
            return [self.path]
        paths = [self.path, *(self.path.with_suffix(s) for s in (".shx", ".dbf", ".prj"))]
        paths.extend(
            self.path.with_suffix(s) for s in (".cpg", ".qix") if self.path.with_suffix(s).exists()
        )
        return paths

    @staticmethod
    def _identity(path):
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    def verify_unchanged(self):
        """Reject changed, replaced or missing inputs, including optional sidecars.

        Call at the end of processing. Stat identities bracket initial hashes and
        are checked here without repeatedly hashing the large world land mask.
        """
        try:
            current = {path: self._identity(path) for path in self._input_paths()}
        except OSError as exc:
            raise ValueError("Land mask inputs changed during processing") from exc
        if current != self._identities:
            raise ValueError("Land mask inputs changed during processing")

    def _project(self, geometry):
        return transform(
            geometry,
            lambda xy: np.column_stack(
                self._transformer.transform(xy[:, 0], xy[:, 1], errcheck=True)
            ),
        )

    def sea_percent(self, geojson_geometry):
        """Return sea area as a percentage of the entire supplied SAFE geometry."""
        try:
            footprint = shape(geojson_geometry)
        except Exception as exc:
            raise ValueError("Invalid SAFE footprint geometry") from exc
        parts = _polygons(footprint, "SAFE footprint")
        projected = self._project(footprint)
        if not np.isfinite(projected.area) or projected.area <= 0:
            raise ValueError("Invalid SAFE footprint: zero or nonfinite projected area")
        intersections = []
        for part in parts:
            try:
                _, _, geometries, _ = pyogrio.raw.read(
                    self.path, columns=[], bbox=part.bounds, force_2d=True
                )
            except Exception as exc:
                raise ValueError(f"Unable to read land mask: {self.path}") from exc
            projected_part = self._project(part)
            for wkb in geometries:
                try:
                    land = from_wkb(wkb)
                    if land is None:
                        raise ValueError("null geometry")
                    _polygons(land, "land mask")
                except Exception as exc:
                    raise ValueError(f"Invalid land mask geometry: {exc}") from exc
                intersection = self._project(land).intersection(projected_part)
                if not intersection.is_empty:
                    intersections.append(intersection)
        land_area = union_all(intersections).area
        return max(0.0, min(100.0, 100.0 * (1.0 - land_area / projected.area)))
