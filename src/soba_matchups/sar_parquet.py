"""SAR metadata from the CDSE daily parquet files, with no SAFE opened and nothing fetched.

A daily file holds one row per Sentinel-1 product. For a WV OCN product the footprint is one
polygon per imagette, and the column WVImagettes lists the imagette files (name, number, times).
Polygon k belongs to the imagette numbered k, once the two halves of an imagette cut at the
dateline are merged. The archive is asked only which imagette files exist, by name.
"""

from datetime import datetime, timedelta
import hashlib
import json
import logging
import math
import re
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union

from .output import file_identity
from .sar_metadata import _utc

logger = logging.getLogger(__name__)
IMAGETTE_MAPPING = "parquet_polygon_number_equals_file_suffix_after_dateline_merge"
TIME_SOURCE = "WVImagettes ContentDate (whole seconds from the file name)"
COLUMNS = ["Name", "Id", "ContentDate", "GeoFootprint", "WVImagettes"]
# A vertex this close to +-180 degrees lies on the dateline cut.
CUT_EDGE_TOLERANCE = 1e-6
# Largest latitude difference, in degrees, between the two ends of a cut shared by two halves.
CUT_LATITUDE_TOLERANCE = 1e-3
_WV_OCN = re.compile(r"S1[ABCD]_WV_OCN__2[A-Z]")


def _rings(footprint):
    """Return the polygons of a GeoJSON footprint as lists of (lon, lat), closing vertex dropped."""
    geometry = json.loads(footprint)
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
    """Return the archive folder of a WV OCN SAFE (sentinel-1x/L2/WV/<S1x>_WV_OCN__2S/year/day)."""
    mission = name[:3]
    start = datetime.strptime(name.split("_")[5], "%Y%m%dT%H%M%S")
    return (
        archive_root
        / ("sentinel-1" + mission[-1].lower())
        / "L2"
        / "WV"
        / (mission + "_WV_OCN__2S")
        / start.strftime("%Y/%j")
        / name
    )


def _read_day(path):
    """Return the rows of one daily parquet file as dicts, reading only the columns used."""
    missing = [c for c in COLUMNS if c not in pq.read_schema(path).names]
    if missing:
        raise ValueError(f"Daily parquet file lacks the columns {missing}: {path}")
    return pq.read_table(path, columns=COLUMNS).to_pylist()


def _product(row, archive_root):
    """Build the product record of one parquet row, with its imagettes and their availability."""
    name = row["Name"]
    listed = json.loads(row["WVImagettes"])
    content = json.loads(row["ContentDate"])
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
    folder = _safe_folder(archive_root, name)
    present = (
        {p.name for p in (folder / "measurement").iterdir()}
        if (folder / "measurement").is_dir()
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
                "href": (folder / relative).as_uri(),
                "available": present is not None and item["Name"] in present,
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
        "Imagettes": imagettes,
        "SkippedImagettes": [],
    }, unavailable


def discover_parquet(
    start: str, end: str, missions: tuple[str, ...], parquet_root: Path, sar_archive_root: Path
) -> tuple[list[dict], dict]:
    """Read the daily parquet files of the covered days and return the WV OCN products to match.

    Products with no imagette list, and products whose polygons and imagettes disagree, are left
    out and named in the returned provenance. Imagette files absent from the archive are kept,
    flagged unavailable and named there too. Coverage must already include the time tolerance.
    """
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
        "sar_archive_root": str(sar_archive_root),
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
            for row in _read_day(path):
                name = row["Name"]
                if not _WV_OCN.match(name) or name[:3] not in missions or name in seen:
                    continue
                seen.add(name)
                count += 1
                content = json.loads(row["ContentDate"])
                if not (_utc(content["Start"]) < last and _utc(content["End"]) > first):
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
        ("safes_skipped", "SAFEs skipped (polygons and imagettes disagree)"),
        ("imagettes_unavailable", "imagettes whose file is not in the archive"),
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
