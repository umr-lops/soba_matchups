"""Build STAC Items and atomically publish complete local runs."""

import hashlib
import json
from pathlib import Path
import re
import tempfile

import numpy as np
from shapely.geometry import shape

from soba_matchups import __version__


def file_identity(path):
    path = Path(path).resolve()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "sha256": digest.hexdigest(), "bytes": path.stat().st_size}


def imagette_item(product, imagette, fields, groups, sea_percent, tolerance):
    assets = {
        "sar": {
            "href": imagette["href"],
            "type": "application/x-netcdf",
            "roles": ["data"],
            "title": imagette["Name"],
        }
    }
    if "manifest_identity" in product:
        assets["sar_manifest"] = {
            "href": Path(product["manifest_identity"]["path"]).as_uri(),
            "type": "application/xml",
            "roles": ["metadata"],
            "soba:sha256": product["manifest_identity"]["sha256"],
        }
    start = np.datetime64(imagette["ContentDate"]["Start"].removesuffix("Z"), "ns").astype("int64")
    end = np.datetime64(imagette["ContentDate"]["End"].removesuffix("Z"), "ns").astype("int64")
    midpoint = start + (end - start) // 2
    differences = []
    matchups = []
    for i, group in enumerate(groups, 1):
        key = f"reference_{i:03d}"
        assets[key] = {
            "href": Path(group["reference_path"]).as_uri(),
            "type": (
                "application/gzip"
                if group["reference_file"].endswith(".gz")
                else "application/x-netcdf"
            ),
            "roles": ["data"],
            "title": group["reference_file"],
            "soba:sha256": group["reference_sha256"],
        }
        points = []
        for point in group["points"]:
            observed = np.datetime64(point["ref_time_utc"].removesuffix("Z"), "ns").astype("int64")
            difference = float((observed - midpoint) / 60e9)
            differences.append(difference)
            points.append({**point, "delta_diff_time_minutes": difference})
        matchups.append(
            {
                "reference_asset": key,
                "reference_file": group["reference_file"],
                "sarmatchup:other_id": group["reference_file"],
                "reference_geometry": {
                    "type": "MultiPoint",
                    "coordinates": [[p["ref_lon"], p["ref_lat"]] for p in points],
                },
                "points": points,
            }
        )
    return {
        "stac_version": "1.0.0",
        "stac_extensions": [],
        "type": "Feature",
        "id": imagette["Id"],
        "geometry": imagette["GeoFootprint"],
        "bbox": list(shape(imagette["GeoFootprint"]).bounds),
        "properties": {
            "datetime": None,
            "start_datetime": imagette["ContentDate"]["Start"],
            "end_datetime": imagette["ContentDate"]["End"],
            "sarmatchup:sar_platform": "sentinel-1" + product["Name"][2].lower(),
            "sarmatchup:sar_instrument_mode": "WV",
            "sarmatchup:sar_product_type": fields["producttype"],
            "sarmatchup:sar_safe": product["Name"],
            "sarmatchup:other_instrument": "HY-2B HSCAT",
            "sarmatchup:other_type": "scatterometer",
            "sarmatchup:other_geometry_type": "MultiPoint",
            "sarmatchup:searching_window_hours": tolerance / 60,
            "sarmatchup:lib_version": __version__,
            "sarmatchup:n_coloc_points": len(differences),
            "sarmatchup:delta_diff_time_minutes_min": min(differences),
            "sarmatchup:delta_diff_time_minutes_max": max(differences),
            "soba:safe_id": product["Id"],
            "soba:imagette_index": imagette["index"],
            "soba:measurement_name": imagette["Name"],
            "soba:polarization": fields["polarization"],
            "soba:sea_percent": sea_percent,
            "soba:mapping_policy": imagette["mapping_policy"],
            "soba:safe_id_source": "SHA256 of SAFE directory name; not a catalogue UUID",
            "soba:time_source": "OCN firstMeasurementTime / lastMeasurementTime UTC attributes",
            "soba:time_tolerance_minutes": tolerance,
            "soba:matchups": matchups,
        },
        "assets": assets,
        "links": [],
    }


def publish(output_dir, items, diagnostics, products, manifest):
    output_dir = Path(output_dir)
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError(f"Output already exists; choose a fresh directory: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".soba-", dir=output_dir.parent) as temp:
        stage = Path(temp) / "result"
        (stage / "items").mkdir(parents=True)
        artifacts, item_ids, point_ids, filenames = [], set(), set(), set()
        for item in items:
            identity = item["id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", identity) or identity in item_ids:
                raise ValueError("Unsafe or duplicate STAC Item identity")
            item_ids.add(identity)
            for group in item["properties"]["soba:matchups"]:
                for point in group["points"]:
                    if point["record_id"] in point_ids:
                        raise ValueError("Conflicting duplicate matchup identities")
                    point_ids.add(point["record_id"])
            props = item["properties"]
            stem = props["sarmatchup:sar_safe"].removesuffix(".SAFE")
            filename = f"{stem}__IMG{props['soba:imagette_index']:03d}__HY2B_HSCAT"
            if not re.fullmatch(r"[A-Za-z0-9_-]+", filename) or filename in filenames:
                raise ValueError("Unsafe or duplicate STAC filename")
            filenames.add(filename)
            relative = f"items/{filename}.json"
            target = stage / relative
            target.write_text(json.dumps(item, indent=2, allow_nan=False) + "\n")
            if json.loads(target.read_text()) != json.loads(json.dumps(item, allow_nan=False)):
                raise ValueError(f"Failed STAC Item readback: {identity}")
            artifacts.append(relative)
        if (
            len(items) != manifest["stac_items"]
            or len(point_ids) != manifest["matchup_imagette_cell_rows"]
        ):
            raise ValueError("Manifest matchup counts disagree with STAC Items")
        for name, value in [
            ("diagnostics.json", diagnostics),
            ("sar_metadata.json", {"value": products}),
        ]:
            target = stage / name
            target.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
            if json.loads(target.read_text()) != json.loads(json.dumps(value, allow_nan=False)):
                raise ValueError(f"Failed artifact readback: {name}")
            artifacts.append(name)
        manifest["artifacts"] = [
            {**file_identity(stage / name), "path": name} for name in artifacts
        ]
        target = stage / "manifest.json"
        target.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
        if json.loads(target.read_text()) != json.loads(json.dumps(manifest, allow_nan=False)):
            raise ValueError("Manifest readback failed")
        # One local-filesystem rename publishes only a complete run.
        stage.rename(output_dir)
    return manifest
