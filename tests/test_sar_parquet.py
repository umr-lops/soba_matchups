"""CDSE daily parquet source: dateline merge, centres, logs of what is left out, a full run."""

import json
import logging
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from soba_matchups import sar_parquet as sp

ROWS = json.loads((Path(__file__).parent / "data/parquet_rows.json").read_text())
WEST_FIRST, EAST_FIRST, ORDINARY, A_CLASS = ROWS
ALL_MISSIONS = ("S1A", "S1B", "S1C", "S1D")


def km(a, b):
    """Distance in km between two [lon, lat] points (small distances, longitude folded)."""
    dx = ((a[0] - b[0] + 180) % 360 - 180) * np.cos(np.radians(a[1])) * 111.195
    return float(np.hypot(dx, (a[1] - b[1]) * 111.195))


def write_day(folder, day, rows):
    """Write a daily parquet file with the given rows (string columns, as the producer does)."""
    folder.mkdir(exist_ok=True)
    columns = {c: [r[c] for r in rows] for c in sp.COLUMNS}
    pq.write_table(pa.table(columns), folder / f"s1_{day}.parquet")


def find(row):
    """Return the day (YYYY-MM-DD) and the window around a row's SAFE."""
    day = json.loads(row["ContentDate"])["Start"][:10]
    return day, f"{day}T00:00:00Z", f"{day}T23:59:59Z"


def test_west_half_first_and_east_half_first_are_both_merged():
    for row, pair in ((WEST_FIRST, (6, 7)), (EAST_FIRST, (1, 2))):
        rings = sp._rings(row["GeoFootprint"])
        groups = sp.merge_dateline(rings)
        assert len(groups) == len(json.loads(row["WVImagettes"])) == len(rings) - 1
        assert list(pair) in groups
        assert all(len(g) == 1 for g in groups if list(pair) != g)


def test_halves_need_matching_cut_latitudes():
    rings = sp._rings(WEST_FIRST["GeoFootprint"])
    shifted = [[(x, y + 0.5) for x, y in rings[7]]]
    assert not sp._are_halves(rings[6], shifted[0])
    assert sp._are_halves(rings[6], rings[7]) and sp._are_halves(rings[7], rings[6])
    assert not sp._are_halves(rings[0], rings[1])
    # A half that meets the cut at the right start but ends elsewhere is not the other half.
    top = max(y for x, y in rings[7] if x == 180.0)
    stretched = [(x, y + 0.5 if (x == 180.0 and y == top) else y) for x, y in rings[7]]
    assert sp._cut_latitudes(stretched, 180)[0] == sp._cut_latitudes(rings[7], 180)[0]
    assert not sp._are_halves(rings[6], stretched)
    bottom = min(y for x, y in rings[7] if x == 180.0)
    lowered = [(x, y - 0.5 if (x == 180.0 and y == bottom) else y) for x, y in rings[7]]
    assert sp._cut_latitudes(lowered, 180)[-1] == sp._cut_latitudes(rings[7], 180)[-1]
    assert not sp._are_halves(rings[6], lowered)


def test_centre_of_an_ordinary_polygon_is_the_mean_of_its_corners():
    ring = sp._rings(ORDINARY["GeoFootprint"])[0]
    got = sp.centre([ring])
    assert km(got, [np.mean([x for x, _ in ring]), np.mean([y for _, y in ring])]) < 0.3


def test_centre_of_a_dateline_imagette_lies_between_its_halves_in_range():
    rings = sp._rings(WEST_FIRST["GeoFootprint"])
    got = sp.centre([rings[6], rings[7]])
    assert -180 <= got[0] < 180 and abs(got[0]) > 179.8
    west, east = np.mean(rings[6], axis=0), np.mean(rings[7], axis=0)
    assert abs(got[1] - (west[1] + east[1]) / 2) < 0.1
    # The same imagette in the other order gives the same centre.
    assert km(got, sp.centre([rings[7], rings[6]])) < 0.01


def test_six_vertex_polygon_uses_the_area_not_the_first_corners():
    # A square with two extra vertices on one side: the centroid stays at the square's centre.
    ring = [(0.0, 0.0), (0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 1.0), (0.0, 1.0)]
    assert km(sp.centre([ring]), [0.5, 0.5]) < 0.1


def test_discovery_logs_safes_without_list_skipped_safes_and_unavailable_imagettes(
    tmp_path, caplog
):
    broken = dict(ORDINARY, Name=ORDINARY["Name"].replace("58C0", "AAAA"))
    imagettes = json.loads(broken["WVImagettes"])
    broken["WVImagettes"] = json.dumps(imagettes[:1])  # two polygons, one imagette
    day = find(ORDINARY)[0]
    write_day(tmp_path / "parquet", day, [WEST_FIRST, ORDINARY, broken])
    archive = tmp_path / "esa"
    archive.mkdir()
    # Put one imagette file of ORDINARY in the archive; the others stay absent.
    folder = sp._safe_folder(archive, ORDINARY["Name"]) / "measurement"
    folder.mkdir(parents=True)
    first = json.loads(ORDINARY["WVImagettes"])[0]["Name"]
    (folder / first).write_bytes(b"")
    with caplog.at_level(logging.WARNING, logger="soba_matchups.sar_parquet"):
        products, prov = sp.discover_parquet(
            f"{day}T00:00:00Z", f"{day}T23:59:59Z", ALL_MISSIONS, tmp_path / "parquet", archive
        )
    assert {p["Name"] for p in products} == {WEST_FIRST["Name"], ORDINARY["Name"]}
    assert [s["safe"] for s in prov["safes_skipped"]] == [broken["Name"]]
    assert "imagettes" in prov["safes_skipped"][0]["reason"]
    unavailable = {(u["safe"], u["index"]) for u in prov["imagettes_unavailable"]}
    n_west = len(json.loads(WEST_FIRST["WVImagettes"]))
    assert len(unavailable) == n_west + 1  # all of the dateline SAFE and the second ORDINARY file
    assert (ORDINARY["Name"], 2) in unavailable and (ORDINARY["Name"], 1) not in unavailable
    assert {u["reason"] for u in prov["imagettes_unavailable"]} == {
        "SAFE not in the archive",
        "file not in the archive",
    }
    ordinary = next(p for p in products if p["Name"] == ORDINARY["Name"])
    assert [i["available"] for i in ordinary["Imagettes"]] == [True, False]
    text = caplog.text
    assert "SAFEs skipped" in text and "imagettes whose file is not in the archive" in text


def test_a_safe_with_no_imagette_list_is_logged_and_left_out(tmp_path, caplog):
    day = json.loads(A_CLASS["ContentDate"])["Start"][:10]
    write_day(tmp_path / "parquet", day, [A_CLASS, ORDINARY])
    archive = tmp_path / "esa"
    archive.mkdir()
    with caplog.at_level(logging.WARNING, logger="soba_matchups.sar_parquet"):
        products, prov = sp.discover_parquet(
            f"{day}T00:00:00Z", f"{day}T23:59:59Z", ALL_MISSIONS, tmp_path / "parquet", archive
        )
    assert products == []  # ORDINARY is from another day, A_CLASS has no list
    assert prov["safes_without_imagettes"] == [
        {"safe": A_CLASS["Name"], "reason": "no imagette list in the parquet file"}
    ]
    assert "1 SAFEs without an imagette list" in caplog.text


def test_missing_columns_and_missing_files_are_reported(tmp_path):
    day = find(ORDINARY)[0]
    folder = tmp_path / "parquet"
    folder.mkdir()
    pq.write_table(pa.table({"Name": ["x"]}), folder / f"s1_{day}.parquet")
    with pytest.raises(ValueError, match="lacks the columns"):
        sp.discover_parquet(f"{day}T00:00:00Z", f"{day}T23:59:59Z", ALL_MISSIONS, folder, tmp_path)
    with pytest.raises(ValueError, match="No daily parquet files"):
        sp.discover_parquet(
            "2000-01-02T00:00:00Z", "2000-01-02T01:00:00Z", ALL_MISSIONS, folder, tmp_path
        )


def test_a_changed_parquet_file_is_refused(tmp_path):
    day = find(ORDINARY)[0]
    write_day(tmp_path / "parquet", day, [ORDINARY])
    _, prov = sp.discover_parquet(
        f"{day}T00:00:00Z", f"{day}T23:59:59Z", ALL_MISSIONS, tmp_path / "parquet", tmp_path
    )
    sp.verify_parquet_inputs(prov)
    write_day(tmp_path / "parquet", day, [WEST_FIRST])
    with pytest.raises(ValueError, match="changed"):
        sp.verify_parquet_inputs(prov)


def point_ids(item):
    """Return the sorted record ids of every point of an Item."""
    groups = item["properties"]["sarmatchup:matchups"]
    return sorted(p["record_id"] for g in groups for p in g["points"])


def synthetic_parquet(local_recipe):
    """A daily file for the synthetic SAFE of conftest (its second imagette crosses 180)."""
    from tests.conftest import SAFE

    root = local_recipe.parent
    east = [[179.8, 10.0], [180.0, 10.0], [180.0, 11.0], [179.8, 11.0], [179.8, 10.0]]
    west = [[-180.0, 10.0], [-179.8, 10.0], [-179.8, 11.0], [-180.0, 11.0], [-180.0, 10.0]]
    square = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
    names = sorted((root / "esa").rglob("*.nc"))
    listed = []
    for index, path in enumerate(names, 1):
        token = path.name.split("-")
        start = f"2026-06-29T{token[4][9:11]}:{token[4][11:13]}:{token[4][13:15]}Z"
        end = f"2026-06-29T{token[5][9:11]}:{token[5][11:13]}:{token[5][13:15]}Z"
        listed.append(
            {"Name": path.name, "index": index, "ContentDate": {"Start": start, "End": end}}
        )
    row = {
        "Name": SAFE,
        "Id": "synthetic",
        "ContentDate": json.dumps({"Start": "2026-06-29T12:00:00Z", "End": "2026-06-29T12:10:03Z"}),
        "GeoFootprint": json.dumps(
            {"type": "MultiPolygon", "coordinates": [[square], [east], [west]]}
        ),
        "WVImagettes": json.dumps(listed),
    }
    write_day(root / "parquet", "2026-06-29", [row])
    return root / "parquet"


def test_full_run_from_the_parquet_gives_the_same_matches_as_the_local_archive(local_recipe):
    from soba_matchups.cli import main

    parquet = synthetic_parquet(local_recipe)
    recipe = local_recipe.parent / "from_parquet.toml"
    recipe.write_text(
        local_recipe.read_text().replace('output_dir = "result"', 'output_dir = "from_parquet"')
        + f'sar_parquet_root = "{parquet}"\n'
    )
    assert main(["run", "--recipe", str(recipe)]) == 0
    root = local_recipe.parent
    assert main(["run", "--recipe", str(local_recipe)]) == 0
    from_parquet = json.loads((root / "from_parquet/manifest.json").read_text())
    from_archive = json.loads((root / "result/manifest.json").read_text())
    assert from_parquet["discovery"]["source"] == "cdse_daily_parquet"
    assert from_parquet["stac_items"] == from_archive["stac_items"] == 2
    assert (
        from_parquet["matchup_imagette_cell_rows"]
        == from_archive["matchup_imagette_cell_rows"]
        == 8
    )
    assert from_parquet["safes_without_imagettes"] == from_parquet["imagettes_unavailable"] == 0
    assert "daily parquet" in from_parquet["limitations"][0]
    names = sorted(p.name for p in (root / "from_parquet/items").glob("*.json"))
    assert names == sorted(p.name for p in (root / "result/items").glob("*.json"))
    for name in names:
        a = json.loads((root / "from_parquet/items" / name).read_text())
        b = json.loads((root / "result/items" / name).read_text())
        assert a["id"] == b["id"]  # the same Item ids in both sources
        assert point_ids(a) == point_ids(b)


def test_without_an_archive_every_imagette_counts_as_available(tmp_path):
    day = find(ORDINARY)[0]
    write_day(tmp_path / "parquet", day, [WEST_FIRST, ORDINARY])
    products, prov = sp.discover_parquet(
        f"{day}T00:00:00Z", f"{day}T23:59:59Z", ALL_MISSIONS, tmp_path / "parquet"
    )
    assert {p["Name"] for p in products} == {WEST_FIRST["Name"], ORDINARY["Name"]}
    assert prov["imagettes_unavailable"] == [] and prov["sar_archive_root"] is None
    assert all(i["available"] for p in products for i in p["Imagettes"])


def test_a_run_with_only_the_parquet_folder_matches_the_run_with_both(local_recipe):
    from soba_matchups.cli import main

    parquet = synthetic_parquet(local_recipe)
    root = local_recipe.parent
    both = root / "both.toml"
    both.write_text(
        local_recipe.read_text().replace('output_dir = "result"', 'output_dir = "both"')
        + f'sar_parquet_root = "{parquet}"\n'
    )
    only = root / "only.toml"
    only.write_text(
        "\n".join(
            line
            for line in both.read_text()
            .replace('output_dir = "both"', 'output_dir = "only"')
            .splitlines()
            if not line.startswith("sar_archive_root")
        )
        + "\n"
    )
    assert main(["run", "--recipe", str(both)]) == 0
    assert main(["run", "--recipe", str(only)]) == 0
    a = json.loads((root / "both/manifest.json").read_text())
    b = json.loads((root / "only/manifest.json").read_text())
    assert a["stac_items"] == b["stac_items"] == 2
    assert a["matchup_imagette_cell_rows"] == b["matchup_imagette_cell_rows"] == 8
    assert b["imagettes_unavailable"] == 0 and b["discovery"]["sar_archive_root"] is None
    for item in sorted((root / "both/items").glob("*.json")):
        x = json.loads(item.read_text())
        y = json.loads((root / "only/items" / item.name).read_text())
        assert x["id"] == y["id"] and point_ids(x) == point_ids(y)
