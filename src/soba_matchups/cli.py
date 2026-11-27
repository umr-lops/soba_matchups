"""Standalone entry point for TOML validation and imagette-level matching."""

import argparse
from datetime import date
import json
import sys
import tomllib

import xml.etree.ElementTree as ET

from soba_matchups.batch import listing, run_day
from soba_matchups.config import load_recipe
from soba_matchups.pipeline import reference_inputs, run


def main(argv=None):
    """Run `validate` (check recipe and reference files), `run` (match and publish), or, for a
    per-day recipe (output_dir holding {day}), `listing` (one `run-day` line per day still to
    do, the task file of a Slurm job array) and `run-day` (one day).

    Returns the process exit code: 0 on success (or a skipped day), 2 on invalid input.
    """
    parser = argparse.ArgumentParser(prog="soba_matchups")
    parser.add_argument("command", choices=["validate", "run", "listing", "run-day"])
    parser.add_argument("--recipe", required=True)
    parser.add_argument(
        "--day", type=date.fromisoformat, help="run-day, validate: the day of a per-day recipe"
    )
    parser.add_argument(
        "--start", type=date.fromisoformat, help="listing: first day (default: start_date)"
    )
    parser.add_argument(
        "--stop", type=date.fromisoformat, help="listing: exclusive last day (default: stop_date)"
    )
    parser.add_argument(
        "--include-done", action="store_true", help="listing: also list the days already done"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="run-day: replace the day's existing output"
    )
    args = parser.parse_args(argv)
    if args.command == "run-day" and args.day is None:
        parser.error("run-day needs --day")
    if args.command in ("run", "listing") and args.day is not None:
        parser.error(f"{args.command} takes no --day")
    try:
        if args.command == "listing":
            print("\n".join(listing(args.recipe, args.start, args.stop, args.include_done)))
            return 0
        if args.command == "run-day":
            manifest = run_day(args.recipe, args.day, overwrite=args.overwrite)
            if manifest is not None:
                print(json.dumps(_summary(manifest, load_recipe(args.recipe, args.day)), indent=2))
            return 0
        recipe = load_recipe(args.recipe, args.day)
        if args.command == "validate":
            skipped = []
            refs = reference_inputs(recipe, skipped)
            print(
                json.dumps(
                    {
                        "status": "valid",
                        "reference_files": len(refs),
                        "reference_files_skipped": len(skipped),
                        "resolved_recipe": recipe.resolved(),
                    },
                    indent=2,
                )
            )
        else:
            manifest = run(recipe)
            print(json.dumps(_summary(manifest, recipe), indent=2))
    except (ValueError, OSError, ET.ParseError, tomllib.TOMLDecodeError) as exc:
        print(f"soba_matchups: {exc}", file=sys.stderr)
        return 2
    return 0


def _summary(manifest, recipe):
    """The few counts printed at the end of a run."""
    return {
        "status": manifest["status"],
        "output_dir": str(recipe.output_dir),
        "stac_items": manifest["stac_items"],
        "matchup_imagette_cell_rows": manifest["matchup_imagette_cell_rows"],
        "sar_products_processed": manifest["sar_products_processed"],
    }
