"""One per-day recipe, many days: the task listing of a Slurm job array, and the run of one day.

A per-day recipe is an ordinary recipe whose ``output_dir`` contains ``{day}``. Its
``start_date`` / ``stop_date`` give the default range of the listing; each task runs one day
into its own folder, so a day can be run again on its own and the days already done are left
alone. The listing lines are the arguments of ``soba_matchups``, one task per line: the input
format of turboblast (https://github.com/umr-lops/turboblast).
"""

from datetime import date, timedelta
import os
from pathlib import Path
import shutil
import tomllib

from soba_matchups.config import load_recipe
from soba_matchups.pipeline import ADAPTERS, run


def recipe_range(recipe_path):
    """Return the start_date and (exclusive) stop_date written in a recipe."""
    with Path(recipe_path).open("rb") as stream:
        data = tomllib.load(stream)
    try:
        return tuple(date.fromisoformat(str(data[key])) for key in ("start_date", "stop_date"))
    except (KeyError, ValueError) as exc:
        raise ValueError("start_date and stop_date must be YYYY-MM-DD dates") from exc


def listing(recipe_path, start=None, stop=None, include_done=False):
    """Return one ``run-day`` line per day of [start, stop) whose output does not exist yet.

    start and stop default to the recipe's own start_date and stop_date. With include_done,
    the days already produced are listed too (run-day skips them unless told to overwrite).
    """
    recipe_path = Path(recipe_path).resolve()
    default_start, default_stop = recipe_range(recipe_path)
    start, stop = start or default_start, stop or default_stop
    if start >= stop:
        raise ValueError("start must precede exclusive stop")
    lines = []
    for offset in range((stop - start).days):
        day = start + timedelta(days=offset)
        # Validating the recipe for each day also checks it before any task is submitted.
        if include_done or not load_recipe(recipe_path, day=day).output_dir.exists():
            lines.append(f"run-day --recipe {recipe_path} --day {day.isoformat()}")
    return lines


def run_day(recipe_path, day, overwrite=False):
    """Run a per-day recipe for one day; return the manifest, or None when the day is skipped.

    A day whose output exists is skipped, unless overwrite: the old output is then kept aside
    and put back if the new run fails. A day for which the adapter finds no reference file is
    skipped too, with its reason (nothing is written), so that a later listing offers it again;
    check a recipe with ``validate`` first, since a wrong reference_root skips every day.
    """
    recipe = load_recipe(recipe_path, day=day)
    output = recipe.output_dir
    if output.exists() and not overwrite:
        print(f"soba_matchups: {day} already done: {output}")
        return None
    try:
        ADAPTERS[recipe.instrument].find_files(recipe)
    except ValueError as exc:
        print(f"soba_matchups: {day} skipped: {exc}")
        return None
    aside = output.with_name(f".{output.name}.replaced-{os.getpid()}")
    if output.exists():
        output.rename(aside)
    try:
        manifest = run(recipe)
    except BaseException:
        if aside.exists():
            if output.exists():
                shutil.rmtree(output)
            aside.rename(output)
        raise
    if aside.exists():
        shutil.rmtree(aside)
    return manifest
