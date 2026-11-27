"""SAR metadata from the CDSE daily parquet files, with no SAFE opened and nothing fetched.

A daily file holds one row per Sentinel-1 product. For a WV OCN product the footprint is one
polygon per imagette, and the column WVImagettes lists the imagette files (name, number, times).
Polygon k belongs to the imagette numbered k, once the two halves of an imagette cut at the
dateline are merged. The archive is asked only which imagette files exist, by name.

An IW, EW or SM OCN product is one scene: its footprint (possibly cut at the dateline or at the
prime meridian) and its ContentDate are all that is used. The archive is asked only which
measurement file the SAFE holds. SM products are named by their beam (S1_ to S6_) and filed
under L2/SM. daily_parquet_files_v2, the folder every mode reads, does not list SM products yet
(its producer drops them), so an SM run on it finds none and says so.
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
from shapely.errors import GEOSException
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union

from .output import file_identity
from .sar_angles import _signed_area
from .sar_metadata import _utc

logger = logging.getLogger(__name__)
IMAGETTE_MAPPING = "parquet_polygon_number_equals_file_suffix_after_dateline_merge"
TIME_SOURCE = "WVImagettes ContentDate (whole seconds from the file name)"
SCENE_MAPPING = "parquet_product_footprint_is_one_scene"
SCENE_TIME_SOURCE = "product ContentDate"
COLUMNS = ["Name", "Id", "ContentDate", "GeoFootprint", "WVImagettes"]
# An IW, EW or SM scene needs no imagette list, so it can also be read from files without one.
# Attributes, when present, gives the catalogue's pass direction.
SCENE_COLUMNS = ["Name", "Id", "ContentDate", "GeoFootprint"]
OPTIONAL_SCENE_COLUMNS = ["Attributes"]
# A vertex this close to +-180 degrees lies on the dateline cut.
CUT_EDGE_TOLERANCE = 1e-6
# Largest latitude difference, in degrees, between the two ends of a cut shared by two halves.
CUT_LATITUDE_TOLERANCE = 1e-3
# The SAFE name each run reads, per mode and product type: L2 OCN for every mode, L1 SLC for WV.
# SM names carry the beam (S1 to S6) where the others carry the mode.
_PRODUCT_TOKENS = {"OCN": "OCN__2[A-Z]", "SLC": "SLC__1[A-Z]"}
PRODUCT_TYPES = {"WV": ("OCN", "SLC"), "IW": ("OCN",), "EW": ("OCN",), "SM": ("OCN",)}
# Archive level and folder suffix of each product type.
_ARCHIVE_FOLDER = {"OCN_": ("L2", "OCN__2S"), "SLC_": ("L1", "SLC__1S")}
# SAR archive layouts (recipe sar_archive_layout). mpc: the MPC Sentinel-1 cache,
# sentinel-1x/<L2|L1>/<mode>/<S1x>_<mode>_<OCN__2S|SLC__1S>/<year>/<doy>/<SAFE>. provider: the
# CERSAT provider tree of the ESA L2 products, which holds IW and EW OCN only,
# sentinel-1x/c-band_sar/s1x_<mode>_ocn__2s/<year>/<doy>/<SAFE>
# (root /legacy/project/cersat/public/provider/esa/satellite/l2).
ARCHIVE_LAYOUTS = ("mpc", "provider")
# A vertex this close (degrees) to +-180 or 0 is one the catalogue's cutting added; halves are
# sometimes cut at 179.99999 instead of 180.
SCENE_GRID_DEGREES = 1e-4


def _pattern(mode, product_type):
    """Return the regex a product name of this mode and type starts with."""
    if product_type not in PRODUCT_TYPES.get(mode, ()):
        raise ValueError(f"Unsupported SAR mode / product type {mode!r} / {product_type!r}")
    beam = "S[1-6]" if mode == "SM" else mode
    return re.compile(rf"S1[ABCD]_{beam}_{_PRODUCT_TOKENS[product_type]}")


def _loads(text):
    """Decode a JSON column value; also the Python-literal form older daily files use.

    Some older values are encoded twice (a JSON string holding a Python literal), so a decoded
    string is decoded again.
    """
    for _ in range(3):
        if not isinstance(text, str):
            break
        try:
            text = json.loads(text)
        except ValueError:
            try:
                text = ast.literal_eval(text)
            except (SyntaxError, ValueError) as exc:
                # Always a ValueError, which the callers treat as an unusable row.
                raise ValueError(f"Unreadable daily-file value: {text[:80]!r}") from exc
    return text


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


def _safe_folder(archive_root, name, product_type=None, layout="mpc"):
    """Return the archive folder of a SAFE, read from its name, in the given layout
    (ARCHIVE_LAYOUTS): mpc, sentinel-1x/<L2|L1>/<mode>/<S1x>_<mode>_<OCN__2S|SLC__1S>/<year>/<doy>/
    <name>; provider, sentinel-1x/c-band_sar/s1x_<mode>_ocn__2s/<year>/<doy>/<name>.

    product_type, when given, must be the name's own.
    """
    mission, mode = name[:3], name[4:6]
    mode = "SM" if mode[0] == "S" else mode
    level, suffix = _ARCHIVE_FOLDER[name[7:11]]
    if product_type is not None and product_type != name[7:10]:
        raise ValueError(f"{name} is not a {product_type} product")
    start = datetime.strptime(name.split("_")[5], "%Y%m%dT%H%M%S")
    if layout == "provider":
        if suffix != "OCN__2S":
            raise ValueError(f"The provider archive holds L2 OCN products only, not {name}")
        return (
            archive_root
            / ("sentinel-1" + mission[-1].lower())
            / "c-band_sar"
            / f"{mission}_{mode}_{suffix}".lower()
            / start.strftime("%Y/%j")
            / name
        )
    if layout != "mpc":
        raise ValueError(f"Unknown SAR archive layout {layout!r}")
    return (
        archive_root
        / ("sentinel-1" + mission[-1].lower())
        / level
        / mode
        / f"{mission}_{mode}_{suffix}"
        / start.strftime("%Y/%j")
        / name
    )


def _read_day(path, columns=COLUMNS, optional=()):
    """Return the rows of one daily parquet file as dicts, reading only the columns used."""
    names = pq.read_schema(path).names
    missing = [c for c in columns if c not in names]
    if missing:
        raise ValueError(f"Daily parquet file lacks the columns {missing}: {path}")
    return pq.read_table(path, columns=[*columns, *(c for c in optional if c in names)]).to_pylist()


def orbit_direction(row):
    """Return the catalogue pass direction of a row (ascending, descending) or None."""
    try:
        attributes = _loads(row.get("Attributes"))
        value = next(a["Value"] for a in attributes if a.get("Name") == "orbitDirection")
    except (ValueError, SyntaxError, TypeError, KeyError, StopIteration):
        return None
    value = str(value).lower()
    return value if value in ("ascending", "descending") else None


def scene_corners(rings):
    """Return the corners of a scene footprint as [lon, lat], longitudes continuous across 180.

    The catalogue cuts a footprint into pieces at the dateline, and adds vertices where a scene
    crosses longitude 0; those added vertices (on +-180 or 0 degrees) are dropped when more than
    four remain. The pieces are joined into one ring in cyclic order: each piece is turned to the
    first piece's orientation and starts just after its cut, so its own corners follow those of
    the piece before. Longitudes are unwrapped next to the first corner. The orientation and the
    first corner are not the catalogue's for a cut footprint; the angle model fixes both from the
    catalogue pass direction. Fails when what is left is not one valid polygon.
    """

    def on_cut(x):
        return abs(x / 180 - round(x / 180)) * 180 < SCENE_GRID_DEGREES

    pieces = [[(float(x), float(y)) for x, y in ring] for ring in rings]
    if sum(len(piece) for piece in pieces) > 4:
        sense = None
        joined = []
        for piece in pieces:
            # Orientation in a frame continuous around the piece's own first vertex.
            x0 = piece[0][0]
            local = [(x0 + ((x - x0 + 180) % 360 - 180), y) for x, y in piece]
            area = _signed_area(local)
            sense = sense if sense is not None else area > 0
            if (area > 0) != sense:
                piece = piece[::-1]
            cut = [on_cut(x) for x, _ in piece]
            if any(cut) and not all(cut):
                start = next(i for i in range(len(piece)) if not cut[i] and cut[i - 1])
                piece = piece[start:] + piece[:start]
            joined.extend(v for v in piece if not on_cut(v[0]))
        corners = joined if len(joined) >= 4 else [v for piece in pieces for v in piece]
    else:
        corners = pieces[0]
    lon0 = corners[0][0]
    unwrapped = []
    for x, y in corners:
        point = [lon0 + ((x - lon0 + 180) % 360 - 180), y]
        if not unwrapped or max(abs(point[0] - unwrapped[-1][0]), abs(y - unwrapped[-1][1])) > 1e-9:
            unwrapped.append(point)
    if len(unwrapped) > 1 and unwrapped[0] == unwrapped[-1]:
        unwrapped.pop()
    polygon = Polygon(unwrapped)
    if len(unwrapped) < 4 or not polygon.is_valid or polygon.area == 0:
        raise ValueError(f"Footprint pieces do not form one scene ({len(rings)} pieces)")
    return unwrapped


# Smallest latitude change (degrees) between two slices of a datatake that gives a direction.
SLICE_MOTION_DEGREES = 0.05


def slice_motion(slices):
    """Return the pass direction of each product from its neighbours in the same datatake.

    slices maps (mission, mode, datatake) to a list of (start token, name, centre latitude).
    Consecutive slices of one datatake follow the flight, so the footprint moves north on an
    ascending pass and south on a descending one. A product gets a direction only when its
    steps to the slices before and after (one is enough) agree and each exceeds
    SLICE_MOTION_DEGREES; near the turning latitude it gets none. On S1B IW/EW, January 2021,
    this direction matched the OCN files on all 2,569 products that had one; on S1A/S1C, April
    2025, on all but 4 of 1,188, where the catalogue orbitDirection was wrong on 82.

    Products with the same start (one slice published twice, under two checksums) are one
    slice: the steps are taken between distinct starts, at their mean latitude.
    """
    motion = {}
    for members in slices.values():
        slots = {}
        for start, name, lat in members:
            slots.setdefault(start, []).append((name, lat))
        order = sorted(slots)
        lats = [float(np.mean([lat for _, lat in slots[s]])) for s in order]
        for i, start in enumerate(order):
            steps = []
            if i > 0:
                steps.append(lats[i] - lats[i - 1])
            if i + 1 < len(order):
                steps.append(lats[i + 1] - lats[i])
            steps = [s for s in steps if abs(s) > SLICE_MOTION_DEGREES]
            if steps and all((s > 0) == (steps[0] > 0) for s in steps):
                for name, _ in slots[start]:
                    motion[name] = "ascending" if steps[0] > 0 else "descending"
    return motion


def _scene_measurement(files):
    """Return the scene's measurement file among a SAFE's measurement files, or None.

    A SAFE holds one <mission>-<mode>-ocn-... file, and recent IW ones also hold one OSW file per
    subswath (s1c-iw1-osw-..., iw2, iw3): the ocn file is the scene's.
    """
    ocn = [f for f in files if "-ocn-" in f]
    if len(ocn) == 1:
        return ocn[0]
    return files[0] if len(files) == 1 and not ocn else None


def _scene(row, archive_root, layout="mpc"):
    """Build the product record of one IW, EW or SM OCN row: one scene and its availability."""
    name = row["Name"]
    content = _loads(row["ContentDate"])
    _utc(content["Start"]), _utc(content["End"])
    rings = _rings(row["GeoFootprint"])
    product_id = "local_" + hashlib.sha256(name.encode()).hexdigest()
    corners = scene_corners(rings)
    # A cut at 179.99999 is put on 180 exactly, so the footprint's two pieces meet and the
    # matching joins them across the dateline as it does for an exact cut.
    rings = [
        [(math.copysign(180.0, x) if abs(abs(x) - 180) < SCENE_GRID_DEGREES else x, y)
         for x, y in ring]
        for ring in rings
    ]
    folder = _safe_folder(archive_root, name, layout=layout) if archive_root is not None else None
    files = (
        sorted(p.name for p in (folder / "measurement").glob("*.nc"))
        if folder is not None and (folder / "measurement").is_dir()
        else None
    )
    measurement = _scene_measurement(files) if files is not None else None
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
            else f"no single ocn file among the {len(files)} measurement files of the SAFE"
        )
        unavailable.append({"safe": name, "index": 1, "name": None, "reason": reason})
    return {
        "Id": product_id,
        "Name": name,
        "ContentDate": scene["ContentDate"],
        "GeoFootprint": scene["GeoFootprint"],
        # The corners in continuous longitudes, for the SAR angle model, and the catalogue pass
        # direction (replaced by the slices' motion when the datatake has neighbours).
        "SceneRing": corners,
        "OrbitDirection": orbit_direction(row),
        "Imagettes": [scene],
        "SkippedImagettes": [],
    }, unavailable


def _product(row, archive_root, product_type="OCN", layout="mpc"):
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
    folder = (
        _safe_folder(archive_root, name, product_type, layout)
        if archive_root is not None
        else None
    )
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
    product_type: str = "OCN",
    mode: str = "WV",
    archive_layout: str = "mpc",
) -> tuple[list[dict], dict]:
    """Read the daily parquet files of the covered days and return the products to match.

    mode is WV (one record per imagette) or IW, EW, SM (one record per product, the scene).
    product_type is OCN, or SLC for WV: a run reads one type and never mixes them.
    Products with no imagette list, and products whose polygons and imagettes disagree (or, for a
    scene, whose footprint is not one polygon), are left out and named in the returned
    provenance. Measurement files absent from the archive are kept, flagged unavailable and named
    there too. Coverage must already include the time tolerance.
    """
    if mode not in PRODUCT_TYPES:
        raise ValueError(f"Unsupported SAR mode {mode!r}")
    selected = _pattern(mode, product_type)
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
        "product_type": product_type,
        "sar_parquet_root": str(parquet_root),
        "sar_archive_root": None if sar_archive_root is None else str(sar_archive_root),
        "sar_archive_layout": archive_layout,
        "sar_mode": mode,
        "coverage_scope": "products listed in the selected daily parquet files",
        "safes_without_imagettes": [],
        "safes_skipped": [],
        "imagettes_unavailable": [],
    }
    products, seen, count = [], set(), 0
    # Every scene product read, grouped by datatake, for the pass direction from slice motion.
    slices = {}
    day = first.date() - timedelta(days=1)
    while day <= last.date():
        path = parquet_root / f"s1_{day.isoformat()}.parquet"
        provenance["scanned_files"].append(str(path))
        if not path.is_file():
            provenance["missing_files"].append(str(path))
        else:
            provenance["parquet_inputs"].append(file_identity(path))
            scene = mode != "WV"
            columns = SCENE_COLUMNS if scene else COLUMNS
            for row in _read_day(path, columns, OPTIONAL_SCENE_COLUMNS if scene else ()):
                name = row["Name"]
                if not selected.match(name) or name[:3] not in missions or name in seen:
                    continue
                seen.add(name)
                count += 1
                try:
                    content = _loads(row["ContentDate"])
                    overlaps = _utc(content["Start"]) < last and _utc(content["End"]) > first
                except (ValueError, KeyError, TypeError) as exc:
                    provenance["safes_skipped"].append({"safe": name, "reason": str(exc)})
                    continue
                if scene:
                    try:
                        latitude = centre(_rings(row["GeoFootprint"]))[1]
                        key = (name[:3], name[4:6], name.split("_")[-2])
                        slices.setdefault(key, []).append((name.split("_")[5], name, latitude))
                    except (ValueError, KeyError, TypeError, IndexError, GEOSException):
                        pass
                if not overlaps:
                    continue
                if mode != "WV":
                    try:
                        product, unavailable = _scene(row, sar_archive_root, archive_layout)
                    except (ValueError, KeyError, TypeError, GEOSException) as exc:
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
                    product, unavailable = _product(
                        row, sar_archive_root, product_type, archive_layout
                    )
                except (ValueError, KeyError, TypeError) as exc:
                    provenance["safes_skipped"].append({"safe": name, "reason": str(exc)})
                    continue
                provenance["imagettes_unavailable"].extend(unavailable)
                products.append(product)
        day += timedelta(days=1)
    if not provenance["parquet_inputs"]:
        raise ValueError(f"No daily parquet files found under: {parquet_root}")
    if mode != "WV":
        # The pass direction: from slice motion when the datatake has a neighbouring slice,
        # else the catalogue's orbitDirection. Disagreements are listed.
        motion = slice_motion(slices)
        # A slice with neighbours but no direction from them is at the turning latitude: its
        # along-track and cross-track edges cannot be told apart, and the run rejects it.
        turning = {
            name
            for members in slices.values()
            if len({start for start, _, _ in members}) > 1
            for _, name, _ in members
        } - set(motion)
        for product in products:
            product["Turning"] = product["Name"] in turning
        provenance["orbit_direction_from_slices"] = 0
        provenance["orbit_direction_corrected"] = []
        # Products whose direction is the catalogue's alone, which nothing checks.
        provenance["orbit_direction_from_catalogue"] = []
        for product in products:
            moved = motion.get(product["Name"])
            catalogue = product["OrbitDirection"]
            if moved is None:
                product["OrbitDirectionSource"] = "catalogue" if catalogue else None
                if catalogue:
                    provenance["orbit_direction_from_catalogue"].append(product["Name"])
                continue
            provenance["orbit_direction_from_slices"] += 1
            product["OrbitDirection"], product["OrbitDirectionSource"] = moved, "slice motion"
            if catalogue is not None and catalogue != moved:
                provenance["orbit_direction_corrected"].append(
                    {"safe": product["Name"], "catalogue": catalogue, "slice_motion": moved}
                )
    if mode == "SM" and count == 0:
        # A day without any SM acquisition is rare; files without SM products are the usual cause.
        provenance["warning"] = (
            "no SM product in the daily files read; daily_parquet_files_v2 does not list SM "
            "products yet (its producer drops them), so this is not a period without SM"
        )
        logger.warning("%s: %s", parquet_root, provenance["warning"])
    provenance["safes_found"] = count
    provenance["products_selected"] = len(products)
    # Everything left out or unusable is named in the manifest and summarised here at each run.
    for key, text in [
        ("safes_without_imagettes", "SAFEs without an imagette list"),
        ("safes_skipped", "SAFEs skipped (unusable footprint or imagette list)"),
        ("imagettes_unavailable", "imagettes whose file is not in the archive (IW/EW/SM: scenes)"),
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
