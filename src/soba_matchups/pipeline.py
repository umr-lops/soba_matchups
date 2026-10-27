"""HSCAT coverage -> SAFE candidates -> imagette matching -> STAC publication."""

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import numpy as np

from soba_matchups import __version__
from soba_matchups.adapters import scat
from soba_matchups.land_fraction import LandMask
from soba_matchups.output import file_identity, imagette_item, publish
from soba_matchups.sar_metadata import decode_safe_name, discover_local, verify_local_inputs


def reference_inputs(recipe):
    paths = []
    day = recipe.start_date
    while day < recipe.stop_date:
        directory = (
            recipe.reference_root
            / recipe.met
            / recipe.scat
            / recipe.resolution
            / "data"
            / str(day.year)
            / day.strftime("%j")
        )
        selected = sorted([*directory.glob("*.nc"), *directory.glob("*.nc.gz")])
        if not selected:
            raise ValueError(f"No reference files for requested day: {directory}")
        paths.extend(selected)
        day += timedelta(days=1)
    if len({p.name for p in paths}) != len(paths):
        raise ValueError("Repeated reference filenames; select one unambiguous product bundle")
    references = []
    for path in paths:
        identity = file_identity(path)
        ref = scat.read_reference(path, recipe.reference_version)
        if not recipe.start_date <= ref["granule_start_day"] < recipe.stop_date:
            raise ValueError(f"Granule start day outside requested range: {path}")
        ref["identity"] = identity
        references.append(ref)
    return references


def run(recipe):
    if recipe.output_dir.exists() or recipe.output_dir.is_symlink():
        raise FileExistsError(
            f"Output already exists; choose a fresh directory: {recipe.output_dir}"
        )
    refs = reference_inputs(recipe)
    land = LandMask(recipe.land_mask)
    delta = timedelta(minutes=recipe.time_tolerance_minutes)

    def iso(t):
        return t.isoformat().replace("+00:00", "Z")

    start = iso(min(ref["coverage_start"] for ref in refs) - delta)
    end = iso(max(ref["coverage_end"] for ref in refs) + delta)
    products, discovery = discover_local(start, end, recipe.sar_missions, recipe.sar_archive_root)
    items, diagnostics = [], []
    seen_points = set()
    imagettes_processed = duplicate_points = safes_without_candidates = 0
    candidate_cells = 0

    def number(x):
        return float(x) if np.isfinite(x) else None

    for product in products:
        fields = decode_safe_name(product["Name"])
        if fields["producttype"] != "OCN":
            raise ValueError("SAR product type does not match the WV OCN discovery request")
        candidates = [
            (ref, scat.match_cells(product, ref, recipe.time_tolerance_minutes)) for ref in refs
        ]
        candidate_cells += sum(len(indices) for _, indices in candidates)
        if not any(len(indices) for _, indices in candidates):
            safes_without_candidates += 1
            continue
        for imagette in product["Imagettes"]:
            imagettes_processed += 1
            groups = []
            for ref, safe_indices in candidates:
                if not len(safe_indices):
                    continue
                subset = {
                    key: ref[key][safe_indices]
                    for key in ["lon", "lat", "time", "wind_speed", "wind_dir"]
                }
                indices = safe_indices[
                    scat.match_cells(imagette, subset, recipe.time_tolerance_minutes)
                ]
                points = []
                for i in indices:
                    flat = int(ref["flat"][i])
                    row_index, cell_index = divmod(flat, ref["columns"])
                    token = f"{imagette['Id']}|{ref['identity']['sha256']}|{flat}"
                    identity = hashlib.sha256(token.encode()).hexdigest()
                    if identity in seen_points:
                        duplicate_points += 1
                        continue
                    seen_points.add(identity)
                    points.append(
                        {
                            "record_id": identity,
                            "ref_row_0based": row_index,
                            "ref_cell_0based": cell_index,
                            "ref_time_utc": str(ref["time"][i].astype("datetime64[ns]")) + "Z",
                            "ref_lon": float((ref["lon"][i] + 180) % 360 - 180),
                            "ref_lat": float(ref["lat"][i]),
                            "wind_speed": float(ref["wind_speed"][i]),
                            "wind_direction_to": float(ref["wind_dir"][i]),
                            "quality_flag": number(ref["wvc_quality_flag"][i]),
                            "ice_probability": number(ref["ice_prob"][i]),
                        }
                    )
                diagnostics.append(
                    {
                        "safe_id": product["Id"],
                        "imagette_id": imagette["Id"],
                        "reference_file": ref["path"].name,
                        "matched_cells": len(indices),
                        "retained_cells": len(points),
                    }
                )
                if points:
                    groups.append(
                        {
                            "reference_file": ref["path"].name,
                            "reference_path": str(ref["path"]),
                            "reference_sha256": ref["identity"]["sha256"],
                            "points": points,
                        }
                    )
            if groups:
                items.append(
                    imagette_item(
                        product,
                        imagette,
                        fields,
                        groups,
                        land.sea_percent(imagette["GeoFootprint"]),
                        recipe.time_tolerance_minutes,
                    )
                )
    verify_local_inputs(discovery)
    land.verify_unchanged()
    for ref in refs:
        if file_identity(ref["path"]) != ref["identity"]:
            raise ValueError(f"Input changed during processing: {ref['path']}")
    manifest = {
        "schema_version": 6,
        "status": "success" if items else "no_matches",
        "created_utc": iso(datetime.now(timezone.utc)),
        "library": "soba_matchups",
        "library_version": __version__,
        "resolved_recipe": recipe.resolved(),
        "recipe_input": file_identity(recipe.path),
        "discovery": discovery,
        "reference_coverage": [
            {
                "file": ref["path"].name,
                "header_start": iso(ref["header_start"]),
                "header_end": iso(ref["header_end"]),
                "discovery_start": iso(ref["coverage_start"]),
                "discovery_end": iso(ref["coverage_end"]),
            }
            for ref in refs
        ],
        "reference_inputs": [ref["identity"] for ref in refs],
        "land_mask": {
            **land.provenance,
            "source": recipe.land_mask_source,
            "footprint_scope": "individual imagette",
            "used_as_filter": False,
        },
        "reference_files": len(refs),
        "reference_finite_wind_cells": sum(len(ref["flat"]) for ref in refs),
        "sar_products_processed": len(products),
        "safes_without_candidate_cells": safes_without_candidates,
        "safe_candidate_cell_rows": candidate_cells,
        "imagettes_processed": imagettes_processed,
        "stac_items": len(items),
        "matchup_imagette_cell_rows": len(seen_points),
        "duplicate_associations_discarded": duplicate_points,
        "matched_safes": len({i["properties"]["soba:safe_id"] for i in items}),
        "positive_imagette_reference_file_pairs": sum(d["retained_cells"] > 0 for d in diagnostics),
        "source_code": [file_identity(p) for p in sorted(Path(__file__).parent.rglob("*.py"))],
        "rules": {
            "policy": "scat_local_ocn_imagette_v1",
            "imagette_mapping": "local_ocn_geolocation_to_manifest_frame",
            "space": "strict individual imagette footprint point containment",
            "time": "strict imagette start - tolerance < cell time < imagette end + tolerance",
            "time_source": "OCN firstMeasurementTime / lastMeasurementTime UTC attributes",
            "time_tolerance_minutes": recipe.time_tolerance_minutes,
            "retention": "all finite wind vectors; retain quality/ice flags without filtering",
        },
        "units": {
            "sea_percent": "percent of individual imagette footprint outside the land mask",
            "wind_speed": "m s-1",
            "wind_direction_to": "degree, flowing toward",
            "ref_lon": "degrees_east, [-180,180)",
            "ref_lat": "degrees_north",
        },
        "limitations": [
            "Requires extracted L2 OCN SAFEs with manifest.safe and all listed measurement files.",
            "Missing/ambiguous native frame associations fail without suffix fallback.",
            "Reads OSW geolocation and UTC attributes, not wind/spectral arrays; no network.",
            "Archive inventory only; missing mission/day directories are listed in discovery.",
            "SAR file identities use size/mtime; manifests and references use SHA256.",
            "soba:* properties are project-specific, not a registered STAC extension.",
            "No sea-percent/polar prefilter or downstream quality/ice filter.",
            "Tolerance is explicitly configured, not scientifically calibrated.",
            "One Item per matched imagette per fresh run; no cross-run update or enrichment.",
        ],
    }
    return publish(recipe.output_dir, items, diagnostics, products, manifest)
