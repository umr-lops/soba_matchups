"""Standalone entry point for TOML validation and imagette-level matching."""

import argparse
import json
import sys
import tomllib

import xml.etree.ElementTree as ET

from soba_matchups.config import load_recipe
from soba_matchups.pipeline import reference_inputs, run


def main(argv=None):
    parser = argparse.ArgumentParser(prog="soba_matchups")
    parser.add_argument("command", choices=["validate", "run"])
    parser.add_argument("--recipe", required=True)
    args = parser.parse_args(argv)
    try:
        recipe = load_recipe(args.recipe)
        if args.command == "validate":
            refs = reference_inputs(recipe)
            print(
                json.dumps(
                    {
                        "status": "valid",
                        "reference_files": len(refs),
                        "resolved_recipe": recipe.resolved(),
                    },
                    indent=2,
                )
            )
        else:
            manifest = run(recipe)
            print(
                json.dumps(
                    {
                        "status": manifest["status"],
                        "output_dir": str(recipe.output_dir),
                        "stac_items": manifest["stac_items"],
                        "matchup_imagette_cell_rows": manifest["matchup_imagette_cell_rows"],
                        "sar_products_processed": manifest["sar_products_processed"],
                    },
                    indent=2,
                )
            )
    except (ValueError, OSError, ET.ParseError, tomllib.TOMLDecodeError) as exc:
        print(f"soba_matchups: {exc}", file=sys.stderr)
        return 2
    return 0
