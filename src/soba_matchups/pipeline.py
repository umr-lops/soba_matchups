"""Bounded HSCAT worker: reference coverage -> SAR discovery -> match -> publish."""

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import numpy as np

from soba_matchups import __version__
from soba_matchups.adapters.scat import match_cells, read_reference
from soba_matchups.manifest import file_identity
from soba_matchups.output import publish
from soba_matchups.sar_metadata import discover


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
    if not paths:
        raise ValueError(f"No reference .nc or .nc.gz files in {recipe.reference_root}")
    if len({p.name for p in paths}) != len(paths):
        raise ValueError("Repeated reference filenames; select one unambiguous product bundle")
    references = []
    for path in paths:
        identity = file_identity(path)
        ref = read_reference(path, recipe.reference_version)
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
    delta = timedelta(minutes=recipe.time_tolerance_minutes)

    def iso(t):
        return t.isoformat().replace("+00:00", "Z")

    start = iso(min(ref["coverage_start"] for ref in refs) - delta)
    end = iso(max(ref["coverage_end"] for ref in refs) + delta)
    products, discovery = discover(
        start, end, recipe.sar_missions, recipe.sar_cache_dir, offline=recipe.offline
    )
    rows, diagnostics = [], []

    def number(x):
        return float(x) if np.isfinite(x) else None

    for product in products:
        for ref in refs:
            indices = match_cells(product, ref, recipe.time_tolerance_minutes)
            diagnostics.append(
                {
                    "safe_id": product["Id"],
                    "reference_file": ref["path"].name,
                    "matched_cells": len(indices),
                }
            )
            for i in indices:
                flat = int(ref["flat"][i])
                row_index, cell_index = divmod(flat, ref["columns"])
                token = f"{product['Id']}|{ref['identity']['sha256']}|{flat}"
                rows.append(
                    {
                        "record_id": hashlib.sha256(token.encode()).hexdigest(),
                        "safe_id": product["Id"],
                        "safe_name": product["Name"],
                        "mission": product["Name"][:3],
                        "safe_start_utc": product["ContentDate"]["Start"],
                        "safe_end_utc": product["ContentDate"]["End"],
                        "reference_file": ref["path"].name,
                        "reference_path": str(ref["path"]),
                        "reference_sha256": ref["identity"]["sha256"],
                        "ref_row_0based": row_index,
                        "ref_cell_0based": cell_index,
                        "ref_time_utc": str(ref["time"][i].astype("datetime64[ns]")) + "Z",
                        "ref_lon": float((ref["lon"][i] + 180) % 360 - 180),
                        "ref_lat": float(ref["lat"][i]),
                        "wind_speed": float(ref["wind_speed"][i]),
                        "wind_direction_to": float(ref["wind_dir"][i]),
                        "quality_flag": number(ref["wvc_quality_flag"][i]),
                        "ice_probability": number(ref["ice_prob"][i]),
                        "matching_policy": "scat_safe_v1",
                    }
                )
    for ref in refs:
        if file_identity(ref["path"]) != ref["identity"]:
            raise ValueError(f"Input changed during processing: {ref['path']}")
    identities = [ref["identity"] for ref in refs]
    manifest = {
        "schema_version": 1,
        "status": "success" if rows else "no_matches",
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
        "reference_inputs": identities,
        "reference_files": len(refs),
        "reference_finite_wind_cells": sum(len(ref["flat"]) for ref in refs),
        "sar_products_processed": len(products),
        "matchup_safe_cell_rows": len(rows),
        "matched_safes": len({r["safe_id"] for r in rows}),
        "positive_safe_reference_file_pairs": sum(d["matched_cells"] > 0 for d in diagnostics),
        "source_code": [file_identity(p) for p in sorted(Path(__file__).parent.rglob("*.py"))],
        "rules": {
            "policy": "scat_safe_v1",
            "space": "strict full SAFE footprint containment",
            "time": "strict SAFE start - tolerance < cell time < SAFE end + tolerance",
            "time_tolerance_minutes": recipe.time_tolerance_minutes,
            "retention": "all finite wind vectors; retain quality/ice flags without filtering",
        },
        "units": {
            "wind_speed": "m s-1",
            "wind_direction_to": "degree, flowing toward",
            "ref_lon": "degrees_east, [-180,180)",
            "ref_lat": "degrees_north",
        },
        "limitations": [
            "SAFE/reference-cell identities, not resolved imagettes or co-aligned data.",
            "Keep all footprint components; no legacy largest-part reduction.",
            "No sea-percent/polar prefilter or downstream quality/ice filter.",
            "Tolerance is explicitly configured, not scientifically calibrated.",
            "Sequential bounded bundles; no scheduler, resume or parallel publication.",
        ],
    }
    return publish(recipe.output_dir, rows, diagnostics, products, manifest)
