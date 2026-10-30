"""Reference coverage -> SAFE candidates -> imagette matching -> STAC publication."""

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import numpy as np

from soba_matchups import __version__
from soba_matchups.adapters import scat, swot
from soba_matchups.config import REFERENCE_PRODUCTS
from soba_matchups.output import file_identity, imagette_item, publish
from soba_matchups.sar_metadata import (
    OCN_HEADER_PRECISION_SECONDS,
    decode_safe_name,
    discover_local,
    verify_local_inputs,
)


# One adapter per instrument: it finds the files, reads them and names the point fields.
ADAPTERS = {"ascat": scat, "hscat": scat, "karin": swot}


def reference_inputs(recipe):
    """Read and validate every reference granule for the requested days (stop_date exclusive)."""
    adapter = ADAPTERS[recipe.instrument]
    paths = adapter.find_files(recipe)
    if len({p.name for p in paths}) != len(paths):
        raise ValueError("Repeated reference filenames; select one unambiguous product bundle")
    references = []
    for path in paths:
        # Hash each granule so the manifest records exactly what was read.
        identity = file_identity(path)
        product = (recipe.instrument, recipe.platform, recipe.resolution)
        source, resolution, _ = REFERENCE_PRODUCTS[product]
        ref = adapter.read_reference(path, expected_source=source, expected_resolution=resolution)
        if not recipe.start_date <= ref["granule_start_day"] < recipe.stop_date:
            raise ValueError(f"Granule start day outside requested range: {path}")
        ref["identity"] = identity
        references.append(ref)
    return references


def run(recipe):
    """Match reference points to SAR imagettes and publish one STAC Item per matched imagette.

    Returns the run manifest. The output directory must not exist yet.
    """
    if recipe.output_dir.exists() or recipe.output_dir.is_symlink():
        raise FileExistsError(
            f"Output already exists; choose a fresh directory: {recipe.output_dir}"
        )
    adapter = ADAPTERS[recipe.instrument]
    refs = reference_inputs(recipe)
    delta = timedelta(minutes=recipe.time_tolerance_minutes)
    precision = timedelta(seconds=OCN_HEADER_PRECISION_SECONDS)

    def iso(t):
        """Format a UTC datetime with a trailing Z."""
        return t.isoformat().replace("+00:00", "Z")

    # Search window: reference coverage widened by the time tolerance and header rounding.
    start = iso(min(ref["coverage_start"] for ref in refs) - delta - precision)
    end = iso(max(ref["coverage_end"] for ref in refs) + delta + precision)
    products, discovery = discover_local(start, end, recipe.sar_missions, recipe.sar_archive_root)
    items, diagnostics = [], []
    seen_points = set()
    imagettes_processed = duplicate_points = safes_without_candidates = 0
    candidate_cells = 0

    def number(x, integer=False):
        """Return a float (an int for flags), or None for NaN so the JSON stays valid."""
        if not np.isfinite(x):
            return None
        return int(x) if integer else float(x)

    # Stage 1 tests each whole SAFE; stage 2 tests each imagette on its own footprint and clock.
    for product in products:
        fields = decode_safe_name(product["Name"])
        if fields["producttype"] != "OCN":
            raise ValueError("SAR product type does not match the WV OCN discovery request")
        candidates = [
            (
                ref,
                scat.match_cells(
                    product, ref, recipe.time_tolerance_minutes + OCN_HEADER_PRECISION_SECONDS / 60
                ),
            )
            for ref in refs
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
                subset = {key: ref[key][safe_indices] for key in ["lon", "lat", "time"]}
                indices = safe_indices[
                    scat.match_cells(imagette, subset, recipe.time_tolerance_minutes)
                ]
                values = ref["fields_at"](indices) if len(indices) else {}
                points = []
                for position, i in enumerate(indices):
                    flat = int(ref["flat"][i])
                    row_index, cell_index = divmod(flat, ref["columns"])
                    # One stable ID per (imagette, granule, cell); repeats are dropped and counted.
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
                            **{
                                key: number(values[key][position], key in adapter.INTEGER_FIELDS)
                                for key in adapter.FIELDS
                            },
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
                            "reference_source": ref["source"],
                            "reference_type": adapter.TYPE,
                            "reference_version": ref["version"],
                            "reference_resolution": ref["resolution"],
                            "points": points,
                        }
                    )
            if groups:
                items.append(
                    imagette_item(product, imagette, fields, groups, recipe.time_tolerance_minutes)
                )
    # Confirm no input changed while matching, before anything is published.
    verify_local_inputs(discovery)
    for ref in refs:
        if file_identity(ref["path"]) != ref["identity"]:
            raise ValueError(f"Input changed during processing: {ref['path']}")
    # The manifest records the recipe, inputs, counts, matching rules and known limitations.
    manifest = {
        "schema_version": 11,
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
        "reference_inputs": [
            {**ref["identity"], **{key: ref[key] for key in ["source", "version", "resolution"]}}
            for ref in refs
        ],
        "reference_files": len(refs),
        # The label of the reference product, used in the Item file names.
        "reference_product": REFERENCE_PRODUCTS[
            (recipe.instrument, recipe.platform, recipe.resolution)
        ][2],
        # Versions are recorded, not enforced; this lists every one used in the run.
        "reference_versions": sorted({ref["version"] for ref in refs}),
        "reference_cells": sum(len(ref["flat"]) for ref in refs),
        "sar_products_processed": len(products),
        "safes_without_candidate_cells": safes_without_candidates,
        "safe_candidate_cell_rows": candidate_cells,
        "imagettes_processed": imagettes_processed,
        "stac_items": len(items),
        "matchup_imagette_cell_rows": len(seen_points),
        "duplicate_associations_discarded": duplicate_points,
        "matched_safes": len({i["properties"]["sarmatchup:sar_safe"] for i in items}),
        "positive_imagette_reference_file_pairs": sum(d["retained_cells"] > 0 for d in diagnostics),
        "source_code": [file_identity(p) for p in sorted(Path(__file__).parent.rglob("*.py"))],
        "rules": {
            "policy": adapter.POLICY,
            "imagette_mapping": "local_ocn_geolocation_to_manifest_frame",
            "space": "strict individual imagette footprint point containment",
            "time": "strict imagette start - tolerance < cell time < imagette end + tolerance",
            "time_source": "OCN firstMeasurementTime / lastMeasurementTime UTC attributes",
            "candidate_padding_seconds": OCN_HEADER_PRECISION_SECONDS,
            "header_precision": "whole-second NC headers use outward-rounded manifest bounds",
            "time_tolerance_minutes": recipe.time_tolerance_minutes,
            "retention": adapter.RETENTION,
        },
        "units": {
            **adapter.UNITS,
            "ref_lon": "degrees_east, [-180,180)",
            "ref_lat": "degrees_north",
        },
        "limitations": [
            "Requires extracted L2 OCN SAFEs with manifest.safe and all listed measurement files.",
            "Missing/ambiguous native frame associations fail without suffix fallback.",
            "Reads OSW geolocation and UTC attributes, not wind/spectral arrays; no network.",
            "Archive inventory only; missing mission/day directories are listed in discovery.",
            "SAR file identities use size/mtime; manifests and references use SHA256.",
            "sarmatchup:* properties are project-specific, not a registered STAC extension.",
            "No sea-percent, polar, quality or ice filter.",
            "Tolerance is explicitly configured, not scientifically calibrated.",
            "One Item per matched imagette per fresh run; no cross-run update or enrichment.",
        ],
    }
    return publish(recipe.output_dir, items, diagnostics, products, manifest)
