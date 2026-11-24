"""SAR metadata from the CDSE daily parquet files, with no SAFE opened and nothing fetched.

A daily file holds one row per Sentinel-1 product. For a WV OCN product the footprint is one
polygon per imagette, and the column WVImagettes lists the imagette files (name, number, times).
Polygon k belongs to the imagette numbered k, once the two halves of an imagette cut at the
dateline are merged. The archive is asked only which imagette files exist, by name.

An IW or EW OCN product is one scene: its footprint (possibly cut at the dateline or at the
prime meridian) and its ContentDate are all that is used. The archive is asked only which
measurement file the SAFE holds.
"""

import ast
from datetime import datetime, timedelta
import hashlib
import json
import logging
import math
import re
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import shapely
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union

from .output import file_identity
from .sar_metadata import _utc

logger = logging.getLogger(__name__)
IMAGETTE_MAPPING = "parquet_polygon_number_equals_file_suffix_after_dateline_merge"
TIME_SOURCE = "WVImagettes ContentDate (whole seconds from the file name)"
SCENE_MAPPING = "parquet_product_footprint_is_one_scene"
SCENE_TIME_SOURCE = "product ContentDate"
COLUMNS = ["Name", "Id", "ContentDate", "GeoFootprint", "WVImagettes"]
# An IW or EW scene needs no imagette list, so it can also be read from files without one.
SCENE_COLUMNS = ["Name", "Id", "ContentDate", "GeoFootprint"]
# A vertex this close to +-180 degrees lies on the dateline cut.
CUT_EDGE_TOLERANCE = 1e-6
# Largest latitude difference, in degrees, between the two ends of a cut shared by two halves.
CUT_LATITUDE_TOLERANCE = 1e-3
_OCN = {mode: re.compile(rf"S1[ABCD]_{mode}_OCN__2[A-Z]") for mode in ("WV", "IW", "EW")}
# Scene pieces are joined on a grid this fine (degrees): halves cut at 179.99999 instead of 180
# would otherwise leave a sliver between them.
SCENE_GRID_DEGREES = 1e-4


def _loads(text):
    """Decode a JSON column value; also the Python-literal form older daily files use."""
    try:
        return json.loads(text)
    except ValueError:
        return ast.literal_eval(text)


def _rings(footprint):
    """Return the polygons of a GeoJSON footprint as lists of (lon, lat), closing vertex dropped."""
    geometry = _loads(footprint)
    if geometry["type"] == "Polygon":
        polygons = [geometry["coordinates"]]
    elif geometry["type"] == "MultiPolygon":
        polygons = geometry["coordinates"]
    else:
        raise ValueError(f"Unexpected footprint type {geometry['type']}")
    return [[tuple(vertex) for vertex in polygon[0][:-1]] for polygon in polygons]


def _cut_latitudes(ring, side):
    """Return the sorted latitudes of the vertices on the dateline cut (side is 180 or -180)."""
    return sorted(y for x, y in ring if abs(x - side) < CUT_EDGE_TOLERANCE)


def _are_halves(first, second):
    """Tell whether two polygons are the east and west halves of one imagette, in either order."""
    for east, west in ((first, second), (second, first)):
        a, b = _cut_latitudes(east, 180), _cut_latitudes(west, -180)
        if a and b and abs(a[0] - b[0]) < CUT_LATITUDE_TOLERANCE:
            if abs(a[-1] - b[-1]) < CUT_LATITUDE_TOLERANCE:
                return True
    return False


def merge_dateline(rings):
    """Group the polygons that are halves of one imagette; return lists of polygon positions.

    Two neighbours in the footprint are merged when one has vertices on the +180 cut, the other
    on the -180 cut, and the latitudes at the two ends of the cut agree. Either may come first.
    """
    groups, position = [], 0
    while position < len(rings):
        if position + 1 < len(rings) and _are_halves(rings[position], rings[position + 1]):
            groups.append([position, position + 1])
            position += 2
        else:
            groups.append([position])
            position += 1
    return groups


def centre(rings):
    """Return the area centroid [lon, lat] of an imagette, given its polygon or its two halves."""
    lat0 = float(np.mean([y for ring in rings for _, y in ring]))
    lon0 = rings[0][0][0]
    scale = math.cos(math.radians(lat0))
    # A local plane in degrees keeps a dateline pair contiguous; longitudes unwrap around lon0.
    planar = [
        Polygon([((((x - lon0 + 180) % 360) - 180) * scale, y - lat0) for x, y in ring])
        for ring in rings
    ]
    union = unary_union(planar)
    if union.is_empty or union.area == 0:
        raise ValueError("Empty imagette footprint")
    middle = union.centroid
    return [float((lon0 + middle.x / scale + 180) % 360 - 180), float(lat0 + middle.y)]


def _geometry(rings):
    """Return the GeoJSON geometry of an imagette: one Polygon, or a MultiPolygon for two halves."""
    shapes = [Polygon(ring) for ring in rings]
    if any(not s.is_valid or s.is_empty for s in shapes):
        raise ValueError("Invalid imagette polygon")
    return mapping(shapes[0] if len(shapes) == 1 else unary_union(shapes))


def _safe_folder(archive_root, name):
    """Return the archive folder of an OCN SAFE (L2/<mode>/<S1x>_<mode>_OCN__2S/<year>/<doy>)."""
    mission, mode = name[:3], name[4:6]
    start = datetime.strptime(name.split("_")[5], "%Y%m%dT%H%M%S")
    return (
        archive_root
        / ("sentinel-1" + mission[-1].lower())
        / "L2"
        / mode
        / f"{mission}_{mode}_OCN__2S"
        / start.strftime("%Y/%j")
        / name
    )


def _read_day(path, columns=COLUMNS):
    """Return the rows of one daily parquet file as dicts, reading only the columns used."""
    missing = [c for c in columns if c not in pq.read_schema(path).names]
    if missing:
        raise ValueError(f"Daily parquet file lacks the columns {missing}: {path}")
    return pq.read_table(path, columns=columns).to_pylist()


def scene_corners(rings):
    """Return the corners of a scene footprint as [lon, lat], longitudes continuous across 180.

    The pieces of a footprint cut at the dateline are joined into one polygon. The vertices the
    cut added (on 0 or +-180 degrees, also where a scene crosses the prime meridian) are dropped
    when more than four remain, so a footprint the cutting left with six or eight vertices gives
    its four corners again. Fails when the pieces do not form one polygon.
    """
    lon0 = rings[0][0][0]
    pieces = [
        Polygon([(lon0 + ((x - lon0 + 180) % 360 - 180), y) for x, y in ring]) for ring in rings
    ]
    union = shapely.union_all(pieces, grid_size=SCENE_GRID_DEGREES)
    if union.geom_type != "Polygon" or union.is_empty:
        raise ValueError(f"Footprint pieces do not form one scene ({union.geom_type})")
    corners = [[float(x), float(y)] for x, y in union.exterior.coords[:-1]]

    def on_cut(x):
        return abs(x / 180 - round(x / 180)) * 180 < SCENE_GRID_DEGREES

    kept = [c for c in corners if not on_cut(c[0])]
    return kept if len(kept) >= 4 and len(corners) > 4 else corners


def _scene(row, archive_root):
    """Build the product record of one IW or EW OCN row: a single scene and its availability."""
    name = row["Name"]
    content = _loads(row["ContentDate"])
    _utc(content["Start"]), _utc(content["End"])
    rings = _rings(row["GeoFootprint"])
    product_id = "local_" + hashlib.sha256(name.encode()).hexdigest()
    corners = scene_corners(rings)
    folder = _safe_folder(archive_root, name) if archive_root is not None else None
    files = (
        sorted(p.name for p in (folder / "measurement").glob("*.nc"))
        if folder is not None and (folder / "measurement").is_dir()
        else None
    )
    measurement = files[0] if files is not None and len(files) == 1 else None
    if folder is None:
        href = name
    elif measurement is None:
        href = folder.as_uri()
    else:
        href = (folder / "measurement" / measurement).as_uri()
    scene = {
        # Independent of the archive, so a run with or without it gives the same Item ids.
        "Id": "local_" + hashlib.sha256((product_id + "/scene").encode()).hexdigest(),
        "Name": measurement,
        "index": 1,
        "ContentDate": {"Start": content["Start"], "End": content["End"]},
        "GeoFootprint": _geometry(rings),
        "Centre": centre(rings),
        "href": href,
        "available": folder is None or measurement is not None,
    }
    unavailable = []
    if not scene["available"]:
        reason = (
            "SAFE not in the archive"
            if files is None
            else f"{len(files)} measurement files in the archive SAFE, expected 1"
        )
        unavailable.append({"safe": name, "index": 1, "name": None, "reason": reason})
    return {
        "Id": product_id,
        "Name": name,
        "ContentDate": scene["ContentDate"],
        "GeoFootprint": scene["GeoFootprint"],
        # The four corners in continuous longitudes, for the SAR angle model.
        "SceneRing": corners,
        "Imagettes": [scene],
        "SkippedImagettes": [],
    }, unavailable


def _product(row, archive_root):
    """Build the product record of one parquet row, with its imagettes and their availability."""
    name = row["Name"]
    listed = _loads(row["WVImagettes"])
    content = _loads(row["ContentDate"])
    _utc(content["Start"]), _utc(content["End"])
    rings = _rings(row["GeoFootprint"])
    groups = merge_dateline(rings)
    numbers = sorted(item["index"] for item in listed)
    if len(groups) != len(listed):
        raise ValueError(
            f"{len(groups)} footprint polygons after the dateline merge, {len(listed)} imagettes"
        )
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError("Imagette numbers are not 1..N")
    product_id = "local_" + hashlib.sha256(name.encode()).hexdigest()
    folder = _safe_folder(archive_root, name) if archive_root is not None else None
    present = (
        {p.name for p in (folder / "measurement").iterdir()}
        if folder is not None and (folder / "measurement").is_dir()
        else None
    )
    imagettes, unavailable = [], []
    for item in sorted(listed, key=lambda item: item["index"]):
        group = groups[item["index"] - 1]
        member = [rings[i] for i in group]
        # The same identifier as the local archive source gives, so both sources agree on Item ids.
        relative = "measurement/" + item["Name"]
        imagettes.append(
            {
                "Id": "local_" + hashlib.sha256((product_id + "/" + relative).encode()).hexdigest(),
                "Name": item["Name"],
                "index": item["index"],
                "ContentDate": item["ContentDate"],
                "GeoFootprint": _geometry(member),
                "Centre": centre(member),
                # With no archive the file is not looked for: the link is the path inside the SAFE.
                "href": (folder / relative).as_uri()
                if folder is not None
                else f"{name}/{relative}",
                "available": folder is None or (present is not None and item["Name"] in present),
            }
        )
        if not imagettes[-1]["available"]:
            reason = "SAFE not in the archive" if present is None else "file not in the archive"
            unavailable.append(
                {"safe": name, "index": item["index"], "name": item["Name"], "reason": reason}
            )
    return {
        "Id": product_id,
        "Name": name,
        "ContentDate": content,
        "GeoFootprint": mapping(unary_union([shape(i["GeoFootprint"]) for i in imagettes])),
        # The footprint as the catalogue gives it (one ring per imagette, in time order).
        "WVFootprint": row["GeoFootprint"],
        "Imagettes": imagettes,
        "SkippedImagettes": [],
    }, unavailable


def discover_parquet(
    start: str,
    end: str,
    missions: tuple[str, ...],
    parquet_root: Path,
    sar_archive_root: Path | None = None,
    mode: str = "WV",
) -> tuple[list[dict], dict]:
    """Read the daily parquet files of the covered days and return the OCN products to match.

    mode is WV (one record per imagette) or IW / EW (one record per product, the scene).
    Products with no imagette list, and products whose polygons and imagettes disagree (or, for a
    scene, whose footprint is not one polygon), are left out and named in the returned
    provenance. Measurement files absent from the archive are kept, flagged unavailable and named
    there too. Coverage must already include the time tolerance.
    """
    if mode not in _OCN:
        raise ValueError(f"Unsupported SAR mode {mode!r}")
    first, last = _utc(start), _utc(end)
    if first >= last:
        raise ValueError("start must precede end")
    provenance = {
        "source": "cdse_daily_parquet",
        "parquet_inputs": [],
        "scanned_files": [],
        "missing_files": [],
        "coverage": {"start": start, "end": end},
        "missions": sorted(set(missions)),
        "sar_parquet_root": str(parquet_root),
        "sar_archive_root": None if sar_archive_root is None else str(sar_archive_root),
        "sar_mode": mode,
        "coverage_scope": "products listed in the selected daily parquet files",
        "safes_without_imagettes": [],
        "safes_skipped": [],
        "imagettes_unavailable": [],
    }
    products, seen, count = [], set(), 0
    day = first.date() - timedelta(days=1)
    while day <= last.date():
        path = parquet_root / f"s1_{day.isoformat()}.parquet"
        provenance["scanned_files"].append(str(path))
        if not path.is_file():
            provenance["missing_files"].append(str(path))
        else:
            provenance["parquet_inputs"].append(file_identity(path))
            for row in _read_day(path, COLUMNS if mode == "WV" else SCENE_COLUMNS):
                name = row["Name"]
                if not _OCN[mode].match(name) or name[:3] not in missions or name in seen:
                    continue
                seen.add(name)
                count += 1
                content = _loads(row["ContentDate"])
                if not (_utc(content["Start"]) < last and _utc(content["End"]) > first):
                    continue
                if mode != "WV":
                    try:
                        product, unavailable = _scene(row, sar_archive_root)
                    except (ValueError, KeyError, TypeError, SyntaxError) as exc:
                        provenance["safes_skipped"].append({"safe": name, "reason": str(exc)})
                        continue
                    provenance["imagettes_unavailable"].extend(unavailable)
                    products.append(product)
                    continue
                if row["WVImagettes"] is None:
                    provenance["safes_without_imagettes"].append(
                        {"safe": name, "reason": "no imagette list in the parquet file"}
                    )
                    continue
                try:
                    product, unavailable = _product(row, sar_archive_root)
                except (ValueError, KeyError, TypeError) as exc:
                    provenance["safes_skipped"].append({"safe": name, "reason": str(exc)})
                    continue
                provenance["imagettes_unavailable"].extend(unavailable)
                products.append(product)
        day += timedelta(days=1)
    if not provenance["parquet_inputs"]:
        raise ValueError(f"No daily parquet files found under: {parquet_root}")
    provenance["safes_found"] = count
    provenance["products_selected"] = len(products)
    # Everything left out or unusable is named in the manifest and summarised here at each run.
    for key, text in [
        ("safes_without_imagettes", "SAFEs without an imagette list"),
        ("safes_skipped", "SAFEs skipped (unusable footprint or imagette list)"),
        ("imagettes_unavailable", "imagettes whose file is not in the archive (IW/EW: scenes)"),
        ("missing_files", "daily parquet files missing"),
    ]:
        if provenance[key]:
            logger.warning(
                "%d %s; listed in the manifest discovery.%s", len(provenance[key]), text, key
            )
    return sorted(products, key=lambda p: (p["ContentDate"]["Start"], p["Name"])), provenance


def verify_parquet_inputs(provenance: dict) -> None:
    """Fail closed when any daily parquet file changed during the run."""
    if provenance.get("source") != "cdse_daily_parquet":
        raise ValueError("Expected cdse_daily_parquet provenance")
    for identity in provenance["parquet_inputs"]:
        try:
            unchanged = file_identity(Path(identity["path"])) == identity
        except OSError as exc:
            raise ValueError(f"Parquet input changed or missing: {identity['path']}") from exc
        if not unchanged:
            raise ValueError(f"Parquet input changed: {identity['path']}")
