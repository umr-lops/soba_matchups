"""Reference coverage -> SAFE candidates -> imagette matching -> STAC publication."""

from datetime import datetime, timedelta, timezone
import hashlib
import logging
from pathlib import Path

import numpy as np

from soba_matchups import __version__
from soba_matchups.adapters import SkippableReference, alti, buoy, scat, swot
from soba_matchups.config import REFERENCE_PRODUCTS
from soba_matchups.distance_to_coast import DistanceToCoast
from soba_matchups.sar_angles import (
    SCENE_FIELDS as SCENE_ANGLE_FIELDS,
    WV_FIELDS as WV_ANGLE_FIELDS,
    add_angles,
    add_scene_angles,
    library_version,
    scene_geometry_problem,
    subswath_of,
)
from soba_matchups.output import file_identity, imagette_item, publish
from soba_matchups.sar_parquet import (
    IMAGETTE_MAPPING,
    SCENE_MAPPING,
    SCENE_TIME_SOURCE,
    TIME_SOURCE,
    discover_parquet,
    verify_parquet_inputs,
)
from soba_matchups.sar_metadata import (
    OCN_HEADER_PRECISION_SECONDS,
    decode_safe_name,
    discover_local,
    verify_local_inputs,
)


# What each SAR source can and cannot do, copied into the manifest of every run.
ARCHIVE_LIMITATIONS = [
    "Requires extracted L2 OCN SAFEs with manifest.safe and every listed measurement file.",
    "Ambiguous native frame associations fail without suffix fallback.",
    "A SAFE whose name start/stop time contradicts its manifest (ESA placeholder names) is"
    " skipped and listed in discovery.safes_skipped; other contradictions fail the run.",
    "An OSW centre in no frame is replaced by the wind-grid mean when that lies in the frame of"
    " the file's number; otherwise the file is skipped and listed.",
    "Reads OSW geolocation and UTC attributes, not wind/spectral arrays; no network.",
    "Archive inventory only; missing mission/day directories are listed in discovery.",
    "SAR file identities use size/mtime; manifests and references use SHA256.",
]
PARQUET_LIMITATIONS = [
    "SAR metadata comes from the daily parquet files; no SAFE is opened, nothing is fetched.",
    "Imagette centres are polygon centroids; polygon k is paired with imagette number k.",
    "SAFEs with no imagette list, or with polygons and imagettes that disagree, are left out.",
    "Imagettes whose file is not in the archive are matched and listed as unavailable.",
    "Parquet files and references use SHA256.",
]
# IW, EW, SM: the scene comes from the daily parquet files alone (sar_parquet_root is required).
SCENE_LIMITATIONS = [
    "SAR metadata comes from the daily parquet files; no SAFE is opened, nothing is fetched.",
    "One OCN product is one scene and one Item: its footprint (dateline and prime-meridian cut"
    " pieces joined) and its ContentDate. Points are not tied to a subswath.",
    "The pass direction comes from the motion of the datatake's consecutive slices, else from"
    " the catalogue orbitDirection; disagreements are listed in"
    " discovery.orbit_direction_corrected. A direction from the catalogue alone (a lone slice)"
    " is checked only against s1-geometry's reading of the footprint; those products are listed"
    " in discovery.orbit_direction_from_catalogue, and each Item gives"
    " sar_orbit_direction_source.",
    "A scene whose along-track and cross-track edges cannot be told apart is rejected (no Item)"
    " and listed with its reason in scenes_rejected_geometry: at the turning latitude (its"
    " datatake neighbours give no direction), a footprint without four corners, no pass"
    " direction, a lone slice (direction from the catalogue alone) whose footprint tilt gives the"
    " other direction or is ambiguous, or a footprint s1-geometry reads in another direction.",
    "sar_distance_to_along_track_edge and sar_distance_to_cross_track_edge are great-circle"
    " distances (sphere of radius 6371008.8 m) to the footprint's straight-lined edges.",
    "The last slice of a datatake past the turning latitude takes its direction from its previous"
    " step alone, which can be the other side of the turn.",
    "SAR angles are s1-geometry's at each point: heading from the latitude and the pass"
    " direction (the IW model, a documented extrapolation for EW and SM); incidence and"
    " elevation from the four corners in the library's standard order for that direction (for"
    " SM the beam's specified bands). Incidence and elevation are left null when the library"
    " reads another direction from those corners, cannot invert them, measures a swath more than"
    " 10% off the scene's width, or the footprint is not four corners. Without any direction the"
    " footprint is read as it is, and a turning reading gives no angles. All those products are"
    " listed in sar_angles.errors.",
    "daily_parquet_files_v2 does not list SM products yet (its producer drops them): an SM run"
    " on it finds none and says so in discovery.warning.",
    "Products whose footprint pieces do not form one polygon are left out and listed.",
    "Scenes whose measurement file is not in the archive are matched and listed as unavailable.",
    "Parquet files and references use SHA256.",
]

# Per SAR source: how imagettes are tied to footprints, where their times come from, and the
# known limitations copied into the manifest.
SOURCE_RULES = {
    "archive": (
        "local_ocn_geolocation_to_manifest_frame",
        "OCN firstMeasurementTime / lastMeasurementTime UTC attributes",
        ARCHIVE_LIMITATIONS,
    ),
    "parquet": (IMAGETTE_MAPPING, TIME_SOURCE, PARQUET_LIMITATIONS),
    "scenes": (SCENE_MAPPING, SCENE_TIME_SOURCE, SCENE_LIMITATIONS),
}

logger = logging.getLogger(__name__)

# One adapter per instrument: it finds the files, reads them and names the point fields.
ADAPTERS = {"ascat": scat, "hscat": scat, "karin": swot, "alti": alti, "buoy": buoy}


class NoUsableReference(ValueError):
    """Every reference file of the requested days was skipped (empty or unreadable)."""


def reference_inputs(recipe, skipped=None):
    """Read and validate every reference granule for the requested days (stop_date exclusive).

    A granule the adapter calls skippable (empty or unreadable) is left out and appended to
    the list "skipped" as its identity plus the reason, if a list is given.
    """
    adapter = ADAPTERS[recipe.instrument]
    paths = adapter.find_files(recipe)
    if len({p.name for p in paths}) != len(paths):
        raise ValueError("Repeated reference filenames; select one unambiguous product bundle")
    # An adapter whose files span more than the requested days (buoy station-years) reads them
    # through a reader bound to the recipe; the others read each file on its own.
    read = getattr(adapter, "reader", lambda recipe: adapter.read_reference)(recipe)
    references = []
    for path in paths:
        # Hash each granule so the manifest records exactly what was read.
        identity = file_identity(path)
        product = (recipe.instrument, recipe.platform, recipe.resolution)
        source, resolution, _ = REFERENCE_PRODUCTS[product]
        try:
            ref = read(path, expected_source=source, expected_resolution=resolution)
        except SkippableReference as exc:
            if skipped is None:
                raise
            skipped.append({**identity, "reason": str(exc)})
            continue
        if not recipe.start_date <= ref["granule_start_day"] < recipe.stop_date:
            raise ValueError(f"Granule start day outside requested range: {path}")
        ref["identity"] = identity
        references.append(ref)
    if not references:
        raise NoUsableReference(
            f"No usable reference file for the requested days under {recipe.reference_root}"
            + (f" ({len(skipped)} skipped)" if skipped else "")
        )
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
    # The grid is opened once; if it cannot be, every point gets null and the run goes on.
    coast = DistanceToCoast(recipe.distance_to_coast_file)
    skipped_references = []
    refs = reference_inputs(recipe, skipped_references)
    for entry in skipped_references:
        logger.warning("Reference file skipped: %s", entry["reason"])
    # The label of the reference product, used in the Item file names. An adapter can add the
    # release of its files (CCI: "_V5"); this fails before any matching if they mix releases.
    label = REFERENCE_PRODUCTS[(recipe.instrument, recipe.platform, recipe.resolution)][2]
    # An adapter may name its own point fields (altimeters); the others use the module's.
    point_fields = refs[0].get("fields", adapter.FIELDS)
    integer_fields = refs[0].get("integer_fields", adapter.INTEGER_FIELDS)
    units = refs[0].get("units", adapter.UNITS)
    label += getattr(adapter, "product_suffix", lambda refs: "")(refs)
    # An adapter can replace the SAFE matching step and add a fallback for imagettes that no
    # reference point falls inside (altimeters do both).
    match_safe = getattr(adapter, "match_safe", scat.match_cells)
    fallback_imagette = getattr(adapter, "fallback_imagette", None)
    delta = timedelta(minutes=recipe.time_tolerance_minutes)
    precision = timedelta(seconds=OCN_HEADER_PRECISION_SECONDS)

    def iso(t):
        """Format a UTC datetime with a trailing Z."""
        return t.isoformat().replace("+00:00", "Z")

    # Search window: reference coverage widened by the time tolerance and header rounding.
    start = iso(min(ref["coverage_start"] for ref in refs) - delta - precision)
    end = iso(max(ref["coverage_end"] for ref in refs) + delta + precision)
    from_parquet = recipe.sar_parquet_root is not None
    # IW, EW and SM are matched per scene (one OCN product, one Item), from the parquet files.
    source = "scenes" if recipe.sar_mode != "WV" else "parquet" if from_parquet else "archive"
    mapping, time_source, sar_limitations = SOURCE_RULES[source]
    unit = "scene" if source == "scenes" else "imagette"
    if from_parquet:
        products, discovery = discover_parquet(
            start,
            end,
            recipe.sar_missions,
            recipe.sar_parquet_root,
            recipe.sar_archive_root,
            mode=recipe.sar_mode,
            product_type=recipe.product_type,
            archive_layout=recipe.sar_archive_layout,
        )
        verify_inputs = verify_parquet_inputs
    else:
        products, discovery = discover_local(
            start, end, recipe.sar_missions, recipe.sar_archive_root
        )
        verify_inputs = verify_local_inputs
    items, diagnostics = [], []
    angle_errors = []
    # IW, EW and SM scenes with candidate cells whose edges cannot be told apart: no Item.
    scenes_rejected = []
    seen_points = set()
    imagettes_processed = duplicate_points = safes_without_candidates = 0
    candidate_cells = 0

    def number(x, integer=False):
        """Return a float (an int for flags), or None for NaN so the JSON stays valid.

        Text and true/false values (buoy data modes and release marks) pass through as they are.
        """
        if x is None or isinstance(x, str):
            return x
        if isinstance(x, (bool, np.bool_)):
            return bool(x)
        if not np.isfinite(x):
            return None
        return int(x) if integer else float(x)

    # Stage 1 tests each whole SAFE; stage 2 tests each imagette on its own footprint and clock.
    for product in products:
        fields = decode_safe_name(product["Name"])
        if fields["producttype"] != recipe.product_type or fields["mode"] != recipe.sar_mode:
            raise ValueError(
                f"SAR product {product['Name']} does not match the {recipe.sar_mode}"
                f" {recipe.product_type} request"
            )
        candidates = [
            (
                ref,
                match_safe(
                    product, ref, recipe.time_tolerance_minutes + OCN_HEADER_PRECISION_SECONDS / 60
                ),
            )
            for ref in refs
        ]
        candidate_cells += sum(len(indices) for _, indices in candidates)
        if not any(len(indices) for _, indices in candidates):
            safes_without_candidates += 1
            continue
        if recipe.sar_mode != "WV":
            problem = scene_geometry_problem(
                product.get("SceneRing"),
                product.get("OrbitDirection"),
                product.get("Turning"),
                product.get("OrbitDirectionSource"),
            )
            if problem:
                scenes_rejected.append({"safe": product["Name"], "reason": problem})
                continue
        pending = []
        for imagette in product["Imagettes"]:
            imagettes_processed += 1
            groups = []
            subsets = [
                {key: ref[key][safe_indices] for key in ["lon", "lat", "time"]}
                if len(safe_indices)
                else None
                for ref, safe_indices in candidates
            ]
            # Every point inside the footprint and the time window, in each reference file.
            picked = [
                safe_indices[scat.match_cells(imagette, subset, recipe.time_tolerance_minutes)]
                if subset is not None
                else safe_indices[:0]
                for (_, safe_indices), subset in zip(candidates, subsets)
            ]
            if fallback_imagette is not None and not any(len(indices) for indices in picked):
                # No file has a point inside: keep the one closest in time over all files (the
                # first file wins a tie).
                best = None
                for k, ((_, safe_indices), subset) in enumerate(zip(candidates, subsets)):
                    if subset is None:
                        continue
                    local, gaps = fallback_imagette(imagette, subset, recipe.time_tolerance_minutes)
                    if len(local) and (best is None or gaps.min() < best[0]):
                        best = (gaps.min(), k, safe_indices[local[gaps.argmin()]])
                if best is not None:
                    picked[best[1]] = np.array([best[2]])
            for (ref, safe_indices), indices in zip(candidates, picked):
                if not len(safe_indices):
                    continue
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
                                key: number(values[key][position], key in integer_fields)
                                for key in point_fields
                            },
                        }
                    )
                distances = coast.at([p["ref_lon"] for p in points], [p["ref_lat"] for p in points])
                for point, distance in zip(points, distances):
                    point["sar_distance_to_coast"] = distance
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
                            # Fields an adapter adds per granule (buoys: the platform).
                            "group_fields": ref.get("group_fields", {}),
                            "points": points,
                        }
                    )
            if groups:
                pending.append((imagette, groups))
        # One library call per product, before the Items copy their points.
        flat, expected = [], []
        for imagette, groups in pending:
            for group in groups:
                for point in group["points"]:
                    flat.append(point)
                    expected.append((imagette["index"], subswath_of(imagette["Name"])))
        if recipe.sar_mode == "WV":
            error = add_angles(product["Name"], product.get("WVFootprint"), flat, expected)
        else:
            error = add_scene_angles(
                product["Name"],
                product.get("SceneRing"),
                recipe.sar_mode,
                flat,
                product.get("OrbitDirection"),
            )
        if error:
            angle_errors.append({"safe": product["Name"], "error": error})
        for imagette, groups in pending:
            items.append(
                imagette_item(product, imagette, fields, groups, recipe.time_tolerance_minutes)
            )
    # Confirm no input changed while matching, before anything is published.
    verify_inputs(discovery)
    for ref in refs:
        if file_identity(ref["path"]) != ref["identity"]:
            raise ValueError(f"Input changed during processing: {ref['path']}")
    # The manifest records the recipe, inputs, counts, matching rules and known limitations.
    manifest = {
        "schema_version": 19,
        "status": "success" if items else "no_matches",
        "created_utc": iso(datetime.now(timezone.utc)),
        "library": "soba_matchups",
        "library_version": __version__,
        "resolved_recipe": recipe.resolved(),
        "recipe_input": file_identity(recipe.path),
        "sar_mode": recipe.sar_mode,
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
        "distance_to_coast": coast.summary(),
        "sar_angles": {
            "library": "s1-geometry",
            "version": library_version(),
            "errors": angle_errors,
        },
        "reference_files": len(refs),
        "reference_files_skipped": skipped_references,
        "reference_product": label,
        # Versions are recorded, not enforced; this lists every one used in the run.
        "reference_versions": sorted({ref["version"] for ref in refs}),
        "reference_cells": sum(len(ref["flat"]) for ref in refs),
        "sar_products_processed": len(products),
        "safes_without_candidate_cells": safes_without_candidates,
        "safe_candidate_cell_rows": candidate_cells,
        "imagettes_processed": imagettes_processed,
        "imagettes_skipped": sum(len(p["SkippedImagettes"]) for p in products),
        "imagettes_centre_repaired": len(discovery.get("imagettes_centre_repaired", [])),
        "safes_without_imagettes": len(discovery.get("safes_without_imagettes", [])),
        "safes_skipped": len(discovery.get("safes_skipped", [])),
        "imagettes_unavailable": len(discovery.get("imagettes_unavailable", [])),
        # IW, EW and SM: scenes with candidate cells rejected because their along-track and
        # cross-track edges cannot be told apart (turning latitude, footprint, direction).
        "scenes_rejected_geometry": scenes_rejected,
        "stac_items": len(items),
        "matchup_imagette_cell_rows": len(seen_points),
        "duplicate_associations_discarded": duplicate_points,
        "matched_safes": len({i["properties"]["sarmatchup:sar_safe"] for i in items}),
        "positive_imagette_reference_file_pairs": sum(d["retained_cells"] > 0 for d in diagnostics),
        "source_code": [file_identity(p) for p in sorted(Path(__file__).parent.rglob("*.py"))],
        "rules": {
            "policy": adapter.POLICY,
            "imagette_mapping": mapping,
            "space": getattr(
                adapter, "SPACE", f"strict individual {unit} footprint point containment"
            ),
            "time": f"strict {unit} start - tolerance < cell time < {unit} end + tolerance",
            "time_source": time_source,
            "candidate_padding_seconds": OCN_HEADER_PRECISION_SECONDS,
            "header_precision": "whole-second NC headers use outward-rounded manifest bounds",
            "time_tolerance_minutes": recipe.time_tolerance_minutes,
            "retention": adapter.RETENTION,
        },
        "units": {
            **units,
            "ref_lon": "degrees_east, [-180,180)",
            "ref_lat": "degrees_north",
            "sar_distance_to_coast": "km, from the NASA GSFC grid; null when it is unavailable",
            **{
                k: f"{v}; null when unavailable"
                for k, v in (
                    WV_ANGLE_FIELDS if recipe.sar_mode == "WV" else SCENE_ANGLE_FIELDS
                ).items()
            },
        },
        "limitations": [
            *sar_limitations,
            "sarmatchup:* properties are project-specific, not a registered STAC extension.",
            "No sea-percent, polar, quality or ice filter.",
            "Tolerance is explicitly configured, not scientifically calibrated.",
            "One Item per matched imagette per fresh run; no cross-run update or enrichment.",
            *getattr(adapter, "LIMITATIONS", []),
        ],
    }
    # An adapter that chooses among several sources records what it read and what it left out.
    if hasattr(adapter, "selection"):
        manifest["reference_selection"] = adapter.selection(recipe)
    return publish(recipe.output_dir, items, diagnostics, products, manifest)
