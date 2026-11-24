"""IW and EW scenes from the daily parquet files: names, footprints, discovery, angles, a run."""

import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely.geometry import Point, Polygon, box, shape

from soba_matchups import sar_angles, sar_parquet as sp
from soba_matchups.cli import main
from soba_matchups.config import load_recipe
from soba_matchups.sar_metadata import decode_safe_name

# A real IW footprint (S1B 2021-01-04, CDSE daily file) and the same shape moved to the
# synthetic reference cells of conftest: one scene at (0.5, 0.5), one centred on the dateline.
IW = [(162.64325, -21.034466), (164.99649, -20.480621), (164.537354, -18.742329),
      (162.207642, -19.28838)]
# A real EW footprint at its real place (S1B 2021-01-04), for the EW angle model.
EW = [(26.745461, 61.721439), (18.967598, 62.491425), (20.470806, 66.6278),
      (29.453114, 65.788322)]
DAY = "2026-06-29"
IW_NAME = "S1A_IW_OCN__2SDV_20260629T120000_20260629T120025_000001_000001_0001.SAFE"
DATELINE_NAME = "S1A_IW_OCN__2SDV_20260629T121000_20260629T121025_000001_000002_0002.SAFE"


def moved(ring, lon, lat):
    """Return the ring translated so that its mean vertex sits at (lon, lat)."""
    x, y = np.mean(ring, axis=0)
    return [(round(a - x + lon, 6), round(b - y + lat, 6)) for a, b in ring]


def geojson(polygons):
    """Return a GeoJSON MultiPolygon string of closed rings, as the daily files store it."""
    return json.dumps(
        {"type": "MultiPolygon", "coordinates": [[[*map(list, p), list(p[0])]] for p in polygons]}
    )


def cut_at_dateline(ring):
    """Split a ring with longitudes beyond 180 into its two [-180, 180] halves."""
    polygon = Polygon(ring)
    east = polygon.intersection(box(0, -90, 180, 90))
    west = polygon.intersection(box(180, -90, 360, 90))
    return [
        list(east.exterior.coords)[:-1],
        [(x - 360, y) for x, y in list(west.exterior.coords)[:-1]],
    ]


def row(name, polygons, start, end, encode=json.dumps):
    """One daily-file row of an IW or EW OCN product."""
    return {
        "Name": name,
        "Id": name[:8],
        "ContentDate": encode({"Start": start, "End": end}),
        "GeoFootprint": geojson(polygons),
        "WVImagettes": None,
    }


def write_day(folder, rows, day=DAY):
    """Write a daily parquet file with the given rows."""
    folder.mkdir(exist_ok=True)
    pq.write_table(
        pa.table({c: [r[c] for r in rows] for c in sp.COLUMNS}), folder / f"s1_{day}.parquet"
    )


@pytest.fixture
def scenes(local_recipe):
    """The conftest recipe pointed at a daily file with two IW scenes (one on the dateline),
    one EW scene elsewhere and the conftest WV SAFE; only the first IW SAFE is in the archive."""
    root = local_recipe.parent
    write_day(
        root / "parquet",
        [
            row(IW_NAME, [moved(IW, 0.5, 0.5)], f"{DAY}T12:00:00.100000Z", f"{DAY}T12:00:25.2Z"),
            row(
                DATELINE_NAME,
                cut_at_dateline(moved(IW, 180.0, 10.5)),
                f"{DAY}T12:10:00.100000Z",
                f"{DAY}T12:10:25.2Z",
            ),
            row(
                "S1A_EW_OCN__2SDH_20260629T120000_20260629T120100_000001_000003_0003.SAFE",
                [moved(IW, 0.5, 0.5)],
                f"{DAY}T12:00:00Z",
                f"{DAY}T12:01:00Z",
            ),
        ],
    )
    safe = root / "esa/sentinel-1a/L2/IW/S1A_IW_OCN__2S/2026/180" / IW_NAME / "measurement"
    safe.mkdir(parents=True)
    (safe / "s1a-iw-ocn-vv-20260629t120000-20260629t120025-000001-000001-001.nc").write_bytes(b"")
    recipe = root / "iw.toml"
    recipe.write_text(
        local_recipe.read_text().replace('output_dir = "result"', 'output_dir = "iw_result"')
        + 'sar_parquet_root = "parquet"\nsar_mode = "IW"\n'
    )
    return recipe


def test_iw_and_ew_names_decode_to_their_mode_and_co_polarization():
    assert decode_safe_name(IW_NAME) == {"mode": "IW", "polarization": "VV", "producttype": "OCN"}
    name = "S1B_EW_OCN__2SDH_20210104T045624_20210104T045733_025000_02F9B2_980A.SAFE"
    assert decode_safe_name(name)["mode"] == "EW"
    assert decode_safe_name(name)["polarization"] == "HH"
    # WV is single-polarization; a dual-polarization WV name is not a real product.
    with pytest.raises(ValueError, match="Unsupported"):
        decode_safe_name(IW_NAME.replace("_IW_", "_WV_"))
    with pytest.raises(ValueError, match="Unsupported"):
        decode_safe_name(IW_NAME.replace("_IW_", "_SM_"))


def test_recipe_mode_defaults_to_wv_and_iw_needs_the_parquet_files(local_recipe, scenes):
    assert load_recipe(local_recipe).sar_mode == "WV"
    assert load_recipe(scenes).sar_mode == "IW"
    text = scenes.read_text()
    for bad, message in [
        (text.replace('sar_mode = "IW"', 'sar_mode = "SM"'), "sar_mode must be one of"),
        (text.replace('sar_parquet_root = "parquet"\n', ""), "needs sar_parquet_root"),
    ]:
        scenes.write_text(bad)
        with pytest.raises(ValueError, match=message):
            load_recipe(scenes)


def test_prime_meridian_cut_vertices_are_dropped_back_to_four_corners():
    # A real S1B IW footprint with the two vertices the catalogue adds on longitude 0.
    seven = [(0.734159, 64.076874), (1.759559, 65.785706), (0.0, 65.935380475197),
             (-3.989408, 66.274734), (-4.66223, 64.548882), (0.0, 64.141088963242)]
    corners = sp.scene_corners([seven])
    assert len(corners) == 4
    assert all(abs(x) > 0.5 for x, _ in corners)


def test_dateline_halves_join_into_four_continuous_corners_even_off_by_a_hair():
    halves = cut_at_dateline(moved(IW, 180.0, 10.5))
    # The catalogue sometimes cuts at 179.99999 instead of 180 (seen in S1A EW 2019).
    hair = [[(x * 0.99999995 if abs(abs(x) - 180) < 1e-9 else x, y) for x, y in h] for h in halves]
    for rings in (halves, hair):
        corners = sp.scene_corners(rings)
        assert len(corners) == 4
        lons = [x for x, _ in corners]
        # Continuous across the dateline: the scene is a few degrees wide, not 360.
        assert max(lons) - min(lons) < 5
    with pytest.raises(ValueError, match="do not form one scene"):
        sp.scene_corners([moved(IW, 0.5, 0.5), moved(IW, 20.5, 0.5)])


def test_discovery_reads_only_the_requested_mode_and_checks_the_archive(scenes):
    root = scenes.parent
    products, provenance = sp.discover_parquet(
        f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", ("S1A",), root / "parquet", root / "esa", "IW"
    )
    assert [p["Name"] for p in products] == [IW_NAME, DATELINE_NAME]
    assert provenance["sar_mode"] == "IW" and provenance["safes_skipped"] == []
    first, dateline = (p["Imagettes"][0] for p in products)
    assert first["index"] == 1 and first["Name"].startswith("s1a-iw-ocn-vv-")
    assert first["href"].endswith(".nc") and first["available"]
    assert shape(first["GeoFootprint"]).covers(Point(0.5, 0.5))
    assert abs(first["Centre"][0] - 0.5) < 0.05 and abs(first["Centre"][1] - 0.5) < 0.05
    # The dateline scene keeps both halves, a centre in range, and is flagged unavailable.
    assert shape(dateline["GeoFootprint"]).geom_type == "MultiPolygon"
    assert abs(dateline["Centre"][0]) > 179.5
    assert not dateline["available"] and dateline["Name"] is None
    assert provenance["imagettes_unavailable"] == [
        {"safe": DATELINE_NAME, "index": 1, "name": None, "reason": "SAFE not in the archive"}
    ]
    # Item ids do not depend on the archive.
    without, _ = sp.discover_parquet(
        f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", ("S1A",), root / "parquet", None, "IW"
    )
    assert [p["Imagettes"][0]["Id"] for p in without] == [first["Id"], dateline["Id"]]
    assert without[0]["Imagettes"][0]["href"] == IW_NAME
    ew, _ = sp.discover_parquet(
        f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", ("S1A",), root / "parquet", None, "EW"
    )
    assert [p["Name"][4:6] for p in ew] == ["EW"]


def test_older_daily_files_with_python_literal_columns_are_read(tmp_path):
    rows = [row(IW_NAME, [moved(IW, 0.5, 0.5)], f"{DAY}T12:00:00Z", f"{DAY}T12:00:25Z", str)]
    rows[0]["GeoFootprint"] = str(json.loads(rows[0]["GeoFootprint"]))
    write_day(tmp_path, rows)
    products, _ = sp.discover_parquet(
        f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", ("S1A",), tmp_path, None, "IW"
    )
    assert len(products) == 1 and len(products[0]["SceneRing"]) == 4


def test_scene_angles_follow_the_iw_and_ew_models_and_skip_outside_points():
    points = [{"ref_lon": 163.6, "ref_lat": -19.9}, {"ref_lon": 100.0, "ref_lat": -30.0}]
    assert sar_angles.add_scene_angles("iw", IW, "IW", points) is None
    assert 29 < points[0]["sar_incidence_angle"] < 46
    assert 0 < points[0]["sar_ground_heading_uncertainty"] < 5
    assert points[0]["sar_wv_subswath"] is None
    assert all(points[1][key] is None for key in sar_angles.FIELDS)
    ew = [{"ref_lon": 24.0, "ref_lat": 64.2}]
    assert sar_angles.add_scene_angles("ew", EW, "EW", ew) is None
    assert 18 < ew[0]["sar_incidence_angle"] < 48
    # A footprint the library cannot invert leaves null angles and says why.
    small = [{"ref_lon": 0.5, "ref_lat": 0.5}]
    text = sar_angles.add_scene_angles("box", [(0, 0), (1, 0), (1, 1), (0, 1)], "IW", small)
    assert "1 of 1 points" in text and small[0]["sar_incidence_angle"] is None


def test_iw_run_writes_one_item_per_scene_with_angles(scenes):
    assert main(["run", "--recipe", str(scenes)]) == 0
    result = scenes.parent / "iw_result"
    manifest = json.loads((result / "manifest.json").read_text())
    assert manifest["sar_mode"] == "IW" and manifest["schema_version"] == 16
    assert manifest["rules"]["imagette_mapping"] == sp.SCENE_MAPPING
    assert manifest["sar_angles"]["errors"] == []
    names = sorted(p.name for p in (result / "items").iterdir())
    assert names == sorted(
        n.removesuffix(".SAFE") + "__KNMI_HSCAT_HY2B_25KM.json" for n in (IW_NAME, DATELINE_NAME)
    )
    first = json.loads((result / "items" / names[0]).read_text())
    props = first["properties"]
    assert props["sarmatchup:sar_instrument_mode"] == "IW"
    assert props["sarmatchup:imagette_index"] == 1
    assert props["sarmatchup:polarization"] == "VV"
    assert first["assets"]["sar"]["type"] == "application/x-netcdf"
    points = [p for g in props["sarmatchup:matchups"] for p in g["points"]]
    # (0.5, 0.5) and (0.0, 0.5) at 12:00:01 in each of the two granules; 12:20 is too late.
    assert len(points) == 4
    assert all(29 < p["sar_incidence_angle"] < 46 for p in points)
    assert all(p["sar_wv_subswath"] is None for p in points)
    dateline = json.loads((result / "items" / names[1]).read_text())
    points = [p for g in dateline["properties"]["sarmatchup:matchups"] for p in g["points"]]
    assert sorted({round(p["ref_lon"], 6) for p in points}) == [-180.0, -179.9, 179.9]
    assert all(p["sar_incidence_angle"] is not None for p in points)
    assert "type" not in dateline["assets"]["sar"]  # SAFE not in the archive: no .nc to link
    assert manifest["imagettes_unavailable"] == 1


def test_ew_run_keeps_points_with_null_angles_when_the_footprint_does_not_invert(scenes):
    # The EW scene is an IW-sized footprint, which the EW model cannot invert.
    scenes.write_text(scenes.read_text().replace('sar_mode = "IW"', 'sar_mode = "EW"'))
    assert main(["run", "--recipe", str(scenes)]) == 0
    manifest = json.loads((scenes.parent / "iw_result" / "manifest.json").read_text())
    assert manifest["stac_items"] == 1
    (error,) = manifest["sar_angles"]["errors"]
    assert error["safe"].startswith("S1A_EW_OCN__") and "points" in error["error"]
    item = json.loads(next((scenes.parent / "iw_result" / "items").iterdir()).read_text())
    assert item["properties"]["sarmatchup:sar_instrument_mode"] == "EW"
    points = [p for g in item["properties"]["sarmatchup:matchups"] for p in g["points"]]
    assert points and all(p["sar_incidence_angle"] is None for p in points)
