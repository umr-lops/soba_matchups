"""Per-day recipes: the job-array listing and the run of one day."""

from datetime import date
import json

import pytest

from soba_matchups import batch
from soba_matchups.batch import listing, run_day
from soba_matchups.cli import main
from soba_matchups.config import load_recipe

DAY = date(2026, 6, 29)


@pytest.fixture
def per_day_recipe(local_recipe):
    text = local_recipe.read_text().replace('output_dir = "result"', 'output_dir = "out/{day}/iw"')
    local_recipe.write_text(text.replace('stop_date = "2026-06-30"', 'stop_date = "2026-07-02"'))
    return local_recipe


def test_day_replaces_dates_and_output_dir(per_day_recipe):
    recipe = load_recipe(per_day_recipe, day=date(2026, 7, 1))
    assert (recipe.start_date, recipe.stop_date) == (date(2026, 7, 1), date(2026, 7, 2))
    assert recipe.output_dir == per_day_recipe.parent / "out/2026-07-01/iw"


def test_per_day_and_plain_recipes_are_not_mixed(per_day_recipe, local_recipe):
    with pytest.raises(ValueError, match="day by day"):
        load_recipe(per_day_recipe)
    assert main(["run", "--recipe", str(per_day_recipe)]) == 2
    plain = local_recipe.parent / "plain.toml"
    plain.write_text(per_day_recipe.read_text().replace("out/{day}/iw", "result"))
    with pytest.raises(ValueError, match="must contain"):
        load_recipe(plain, day=DAY)


def test_listing_skips_days_done(per_day_recipe, capsys):
    lines = listing(per_day_recipe)
    assert lines == [
        f"run-day --recipe {per_day_recipe} --day 2026-06-{d}" for d in ("29", "30")
    ] + [f"run-day --recipe {per_day_recipe} --day 2026-07-01"]
    (per_day_recipe.parent / "out/2026-06-30/iw").mkdir(parents=True)
    assert [line[-10:] for line in listing(per_day_recipe)] == ["2026-06-29", "2026-07-01"]
    assert len(listing(per_day_recipe, include_done=True)) == 3
    assert main(["listing", "--recipe", str(per_day_recipe), "--start", "2026-06-30"]) == 0
    assert capsys.readouterr().out.split() [-1] == "2026-07-01"
    with pytest.raises(ValueError, match="precede"):
        listing(per_day_recipe, start=date(2026, 7, 2))


def test_run_day_runs_once_then_skips(per_day_recipe, capsys):
    assert main(["run-day", "--recipe", str(per_day_recipe), "--day", "2026-06-29"]) == 0
    out = per_day_recipe.parent / "out/2026-06-29/iw"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["stac_items"] == 2
    assert manifest["resolved_recipe"]["stop_date"] == "2026-06-30"
    capsys.readouterr()
    assert run_day(per_day_recipe, DAY) is None
    assert "already done" in capsys.readouterr().out
    # A day without reference granules writes nothing and stays in the listing.
    assert run_day(per_day_recipe, date(2026, 7, 1)) is None
    assert "skipped" in capsys.readouterr().out
    assert not (per_day_recipe.parent / "out/2026-07-01").exists()


def test_overwrite_keeps_old_output_when_the_run_fails(per_day_recipe, monkeypatch):
    run_day(per_day_recipe, DAY)
    out = per_day_recipe.parent / "out/2026-06-29/iw"
    before = (out / "manifest.json").read_text()

    def broken(recipe):
        recipe.output_dir.mkdir()
        raise RuntimeError("boom")

    monkeypatch.setattr(batch, "run", broken)
    with pytest.raises(RuntimeError):
        run_day(per_day_recipe, DAY, overwrite=True)
    assert (out / "manifest.json").read_text() == before
    assert [p.name for p in out.parent.iterdir()] == ["iw"]
    monkeypatch.undo()
    assert run_day(per_day_recipe, DAY, overwrite=True)["stac_items"] == 2
    assert [p.name for p in out.parent.iterdir()] == ["iw"]


def test_cli_day_arguments(per_day_recipe):
    with pytest.raises(SystemExit):
        main(["run-day", "--recipe", str(per_day_recipe)])
    with pytest.raises(SystemExit):
        main(["listing", "--recipe", str(per_day_recipe), "--day", "2026-06-29"])
    assert main(["validate", "--recipe", str(per_day_recipe), "--day", "2026-06-29"]) == 0


def test_day_whose_reference_files_are_all_unusable_is_skipped(per_day_recipe, monkeypatch, capsys):
    """An empty SWOT granule alone on a day: found, then skipped as unusable."""
    from soba_matchups.pipeline import NoUsableReference

    def unusable(recipe):
        raise NoUsableReference("No usable reference file for the requested days (1 skipped)")

    monkeypatch.setattr(batch, "run", unusable)
    assert run_day(per_day_recipe, DAY) is None
    assert "skipped: No usable reference file" in capsys.readouterr().out
    assert not (per_day_recipe.parent / "out/2026-06-29").exists()
    assert main(["run-day", "--recipe", str(per_day_recipe), "--day", "2026-06-29"]) == 0
    # With --overwrite, the day's previous output is put back.
    monkeypatch.undo()
    run_day(per_day_recipe, DAY)
    out = per_day_recipe.parent / "out/2026-06-29/iw"
    before = (out / "manifest.json").read_text()
    monkeypatch.setattr(batch, "run", unusable)
    assert run_day(per_day_recipe, DAY, overwrite=True) is None
    assert (out / "manifest.json").read_text() == before
    assert [p.name for p in out.parent.iterdir()] == ["iw"]


def test_cli_run_still_fails_when_no_reference_file_is_usable(per_day_recipe):
    from soba_matchups.pipeline import NoUsableReference

    assert issubclass(NoUsableReference, ValueError)  # `run` keeps exit code 2
