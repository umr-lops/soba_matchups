"""Local Level-2 WV OCN metadata, associated by native OSW cell centres."""

import hashlib
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

import netCDF4
import numpy as np
from shapely.affinity import translate
from shapely.geometry import MultiPolygon, Point, Polygon, box, mapping, shape
from shapely.ops import unary_union

from .output import file_identity

IMAGETTE_MAPPING_POLICY = "local_ocn_geolocation_to_manifest_frame"
OCN_HEADER_PRECISION_SECONDS = 1
_PATTERN = re.compile(
    r"S1[ABCD]_WV_(?P<producttype>OCN|SLC)__"
    r"(?P<level>[12])[A-Z](?P<pol>SV|SH)_"
    r"\d{8}T\d{6}_\d{8}T\d{6}_\d{6}_[0-9A-F]{6}_[0-9A-F]{4}\.SAFE"
)
_NC_PATTERN = re.compile(
    r"(s1[abcd])-wv[12]-ocn-(vv|hh)-(\d{8}t\d{6})-(\d{8}t\d{6})-"
    r"(\d{6})-([0-9a-f]{6})-(\d{3})\.nc"
)
_SAFE = "{http://www.esa.int/safe/sentinel-1.0}"
_GML = "{http://www.opengis.net/gml}"


def decode_safe_name(name):
    """Return polarization and product type of a WV SAFE name; reject any other name."""
    match = _PATTERN.fullmatch(name) if isinstance(name, str) else None
    if match is None or match["level"] != {"OCN": "2", "SLC": "1"}[match["producttype"]]:
        raise ValueError(f"Unsupported Sentinel-1 WV SAFE name: {name!r}")
    return {
        "polarization": {"SV": "VV", "SH": "HH"}[match["pol"]],
        "producttype": match["producttype"],
    }


def _utc(value, *, esa=False):
    """Parse an ISO8601 UTC timestamp. ESA manifests omit the zone, so esa=True assumes UTC."""
    if not isinstance(value, str) or "T" not in value:
        raise ValueError(f"Expected UTC ISO8601 timestamp: {value!r}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if esa and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed.utcoffset() != timedelta(0):
        raise ValueError(f"Expected UTC timestamp: {value!r}")
    return parsed


def _iso(value):
    """Format a UTC datetime with a trailing Z."""
    return value.isoformat().replace("+00:00", "Z")


def _objects(root):
    """Map each manifest metadataObject ID to its XML element."""
    result = {}
    for element in root.iter():
        if element.tag.split("}")[-1] == "metadataObject":
            identity = element.get("ID")
            if not identity or identity in result:
                raise ValueError("Missing/duplicate manifest metadata identity")
            result[identity] = element
    return result


def _text(element, tag):
    """Return the text of the one `tag` element below `element`; fail if absent or repeated."""
    values = [e.text.strip() for e in element.iter() if e.tag.split("}")[-1] == tag and e.text]
    if len(values) != 1:
        raise ValueError(f"Expected unique manifest {tag}")
    return values[0]


def _frames(root):
    """Parse the manifest frame set into GeoJSON polygons, one per imagette frame."""
    try:
        frame_set = _objects(root)["measurementFrameSet"]
    except KeyError as exc:
        raise ValueError("Missing measurementFrameSet") from exc
    frames = []
    for frame in frame_set.iter(_SAFE + "frame"):
        footprints = frame.findall(_SAFE + "footPrint")
        if len(footprints) != 1:
            raise ValueError("Expected one native footprint per frame")
        footprint = footprints[0]
        if "4326" not in footprint.get("srsName", ""):
            raise ValueError("Native frame must use WGS84 EPSG4326 lat/lon")
        coords = footprint.findall(_GML + "coordinates")
        if len(coords) != 1 or not coords[0].text:
            raise ValueError("Missing frame coordinates")
        ring = []
        for pair in coords[0].text.split():
            # Manifest coordinates are written as "lat,lon".
            lat, lon = map(float, pair.split(","))
            if not (
                math.isfinite(lon)
                and math.isfinite(lat)
                and -180 <= lon <= 180
                and -90 <= lat <= 90
            ):
                raise ValueError("Nonfinite/out-of-range frame coordinates")
            # Unwrap longitudes so a frame crossing the dateline stays contiguous.
            if ring:
                while lon - ring[-1][0] > 180:
                    lon -= 360
                while lon - ring[-1][0] < -180:
                    lon += 360
            ring.append((lon, lat))
        # Close the ring if the file leaves it open.
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        polygon = Polygon(ring)
        if (
            len(ring) != 5
            or not polygon.is_valid
            or polygon.is_empty
            or polygon.area == 0
            or polygon.bounds[2] - polygon.bounds[0] >= 180
        ):
            raise ValueError("Invalid native four-corner frame")
        # Cut at +/-180 so a dateline frame becomes a MultiPolygon of the same frame.
        pieces = []
        for shift in (-360, 0, 360):
            part = translate(polygon, xoff=shift).intersection(box(-180, -90, 180, 90))
            if part.geom_type == "Polygon" and part.area > 0:
                pieces.append(part)
        if not pieces:
            raise ValueError("Invalid WGS84 frame")
        frames.append(mapping(pieces[0] if len(pieces) == 1 else MultiPolygon(pieces)))
    if not frames:
        raise ValueError("No native manifest frames")
    return frames


def read_manifest_frames(path: Path) -> list[dict]:
    """Read native frames only, including L1 structural reference manifests."""
    return _frames(ET.parse(path).getroot())


def _manifest(root, name, mission):
    """Check the manifest agrees with the SAFE name; return (name info, start, stop)."""
    info = decode_safe_name(name)
    if info["producttype"] != "OCN" or name[:3] != mission:
        raise ValueError("Production discovery requires requested mission L2 WV OCN SAFE")
    objects = _objects(root)
    try:
        platform = objects["platform"]
        general = objects["generalProductInformation"]
        period = objects["acquisitionPeriod"]
    except KeyError as exc:
        raise ValueError(f"Missing manifest metadata identity: {exc}") from exc
    family = [e.text for e in platform.iter(_SAFE + "familyName")]
    if (
        not family
        or family[0] != "SENTINEL-1"
        or _text(platform, "number") != mission[-1]
        or _text(platform, "mode") != "WV"
    ):
        raise ValueError("Manifest platform/mission/mode mismatch")
    if (
        _text(general, "productType") != "OCN"
        or _text(general, "transmitterReceiverPolarisation") != info["polarization"]
    ):
        raise ValueError("Manifest product/polarization mismatch")
    # SAFE name fields: parts[5:7] start/stop, parts[7] absolute orbit, parts[8] datatake ID.
    parts = name.split("_")
    first, last = (_utc(_text(period, key), esa=True) for key in ("startTime", "stopTime"))
    if not timedelta(0) < last - first < timedelta(days=1):
        raise ValueError("Manifest WV acquisition must increase and last less than one day")
    for native, token in zip((first, last), parts[5:7]):
        named = datetime.strptime(token, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        if abs((native - named).total_seconds()) > 2:
            raise ValueError("SAFE filename/manifest acquisition mismatch")
    # Datatake and orbit are cross-checked only when the manifest carries them.
    expected_datatake = int(parts[8], 16)
    values = [e.text for e in root.iter() if e.tag.split("}")[-1] == "missionDataTakeID"]
    if values and any(int(v) != expected_datatake for v in values):
        raise ValueError(f"Manifest missionDataTakeID/SAFE identity mismatch: {name}")
    orbits = [e for e in root.iter() if e.tag.split("}")[-1] == "orbitNumber"]
    if orbits:
        starts = [int(e.text) for e in orbits if e.get("type") == "start"]
        stops = [int(e.text) for e in orbits if e.get("type") == "stop"]
        if len(orbits) != 2 or len(starts) != 1 or len(stops) != 1:
            raise ValueError(f"Manifest requires unique start/stop orbit numbers: {name}")
        # The filename identifies the starting orbit; an acquisition can cross the orbit boundary.
        if starts[0] != int(parts[7]) or stops[0] < starts[0]:
            raise ValueError(
                f"Manifest orbitNumber/SAFE identity mismatch: {name}; "
                f"filename={int(parts[7])}, start={starts[0]}, stop={stops[0]}"
            )
    return info, first, last


def _measurements(root, safe):
    """Return the measurement NetCDF paths the manifest lists, checked against the disk."""
    # Map each dataObject ID to a file path that must stay inside the SAFE directory.
    objects, paths = {}, set()
    for element in root.iter():
        if element.tag.split("}")[-1] != "dataObject":
            continue
        identity = element.get("ID")
        if not identity or identity in objects:
            raise ValueError("Missing/duplicate dataObject identity")
        locations = [e for e in element.iter() if e.tag.split("}")[-1] == "fileLocation"]
        if len(locations) != 1:
            raise ValueError("Expected unique dataObject file reference")
        href = locations[0].get("href", "")
        relative = PurePosixPath(href)
        if (
            not href
            or "\\" in href
            or ":" in href
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            raise ValueError(f"Unsafe native file reference: {href}")
        path = (safe / str(relative)).resolve()
        if not path.is_relative_to(safe.resolve()):
            raise ValueError(f"Native file escapes SAFE: {href}")
        if path in paths:
            raise ValueError(f"Duplicate native file reference: {href}")
        paths.add(path)
        objects[identity] = path
    # Measurement data units point at the files that hold the imagettes.
    references = []
    for unit in root.iter():
        if unit.attrib.get("unitType", "").replace(" ", "") != "MeasurementDataUnit":
            continue
        pointers = [
            e.get("dataObjectID")
            for e in unit.iter()
            if e.tag.split("}")[-1] == "dataObjectPointer"
        ]
        if len(pointers) != 1 or pointers[0] not in objects:
            raise ValueError("Invalid measurement dataObjectPointer")
        references.append(objects[pointers[0]])
    # The listed files, the measurement/ folder and the data units must all agree.
    native = {p for p in paths if p.parent == (safe / "measurement").resolve()}
    if not references or len(set(references)) != len(references) or set(references) != native:
        raise ValueError("Duplicate/missing native measurement references")
    if any(not p.is_file() for p in references):
        raise ValueError("Missing native measurement file")
    actual = {p.resolve() for p in (safe / "measurement").glob("*.nc")}
    if actual != set(references):
        raise ValueError("Unlisted or missing native OCN measurement files")
    return sorted(references)


def _stat(path):
    """Cheap file identity (path, size, mtime) used to spot changes to large NetCDF files."""
    stat = path.stat()
    return {"path": str(path.resolve()), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _imagettes(root, safe, product, info, first, last, frames):
    """Build one record per measurement NetCDF, tied to its own manifest frame.

    Returns the records and the files skipped: those with no cell centres, and those whose
    centres lie in a frame that belongs to another imagette.
    """
    paths = _measurements(root, safe)
    if len(paths) != len(frames):
        raise ValueError("Native measurement/frame count mismatch")
    claims, skipped, indices = {}, [], set()
    parts = safe.name.split("_")
    for path in paths:
        match = _NC_PATTERN.fullmatch(path.name)
        if match is None:
            raise ValueError(f"Unsupported OCN WV filename: {path.name}")
        mission, pol, start, end, orbit, datatake, index = match.groups()
        if (mission, pol, orbit, datatake) != (
            parts[0].lower(),
            info["polarization"].lower(),
            parts[7],
            parts[8].lower(),
        ):
            raise ValueError(f"Measurement/SAFE identity mismatch: {path.name}")
        index = int(index)
        if index < 1 or index in indices:
            raise ValueError("Duplicate/invalid numeric measurement identifier")
        indices.add(index)
        identity = _stat(path)
        # Read only global times and OSW cell centres, never the wind or spectra arrays.
        with netCDF4.Dataset(path, "r") as dataset:
            times = [
                _utc(dataset.getncattr(key), esa=True)
                for key in ("firstMeasurementTime", "lastMeasurementTime")
            ]
            lon, lat = [
                np.ma.asarray(dataset.variables[key][:]).filled(np.nan)
                for key in ("oswLon", "oswLat")
            ]
        # The file must not change while it is being read.
        if identity != _stat(path):
            raise ValueError(f"OCN metadata input changed during read: {path}")
        # Some OCN headers quantize to whole seconds while the manifest retains microseconds.
        earliest = first.replace(microsecond=0) if times[0].microsecond == 0 else first
        latest = last
        if times[1].microsecond == 0 and last.microsecond:
            latest = last.replace(microsecond=0) + timedelta(seconds=1)
        if not earliest <= times[0] < times[1] <= latest:
            raise ValueError(
                f"Measurement times outside manifest acquisition: {path}; "
                f"NC={list(map(_iso, times))}, manifest={[_iso(first), _iso(last)]}"
            )
        # Filename timestamps must match the NetCDF global times to within 2 s.
        for native_time, token in zip(times, (start, end)):
            named = datetime.strptime(token, "%Y%m%dt%H%M%S").replace(tzinfo=timezone.utc)
            if abs((native_time - named).total_seconds()) > 2:
                raise ValueError("Measurement filename/global time mismatch")
        if lon.shape != lat.shape:
            raise ValueError("OSW longitude/latitude shape mismatch")
        valid = np.isfinite(lon) & np.isfinite(lat)
        # An imagette without any cell centre has no position to place it, so it is skipped
        # and listed; the rest of the SAFE is still used.
        if not valid.any():
            skipped.append(
                {
                    "Name": path.name,
                    "index": index,
                    "reason": "no finite OSW cell centres",
                    "metadata_inputs": [identity],
                }
            )
            continue
        centres = list(zip(lon[valid].ravel(), lat[valid].ravel()))
        if any(not (-180 <= x <= 180 and -90 <= y <= 90) for x, y in centres):
            raise ValueError("Out-of-range OSW centres")
        # The one frame covering every OSW centre is this imagette's footprint (a centre at
        # exactly +/-180 also matches the opposite side of the dateline). File order is never
        # used to find it; two files that claim one frame are settled after the loop.
        matches = [
            i
            for i, frame in enumerate(frames)
            if all(
                shape(frame).covers(Point(x, y))
                or (abs(x) == 180 and shape(frame).covers(Point(-x, y)))
                for x, y in centres
            )
        ]
        if len(matches) != 1:
            raise ValueError("OSW centres require unique one-to-one manifest frame association")
        relative = path.relative_to(safe.resolve()).as_posix()
        claims.setdefault(matches[0], []).append(
            {
                "Id": "local_"
                + hashlib.sha256((product["Id"] + "/" + relative).encode()).hexdigest(),
                "Name": path.name,
                "index": index,
                "ContentDate": dict(zip(("Start", "End"), map(_iso, times))),
                "GeoFootprint": frames[matches[0]],
                "mapping_policy": IMAGETTE_MAPPING_POLICY,
                "href": path.as_uri(),
                "metadata_inputs": [identity],
            }
        )
    rows = []
    for frame_index, claimed in claims.items():
        if len(claimed) == 1:
            rows.append(claimed[0])
            continue
        # Some files carry the cell centre of another imagette (seen 6 numbers apart). Only the
        # file numbered like the frame is kept; with no such file the run stops.
        owners = [row for row in claimed if row["index"] == frame_index + 1]
        if len(owners) != 1:
            raise ValueError("OSW centres require unique one-to-one manifest frame association")
        rows.append(owners[0])
        for row in claimed:
            if row is not owners[0]:
                skipped.append(
                    {
                        "Name": row["Name"],
                        "index": row["index"],
                        "reason": f"OSW centres lie in the frame of imagette {owners[0]['index']}",
                        "metadata_inputs": row["metadata_inputs"],
                    }
                )
    return sorted(rows, key=lambda row: row["index"]), sorted(skipped, key=lambda s: s["index"])


def discover_local(
    start: str, end: str, missions: tuple[str, ...], sar_archive_root: Path
) -> tuple[list[dict], dict]:
    """Scan only requested missions and inclusive UTC year/DOY coverage days.

    Coverage must already include the caller's scientific time-window expansion.
    Native manifest acquisition overlap is tested before opening any NetCDF file.
    """
    timestamp = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z"
    if any(not isinstance(v, str) or not re.fullmatch(timestamp, v) for v in (start, end)):
        raise ValueError("start/end must be expanded UTC ISO8601 Z timestamps")
    first, last = _utc(start), _utc(end)
    if first >= last:
        raise ValueError("start must precede end")
    if (
        not isinstance(missions, tuple)
        or not missions
        or any(m not in {"S1A", "S1B", "S1C", "S1D"} for m in missions)
    ):
        raise ValueError("missions must be a nonempty tuple of S1A/S1B/S1C/S1D")
    archive_root = Path(sar_archive_root).resolve()
    provenance = {
        "source": "local_ocn",
        "manifest_inputs": [],
        "ocn_inputs": [],
        "scanned_dirs": [],
        "missing_dirs": [],
        "coverage": {"start": start, "end": end},
        "missions": sorted(set(missions)),
        "sar_archive_root": str(archive_root),
        "coverage_scope": "files present in selected local archive directories",
    }
    products, seen = [], set()
    count = 0
    for mission in sorted(set(missions)):
        # WV SAFEs can start on the previous UTC day and continue past midnight.
        day = first.date() - timedelta(days=1)
        while day <= last.date():
            directory = (
                archive_root
                / ("sentinel-1" + mission[-1].lower())
                / "L2"
                / "WV"
                / (mission + "_WV_OCN__2S")
                / day.strftime("%Y/%j")
            )
            provenance["scanned_dirs"].append(str(directory))
            if not directory.is_dir():
                provenance["missing_dirs"].append(str(directory))
            else:
                for safe in sorted(directory.glob("*.SAFE")):
                    count += 1
                    if safe.name in seen:
                        raise ValueError(f"Duplicate SAFE name: {safe.name}")
                    seen.add(safe.name)
                    manifest = safe / "manifest.safe"
                    # Hash before and after parsing to catch a file modified mid-read.
                    identity = file_identity(manifest)
                    root = ET.parse(manifest).getroot()
                    if file_identity(manifest) != identity:
                        raise ValueError(f"Manifest changed during read: {manifest}")
                    provenance["manifest_inputs"].append(identity)
                    info, acquisition_start, acquisition_end = _manifest(root, safe.name, mission)
                    # Cheap manifest test first; NetCDF files are opened only for overlapping SAFEs.
                    if not (acquisition_start < last and acquisition_end > first):
                        continue
                    frames = _frames(root)
                    product = {
                        "Id": "local_" + hashlib.sha256(safe.name.encode()).hexdigest(),
                        "Name": safe.name,
                        "ContentDate": {
                            "Start": _iso(acquisition_start),
                            "End": _iso(acquisition_end),
                        },
                        "GeoFootprint": mapping(unary_union([shape(frame) for frame in frames])),
                        "manifest_identity": identity,
                    }
                    try:
                        product["Imagettes"], product["SkippedImagettes"] = _imagettes(
                            root, safe, product, info, acquisition_start, acquisition_end, frames
                        )
                    except (KeyError, AttributeError, OSError) as exc:
                        raise ValueError(f"Invalid local OCN metadata {safe}: {exc}") from exc
                    provenance["ocn_inputs"].extend(
                        row["metadata_inputs"][0]
                        for row in product["Imagettes"] + product["SkippedImagettes"]
                    )
                    products.append(product)
            day += timedelta(days=1)
    if count == 0:
        raise ValueError(f"No SAFEs found under searched local OCN archive root: {archive_root}")
    provenance["safes_found"] = count
    provenance["products_selected"] = len(products)
    return sorted(products, key=lambda p: (p["ContentDate"]["Start"], p["Name"])), provenance


def verify_local_inputs(provenance: dict) -> None:
    """Fail closed when any manifest hash or NetCDF size/mtime changed."""
    if provenance.get("source") != "local_ocn":
        raise ValueError("Expected local_ocn provenance")
    for kind, reader in [("manifest_inputs", file_identity), ("ocn_inputs", _stat)]:
        for identity in provenance[kind]:
            try:
                unchanged = reader(Path(identity["path"])) == identity
            except OSError as exc:
                raise ValueError(f"Local input changed or missing: {identity['path']}") from exc
            if not unchanged:
                raise ValueError(f"Local input changed: {identity['path']}")
