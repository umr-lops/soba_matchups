"""IW, EW and SM scenes from the daily parquet files: names, footprints, discovery, angles, runs."""

import json
import shutil
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely.geometry import Point, Polygon, box, mapping, shape

from soba_matchups import sar_angles, sar_parquet as sp
from soba_matchups.cli import main
from soba_matchups.config import load_recipe
from soba_matchups.sar_metadata import decode_safe_name

# A real IW footprint (S1B 2021-01-04, CDSE daily file) and the same shape moved to the
# synthetic reference cells of conftest: one scene at (0.5, 0.5), one centred on the dateline.
IW = [(162.64325, -21.034466), (164.99649, -20.480621), (164.537354, -18.742329),
      (162.207642, -19.28838)]
# A real EW footprint at its real place, in the catalogue's vertex order (S1B 2021-01-04,
# descending), for the EW angle model.
EW = [(26.745461, 61.721439), (29.453114, 65.788322), (20.470806, 66.6278),
      (18.967598, 62.491425)]
# A real SM beam-1 footprint (S1A 2023-11-01, older CDSE daily file).
SM = [(55.55426, -5.492395), (55.862892, -4.027804), (55.143703, -3.871485),
      (54.833748, -5.335202)]
DAY = "2026-06-29"
IW_NAME = "S1A_IW_OCN__2SDV_20260629T120000_20260629T120025_000001_000001_0001.SAFE"
SM_NAME = "S1A_S1_OCN__2SDV_20260629T120000_20260629T120024_000001_000004_0004.SAFE"
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


def row(name, polygons, start, end, encode=json.dumps, direction=None):
    """One daily-file row of an IW, EW or SM OCN product, with its pass direction if given."""
    return {
        "Name": name,
        "Id": name[:8],
        "ContentDate": encode({"Start": start, "End": end}),
        "GeoFootprint": geojson(polygons),
        "WVImagettes": None,
        **({"Attributes": attributes(direction)} if direction else {}),
    }


def write_day(folder, rows, day=DAY):
    """Write a daily parquet file with the given rows (Attributes only if a row has it)."""
    folder.mkdir(exist_ok=True)
    columns = sp.COLUMNS + (["Attributes"] if any("Attributes" in r for r in rows) else [])
    pq.write_table(
        pa.table({c: [r.get(c) for r in rows] for c in columns}), folder / f"s1_{day}.parquet"
    )


def attributes(direction, encode=json.dumps):
    """The Attributes value of a catalogue row carrying an orbitDirection."""
    return encode([{"Name": "orbitDirection", "Value": direction.upper(), "ValueType": "String"}])


@pytest.fixture
def scenes(local_recipe):
    """The conftest recipe pointed at a daily file with two IW scenes (one cut at the dateline,
    its pieces clockwise like the catalogue's), one EW and one SM scene, each with its pass
    direction; only the first IW SAFE is in the archive."""
    root = local_recipe.parent
    write_day(
        root / "parquet",
        [
            row(
                IW_NAME,
                [moved(IW, 0.5, 0.5)],
                f"{DAY}T12:00:00.100000Z",
                f"{DAY}T12:00:25.2Z",
                direction="ascending",
            ),
            row(
                DATELINE_NAME,
                cut_at_dateline(moved(IW, 180.0, 10.5)),
                f"{DAY}T12:10:00.100000Z",
                f"{DAY}T12:10:25.2Z",
                direction="ascending",
            ),
            row(
                "S1A_EW_OCN__2SDH_20260629T120000_20260629T120100_000001_000003_0003.SAFE",
                [moved(IW, 0.5, 0.5)],
                f"{DAY}T12:00:00Z",
                f"{DAY}T12:01:00Z",
                direction="ascending",
            ),
            row(
                SM_NAME,
                [moved(SM, 0.5, 0.5)],
                f"{DAY}T12:00:00Z",
                f"{DAY}T12:00:24Z",
                direction="descending",
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
    assert decode_safe_name(IW_NAME) == {
        "mode": "IW", "polarization": "VV", "polarizations": "VV+VH", "producttype": "OCN"
    }
    name = "S1B_EW_OCN__2SDH_20210104T045624_20210104T045733_025000_02F9B2_980A.SAFE"
    assert decode_safe_name(name)["mode"] == "EW"
    assert decode_safe_name(name)["polarization"] == "HH"
    # WV is single-polarization; a dual-polarization WV name is not a real product.
    with pytest.raises(ValueError, match="Unsupported"):
        decode_safe_name(IW_NAME.replace("_IW_", "_WV_"))
    # StripMap is named by its beam; "SM" itself never appears in a SAFE name.
    assert decode_safe_name(SM_NAME) == {
        "mode": "SM", "polarization": "VV", "polarizations": "VV+VH", "producttype": "OCN"
    }
    assert decode_safe_name(IW_NAME.replace("SDV", "SDH"))["polarizations"] == "HH+HV"
    with pytest.raises(ValueError, match="Unsupported"):
        decode_safe_name(IW_NAME.replace("_IW_", "_SM_"))
    with pytest.raises(ValueError, match="Unsupported"):
        decode_safe_name(SM_NAME.replace("_S1_", "_S7_"))


def test_recipe_mode_defaults_to_wv_and_iw_needs_the_parquet_files(local_recipe, scenes):
    assert load_recipe(local_recipe).sar_mode == "WV"
    assert load_recipe(scenes).sar_mode == "IW"
    text = scenes.read_text()
    for bad, message in [
        (text.replace('sar_mode = "IW"', 'sar_mode = "RF"'), "sar_mode must be one of"),
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
        f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", ("S1A",), root / "parquet", root / "esa", mode="IW"
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
        f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", ("S1A",), root / "parquet", None, mode="IW"
    )
    assert [p["Imagettes"][0]["Id"] for p in without] == [first["Id"], dateline["Id"]]
    assert without[0]["Imagettes"][0]["href"] == IW_NAME
    ew, _ = sp.discover_parquet(
        f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", ("S1A",), root / "parquet", None, mode="EW"
    )
    assert [p["Name"][4:6] for p in ew] == ["EW"]


def test_older_daily_files_with_python_literal_columns_are_read(tmp_path):
    rows = [row(IW_NAME, [moved(IW, 0.5, 0.5)], f"{DAY}T12:00:00Z", f"{DAY}T12:00:25Z", str)]
    rows[0]["GeoFootprint"] = str(json.loads(rows[0]["GeoFootprint"]))
    write_day(tmp_path, rows)
    products, _ = sp.discover_parquet(
        f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", ("S1A",), tmp_path, None, mode="IW"
    )
    assert len(products) == 1 and len(products[0]["SceneRing"]) == 4


def test_scene_angles_follow_the_iw_and_ew_models_and_skip_outside_points():
    points = [{"ref_lon": 163.6, "ref_lat": -19.9}, {"ref_lon": 100.0, "ref_lat": -30.0}]
    assert sar_angles.add_scene_angles("iw", IW, "IW", points, "ascending") is None
    assert 29 < points[0]["sar_incidence_angle"] < 46
    # Across the swath, from near range (west on this ascending pass) to far range.
    across = [{"ref_lon": lon, "ref_lat": -19.95} for lon in (162.75, 163.6, 164.45)]
    assert sar_angles.add_scene_angles("iw", IW, "IW", across, "ascending") is None
    near, middle, far = (p["sar_incidence_angle"] for p in across)
    assert 29 < near < 34 < middle < 41 < far < 46.5
    assert 0 < points[0]["sar_ground_heading_uncertainty"] < 5
    assert "sar_wv_subswath" not in points[0]
    assert all(points[1][key] is None for key in sar_angles.FIELDS)
    ew = [{"ref_lon": 24.0, "ref_lat": 64.2}]
    assert sar_angles.add_scene_angles("ew", EW, "EW", ew, "descending") is None
    assert 18 < ew[0]["sar_incidence_angle"] < 48
    # A footprint the library cannot invert leaves null incidence and says why.
    small = [{"ref_lon": 0.5, "ref_lat": 0.5}]
    box_ring = [(0, 0), (1, 0), (1, 1), (0, 1)]
    text = sar_angles.add_scene_angles("box", box_ring, "IW", small, "ascending")
    assert text and small[0]["sar_incidence_angle"] is None
    assert small[0]["sar_angles_coarse"] is None
    assert points[0]["sar_angles_coarse"] is False
    assert 0 < points[0]["sar_incidence_angle_uncertainty"] < 1.5
    assert 0 < points[0]["sar_elevation_angle_uncertainty"] < 1.5
    assert small[0]["sar_incidence_angle_uncertainty"] is None


# S1A_IW_GRDH_1SSH_20240210T141522_20240210T141547_052497_065975_BB3A.SAFE (catalogue corners,
# ascending, ~67 S): its 250.9 km width is just below s1-geometry's IW orbit-altitude band.
POLAR_TURN_IW = [[88.143066, -68.73497], [93.03759, -67.410416], [90.656868, -66.19561],
                 [85.893784, -67.45388]]


def test_a_footprint_just_outside_the_altitude_band_gets_coarse_angles():
    across = [{"ref_lon": lon, "ref_lat": lat}
              for lon, lat in ((87.0, -67.6), (89.4, -67.45), (92.0, -67.3))]
    assert sar_angles.add_scene_angles("polar", POLAR_TURN_IW, "IW", across, "ascending") is None
    near, middle, far = (p["sar_incidence_angle"] for p in across)
    assert 29 < near < 34 < middle < 41 < far < 46.5
    assert all(p["sar_angles_coarse"] is True for p in across)
    # coarse estimates carry s1-geometry's larger uncertainty
    assert all(p["sar_incidence_angle_uncertainty"] > 3 for p in across)
    assert all(p["sar_elevation_angle_uncertainty"] > 3 for p in across)
    assert all(p["sar_elevation_angle"] is not None for p in across)


def test_iw_run_writes_one_item_per_scene_with_angles(scenes):
    assert main(["run", "--recipe", str(scenes)]) == 0
    result = scenes.parent / "iw_result"
    manifest = json.loads((result / "manifest.json").read_text())
    assert manifest["sar_mode"] == "IW" and manifest["schema_version"] == 24
    assert manifest["rules"]["imagette_mapping"] == sp.SCENE_MAPPING
    assert manifest["sar_angles"]["errors"] == []
    names = sorted(p.name for p in (result / "items").iterdir())
    assert names == sorted(
        n.removesuffix(".SAFE") + "__KNMI_HSCAT_HY2B_25KM.json" for n in (IW_NAME, DATELINE_NAME)
    )
    first = json.loads((result / "items" / names[0]).read_text())
    props = first["properties"]
    assert props["sarmatchup:sar_instrument_mode"] == "IW"
    assert "sarmatchup:imagette_index" not in props
    assert props["sarmatchup:polarization"] == "VV+VH"  # SDV
    # A lone slice: its direction is the catalogue's, unchecked, and said so.
    assert props["sarmatchup:sar_orbit_direction"] == "ascending"
    assert props["sarmatchup:sar_orbit_direction_source"] == "catalogue"
    assert IW_NAME in manifest["discovery"]["orbit_direction_from_catalogue"]
    assert first["assets"]["sar"]["type"] == "application/x-netcdf"
    points = [p for g in props["sarmatchup:matchups"] for p in g["points"]]
    # (0.5, 0.5) and (0.0, 0.5) at 12:00:01 in each of the two granules; 12:20 is too late.
    assert len(points) == 4
    assert all(29 < p["sar_incidence_angle"] < 46 for p in points)
    # Ascending and right-looking: the western point (0.0) is nearer range than the eastern one.
    by_lon = {round(p["ref_lon"], 3): p["sar_incidence_angle"] for p in points}
    assert by_lon[0.0] < by_lon[0.5] - 2
    assert all("sar_wv_subswath" not in p for p in points)
    dateline = json.loads((result / "items" / names[1]).read_text())
    points = [p for g in dateline["properties"]["sarmatchup:matchups"] for p in g["points"]]
    assert sorted({round(p["ref_lon"], 6) for p in points}) == [-180.0, -179.9, 179.9]
    # The scene cut into two clockwise pieces gives the angles of the same scene left whole.
    whole = moved(IW, 180.0, 10.5)
    for point in points:
        unsplit = [{"ref_lon": point["ref_lon"], "ref_lat": point["ref_lat"]}]
        sar_angles.add_scene_angles(DATELINE_NAME, whole, "IW", unsplit, "ascending")
        for key in ("sar_incidence_angle", "sar_elevation_angle", "sar_ground_heading"):
            assert point[key] is not None
            assert point[key] == pytest.approx(unsplit[0][key], abs=0.01)
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


def test_sm_discovery_reads_beam_named_products_and_files_them_under_sm(scenes):
    root = scenes.parent
    safe = root / "esa/sentinel-1a/L2/SM/S1A_SM_OCN__2S/2026/180" / SM_NAME / "measurement"
    safe.mkdir(parents=True)
    (safe / "s1a-s1-ocn-vv-20260629t120000-20260629t120024-000001-000004-001.nc").write_bytes(b"")
    products, provenance = sp.discover_parquet(
        f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", ("S1A",), root / "parquet", root / "esa", mode="SM"
    )
    assert [p["Name"] for p in products] == [SM_NAME]
    assert products[0]["Imagettes"][0]["href"].endswith("-001.nc")
    assert "warning" not in provenance


def test_sm_run_on_files_without_sm_products_warns_instead_of_looking_empty(scenes, caplog):
    root = scenes.parent
    write_day(root / "no_sm", [row(IW_NAME, [moved(IW, 0.5, 0.5)], f"{DAY}T12:00:00Z",
                                   f"{DAY}T12:00:25Z")])
    _, provenance = sp.discover_parquet(
        f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", ("S1A",), root / "no_sm", None, mode="SM"
    )
    assert "daily_parquet_files_v2 does not list SM products yet" in provenance["warning"]
    assert "no SM product" in caplog.text


def test_sm_angles_use_the_beam_of_the_safe_name():
    point = {"ref_lon": 55.35, "ref_lat": -4.7}
    beam1 = [dict(point)]
    assert sar_angles.add_scene_angles(SM_NAME, SM, "SM", beam1, "descending") is None
    beam6 = [dict(point)]
    s6 = SM_NAME.replace("_S1_", "_S6_")
    assert sar_angles.add_scene_angles(s6, SM, "SM", beam6, "descending") is None
    # Beam 1 is the steepest StripMap beam, beam 6 the shallowest.
    assert 18 < beam1[0]["sar_incidence_angle"] < 27 < 40 < beam6[0]["sar_incidence_angle"] < 48


def test_sm_run_writes_sm_items(scenes):
    scenes.write_text(scenes.read_text().replace('sar_mode = "IW"', 'sar_mode = "SM"'))
    assert main(["run", "--recipe", str(scenes)]) == 0
    result = scenes.parent / "iw_result"
    manifest = json.loads((result / "manifest.json").read_text())
    assert manifest["sar_mode"] == "SM" and manifest["sar_angles"]["errors"] == []
    (path,) = (result / "items").iterdir()
    assert path.name == SM_NAME.removesuffix(".SAFE") + "__KNMI_HSCAT_HY2B_25KM.json"
    item = json.loads(path.read_text())
    assert item["properties"]["sarmatchup:sar_instrument_mode"] == "SM"
    points = [p for g in item["properties"]["sarmatchup:matchups"] for p in g["points"]]
    assert points and all(18 < p["sar_incidence_angle"] < 27 for p in points)


def test_catalogue_pass_direction_is_read_from_every_attributes_encoding():
    assert sp.orbit_direction({"Attributes": attributes("ascending")}) == "ascending"
    # Older daily files: a JSON string holding a Python literal.
    twice = json.dumps(str([{"Name": "orbitDirection", "Value": "DESCENDING"}]))
    assert sp.orbit_direction({"Attributes": twice}) == "descending"
    assert sp.orbit_direction({}) is None
    assert sp.orbit_direction({"Attributes": attributes("turning")}) is None


def test_heading_follows_the_pass_direction_whatever_the_vertex_order():
    from s1_geometry import heading_from_lat

    agree = [{"ref_lon": 163.6, "ref_lat": -19.9}]
    assert sar_angles.add_scene_angles(IW_NAME, IW, "IW", agree, "ascending") is None
    assert agree[0]["sar_ground_heading"] == pytest.approx(heading_from_lat(-19.9, "ascending"))
    assert agree[0]["sar_incidence_angle"] is not None
    # The same corners in any order or sense give the same angles.
    for corners in (IW[2:] + IW[:2], IW[::-1]):
        again = [{"ref_lon": 163.6, "ref_lat": -19.9}]
        assert sar_angles.add_scene_angles(IW_NAME, corners, "IW", again, "ascending") is None
        assert again[0] == pytest.approx(agree[0])


def test_discovery_keeps_the_catalogue_direction(tmp_path):
    rows = [row(IW_NAME, [moved(IW, 0.5, 0.5)], f"{DAY}T12:00:00Z", f"{DAY}T12:00:25Z")]
    rows[0]["Attributes"] = attributes("ascending")
    write_day(tmp_path, rows)
    (product,), _ = sp.discover_parquet(
        f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", ("S1A",), tmp_path, None, mode="IW"
    )
    assert product["OrbitDirection"] == "ascending"


# Two real footprints the catalogue cut at the dateline (2021-01-01), with their pass direction.
REAL_DATELINE = {
    "ascending": [
        [(180.0, -68.395966450262), (175.752502, -65.14431), (180.0, -64.116248322531)],
        [(-179.674164, -68.645409), (-180.0, -68.395966450262), (-180.0, -64.116248322531),
         (-176.729828, -63.324738), (-171.469574, -66.587868)],
    ],
    "descending": [
        [(180.0, 64.054580833729), (176.35817, 64.371918), (177.027771, 66.098602),
         (180.0, 65.846860699663)],
        [(-178.54538, 63.92783), (-180.0, 64.054580833729), (-180.0, 65.846860699663),
         (-177.544769, 65.638908)],
    ],
}


def test_real_dateline_pieces_join_and_get_every_angle():
    from s1_geometry import heading_from_lat, orbit_direction_from_ring

    names = {
        "ascending": "S1A_EW_OCN__2SDH_20210101T074838_20210101T074942_035941_0435B6_B715.SAFE",
        "descending": "S1A_IW_OCN__2SDV_20210101T183152_20210101T183221_035948_0435F1_4325.SAFE",
    }
    points = {"ascending": (-176.0, -66.0), "descending": (178.5, 65.0)}
    for direction, pieces in REAL_DATELINE.items():
        corners = sp.scene_corners(pieces)
        assert len(corners) == 4 and Polygon(corners).is_valid
        assert max(x for x, _ in corners) - min(x for x, _ in corners) < 20
        ring = sar_angles.library_ring(corners, direction)
        assert orbit_direction_from_ring(ring) == direction
        name = names[direction]
        lon, lat = points[direction]
        point = [{"ref_lon": lon, "ref_lat": lat}]
        assert sar_angles.add_scene_angles(name, corners, name[4:6], point, direction) is None
        assert 18 < point[0]["sar_incidence_angle"] < 48
        assert point[0]["sar_ground_heading"] == pytest.approx(heading_from_lat(lat, direction))


def test_library_order_of_whole_footprints():
    # Real S1B footprints in the catalogue's order: the library's order is the same for a
    # descending pass (EW, SM) and one corner earlier, last-near first, for an ascending one
    # (IW), whatever order or sense the corners come in.
    for ring, direction, expected in (
        (EW, "descending", EW),
        (SM, "descending", SM),
        (IW, "ascending", IW[3:] + IW[:3]),
    ):
        for corners in (ring, ring[2:] + ring[:2], ring[::-1]):
            assert sar_angles.library_ring(corners, direction) == [list(c) for c in expected]


def test_sm_scene_the_library_misreads_in_catalogue_order_reads_right_in_its_own():
    from s1_geometry import orbit_direction_from_ring

    # A real S1A beam-6 footprint (2021-01-09), ascending, that the library reads as descending
    # in the catalogue's order.
    s6 = [(-46.47036, -24.386806), (-45.699665, -24.198542), (-46.183361, -22.460461),
          (-46.944817, -22.64558)]
    assert orbit_direction_from_ring(s6) == "descending"
    assert orbit_direction_from_ring(sar_angles.library_ring(s6, "ascending")) == "ascending"
    point = [{"ref_lon": -46.2, "ref_lat": -23.4}]
    name = "S1A_S6_OCN__2SDV_20210109T214356_20210109T214425_036066_043A29_7CFB.SAFE"
    assert sar_angles.add_scene_angles(name, s6, "SM", point, "ascending") is None
    assert 40 < point[0]["sar_incidence_angle"] < 48


def test_a_footprint_that_is_not_four_corners_gets_a_heading_but_no_incidence():
    five = [*IW[:2], ((IW[1][0] + IW[2][0]) / 2 + 0.3, (IW[1][1] + IW[2][1]) / 2), *IW[2:]]
    point = [{"ref_lon": 163.6, "ref_lat": -19.9}]
    text = sar_angles.add_scene_angles(IW_NAME, five, "IW", point, "ascending")
    assert "the footprint has 5 corners; incidence left null" in text
    assert point[0]["sar_incidence_angle"] is None and point[0]["sar_ground_heading"] is not None


def test_an_unreadable_content_date_skips_the_product(tmp_path):
    rows = [row(IW_NAME, [moved(IW, 0.5, 0.5)], f"{DAY}T12:00:00Z", f"{DAY}T12:00:25Z")]
    rows[0]["ContentDate"] = '{"Start": "2026-06-29T12:00:00Z",'
    write_day(tmp_path, rows)
    products, provenance = sp.discover_parquet(
        f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", ("S1A",), tmp_path, None, mode="IW"
    )
    assert products == [] and provenance["safes_skipped"][0]["safe"] == IW_NAME


def test_without_a_catalogue_direction_the_product_is_reported_and_turning_gives_no_angles():
    point = [{"ref_lon": 163.6, "ref_lat": -19.9}]
    text = sar_angles.add_scene_angles(IW_NAME, IW, "IW", point)
    assert "no pass direction; the footprint reads as ascending" in text
    assert point[0]["sar_incidence_angle"] is not None
    assert point[0]["sar_ground_heading"] is not None
    # A real S1A beam-1 footprint (2021-01-09) the library reads as turning.
    s1 = [(46.120533, -12.695731), (46.858467, -12.5327), (46.672684, -11.697246),
          (45.936893, -11.859667)]
    point = [{"ref_lon": 46.4, "ref_lat": -12.2}]
    text = sar_angles.add_scene_angles(SM_NAME, s1, "SM", point)
    assert "reads as turning, so no angles" in text
    assert all(point[0][key] is None for key in sar_angles.FIELDS)


def test_altimeter_first_pass_keeps_every_point_inside_a_scene():
    from soba_matchups.adapters import alti

    footprint = moved(IW, 0.5, 0.5)
    product = {
        "GeoFootprint": mapping(Polygon(footprint)),
        "ContentDate": {"Start": f"{DAY}T12:00:00Z", "End": f"{DAY}T12:00:25Z"},
        "Imagettes": [{"Centre": [0.5, 0.5]}],
        "SceneRing": [list(c) for c in footprint],
    }
    reference = {
        # At the centre, inside but ~100 km from it, and outside the scene.
        "lon": np.array([0.5, 1.3, 5.0]),
        "lat": np.array([0.5, 1.0, 5.0]),
        "time": np.array([f"{DAY}T12:00:10"] * 3, dtype="datetime64[ns]"),
    }
    assert list(alti.match_safe(product, reference, 30)) == [0, 1]
    # A WV product keeps the radius pass only.
    product.pop("SceneRing")
    assert list(alti.match_safe(product, reference, 30)) == [0]


def test_unreadable_values_raise_value_error_so_a_wv_safe_is_skipped(tmp_path):
    with pytest.raises(ValueError, match="Unreadable"):
        sp._loads('[{"index": 1,')
    rows = json.loads((Path(__file__).parent / "data/parquet_rows.json").read_text())
    ordinary = dict(rows[2])
    day = json.loads(ordinary["ContentDate"])["Start"][:10]
    ordinary["WVImagettes"] = '[{"index": 1,'
    write_day(tmp_path, [ordinary], day)
    products, provenance = sp.discover_parquet(
        f"{day}T00:00:00Z", f"{day}T23:59:59Z", ("S1A", "S1B", "S1C", "S1D"), tmp_path
    )
    assert products == [] and provenance["safes_skipped"][0]["safe"] == ordinary["Name"]


def test_wv_discovery_ignores_scene_products_in_the_same_daily_file(tmp_path):
    rows = json.loads((Path(__file__).parent / "data/parquet_rows.json").read_text())
    ordinary = rows[2]
    day = json.loads(ordinary["ContentDate"])["Start"][:10]
    start = json.loads(ordinary["ContentDate"])["Start"]
    iw = row(IW_NAME, [moved(IW, 0.5, 0.5)], start, start, direction="ascending")
    write_day(tmp_path, [ordinary, iw], day)
    window = (f"{day}T00:00:00Z", f"{day}T23:59:59Z", ("S1A", "S1B", "S1C", "S1D"), tmp_path)
    wv, _ = sp.discover_parquet(*window)
    assert [p["Name"] for p in wv] == [ordinary["Name"]]
    scenes, _ = sp.discover_parquet(*window, None, mode="IW")
    assert [p["Name"] for p in scenes] == [IW_NAME]


def test_double_encoded_content_date_and_footprint_are_read(tmp_path):
    def twice(value):
        return json.dumps(str(value))

    rows = [row(IW_NAME, [moved(IW, 0.5, 0.5)], f"{DAY}T12:00:00Z", f"{DAY}T12:00:25Z", twice)]
    rows[0]["GeoFootprint"] = twice(json.loads(rows[0]["GeoFootprint"]))
    write_day(tmp_path, rows)
    (product,), _ = sp.discover_parquet(
        f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", ("S1A",), tmp_path, None, mode="IW"
    )
    assert product["ContentDate"]["Start"] == f"{DAY}T12:00:00Z"
    assert len(product["SceneRing"]) == 4


def test_pass_direction_comes_from_the_slices_of_the_datatake_and_corrections_are_listed(tmp_path):
    # Three slices of one datatake moving north (an ascending pass), the catalogue saying
    # descending for the middle one; a lone slice keeps the catalogue direction.
    names = [
        f"S1A_IW_OCN__2SDV_20260629T1200{s0}_20260629T1200{s1}_000001_00000A_000{i}.SAFE"
        for i, (s0, s1) in enumerate((("00", "25"), ("25", "50"), ("50", "75")), 1)
    ]
    rows = [
        row(name, [moved(IW, 0.5, lat)], f"{DAY}T12:00:00Z", f"{DAY}T12:00:25Z", direction=d)
        for name, lat, d in zip(names, (0.0, 1.5, 3.0), ("ascending", "descending", "ascending"))
    ]
    single = "S1A_IW_OCN__2SDV_20260629T130000_20260629T130025_000001_00000B_0004.SAFE"
    rows.append(
        row(single, [moved(IW, 0.5, -5.0)], f"{DAY}T13:00:00Z", f"{DAY}T13:00:25Z",
            direction="descending")
    )
    write_day(tmp_path, rows)
    products, provenance = sp.discover_parquet(
        f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", ("S1A",), tmp_path, None, mode="IW"
    )
    by_name = {p["Name"]: p for p in products}
    assert all(by_name[n]["OrbitDirection"] == "ascending" for n in names)
    assert {by_name[n]["OrbitDirectionSource"] for n in names} == {"slice motion"}
    assert by_name[single]["OrbitDirection"] == "descending"
    assert by_name[single]["OrbitDirectionSource"] == "catalogue"
    assert provenance["orbit_direction_from_slices"] == 3
    assert provenance["orbit_direction_corrected"] == [
        {"safe": names[1], "catalogue": "descending", "slice_motion": "ascending"}
    ]


def test_a_swath_measured_across_the_wrong_edges_leaves_the_incidence_null(monkeypatch):
    import s1_geometry

    real = s1_geometry.estimate_incidence_elevation

    def wrong_edges(*args, **kwargs):
        angles = real(*args, **kwargs)
        return type(angles)(**{**angles.__dict__, "swath_width_km": 170.0})

    monkeypatch.setattr(s1_geometry, "estimate_incidence_elevation", wrong_edges)
    point = [{"ref_lon": 163.6, "ref_lat": -19.9}]
    text = sar_angles.add_scene_angles(IW_NAME, IW, "IW", point, "ascending")
    assert "measured a 170 km swath across a" in text
    assert point[0]["sar_incidence_angle"] is None and point[0]["sar_ground_heading"] is not None


def test_a_point_on_a_cut_at_179_99999_is_matched(tmp_path):
    from soba_matchups.adapters import scat

    hair = [
        [(179.99999 if abs(x - 180) < 1e-9 else -179.99999 if abs(x + 180) < 1e-9 else x, y)
         for x, y in piece]
        for piece in cut_at_dateline(moved(IW, 180.0, 10.5))
    ]
    write_day(tmp_path, [row(IW_NAME, hair, f"{DAY}T12:00:00Z", f"{DAY}T12:00:25Z")])
    (product,), _ = sp.discover_parquet(
        f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", ("S1A",), tmp_path, None, mode="IW"
    )
    reference = {
        "lon": np.array([180.0, -179.999995, 179.5, 170.0]),
        "lat": np.array([10.5, 10.5, 10.5, 10.5]),
        "time": np.array([f"{DAY}T12:00:10"] * 4, dtype="datetime64[ns]"),
    }
    assert list(scat.match_cells(product, reference, 1)) == [0, 1, 2]


def test_buoy_first_pass_keeps_the_records_inside_a_scene():
    from soba_matchups.adapters import buoy

    footprint = moved(IW, 0.5, 0.5)
    product = {
        "Id": "local_scene_buoy_test",
        "GeoFootprint": mapping(Polygon(footprint)),
        "ContentDate": {"Start": f"{DAY}T12:00:00Z", "End": f"{DAY}T12:00:25Z"},
        "Imagettes": [{"Centre": [0.5, 0.5]}],
        "SceneRing": [list(c) for c in footprint],
    }
    # Sorted times: before the window, inside it (in, ~100 km from the centre, out), after it.
    reference = {
        "lon": np.array([0.5, 0.5, 1.3, 5.0, 0.5]),
        "lat": np.array([0.5, 0.5, 1.0, 5.0, 0.5]),
        "time": np.array(
            [f"{DAY}T10:00:00"] + [f"{DAY}T12:00:10"] * 3 + [f"{DAY}T14:00:00"],
            dtype="datetime64[ns]",
        ),
    }
    assert list(buoy.match_safe(product, reference, 30)) == [1, 2]


def test_a_wrong_catalogue_direction_on_a_lone_slice_is_not_detected_only_flagged():
    # Documents the limitation: the ring is built for the direction given, so the library reads
    # it back; near and far range swap and nothing is reported. The source is what tells.
    right = [{"ref_lon": 162.9, "ref_lat": -19.95}]
    wrong = [{"ref_lon": 162.9, "ref_lat": -19.95}]
    assert sar_angles.add_scene_angles(IW_NAME, IW, "IW", right, "ascending") is None
    assert sar_angles.add_scene_angles(IW_NAME, IW, "IW", wrong, "descending") is None
    assert wrong[0]["sar_incidence_angle"] > right[0]["sar_incidence_angle"] + 5


def test_slice_motion_cases():
    def key(mission="S1A", mode="IW", datatake="00000A"):
        return (mission, mode, datatake)

    slices = {
        # Northward with a reprocessed duplicate of the middle slice (same start, same place).
        key(): [("T1", "a1", 0.0), ("T2", "a2", 1.5), ("T2", "a2bis", 1.5), ("T3", "a3", 3.0)],
        # Steps below the threshold give nothing.
        key(datatake="00000B"): [("T1", "b1", 10.0), ("T2", "b2", 10.03)],
        # A slice between opposite steps (the turn) gives nothing; the ends follow their step.
        key(datatake="00000C"): [("T1", "c1", 78.0), ("T2", "c2", 79.0), ("T3", "c3", 78.5)],
        # Same datatake id on another mission is another datatake.
        key("S1C", datatake="00000A"): [("T1", "d1", 5.0), ("T2", "d2", 3.0)],
    }
    motion = sp.slice_motion(slices)
    assert {n: motion.get(n) for n in ("a1", "a2", "a2bis", "a3")} == dict.fromkeys(
        ("a1", "a2", "a2bis", "a3"), "ascending"
    )
    assert "b1" not in motion and "b2" not in motion
    assert motion["c1"] == "ascending" and "c2" not in motion and motion["c3"] == "descending"
    assert motion["d1"] == motion["d2"] == "descending"


def test_corner_roles_survive_a_heading_model_off_by_40_degrees(monkeypatch):
    import s1_geometry

    expected = sar_angles.library_ring(IW, "ascending")
    real = s1_geometry.heading_from_lat
    for bias in (-40, 40):
        skewed = lambda lat, d, b=bias: real(lat, d) + b  # noqa: E731
        monkeypatch.setattr(s1_geometry, "heading_from_lat", skewed)
        assert sar_angles.library_ring(IW, "ascending") == expected


def test_provider_layout_links_the_ocn_file_among_the_subswath_osw_files(scenes):
    """The CERSAT provider tree, where recent IW SAFEs also hold one OSW file per subswath."""
    root = scenes.parent
    safe = root / "provider/sentinel-1a/c-band_sar/s1a_iw_ocn__2s/2026/180" / IW_NAME
    (safe / "measurement").mkdir(parents=True)
    stamp = "20260629t120000-20260629t120025-000001-000001"
    for name in [f"s1a-iw{k}-osw-vv-{stamp}-00{k}.nc" for k in (1, 2, 3)] + [
        f"s1a-iw-ocn-vv-{stamp}-001.nc"
    ]:
        (safe / "measurement" / name).write_bytes(b"")
    text = scenes.read_text().replace('sar_archive_root = "esa"', 'sar_archive_root = "provider"')
    scenes.write_text(text + 'sar_archive_layout = "provider"\n')
    assert main(["run", "--recipe", str(scenes)]) == 0
    result = root / "iw_result"
    manifest = json.loads((result / "manifest.json").read_text())
    assert manifest["discovery"]["sar_archive_layout"] == "provider"
    assert manifest["imagettes_unavailable"] == 1  # the dateline scene is not in the tree
    name = IW_NAME.removesuffix(".SAFE") + "__KNMI_HSCAT_HY2B_25KM.json"
    item = json.loads((result / "items" / name).read_text())
    href = (safe / "measurement" / f"s1a-iw-ocn-vv-{stamp}-001.nc").as_uri()
    assert item["assets"]["sar"]["href"] == href
    assert item["properties"]["sarmatchup:measurement_name"] == href.rsplit("/", 1)[1]


def test_provider_layout_paths_and_measurement_choice(tmp_path):
    folder = sp._safe_folder(tmp_path, IW_NAME, "OCN", "provider")
    assert folder.relative_to(tmp_path).parts == (
        "sentinel-1a", "c-band_sar", "s1a_iw_ocn__2s", "2026", "180", IW_NAME,
    )
    assert sp._safe_folder(tmp_path, IW_NAME).parts[-6:-4] == ("L2", "IW")
    with pytest.raises(ValueError, match="layout"):
        sp._safe_folder(tmp_path, IW_NAME, layout="other")
    osw = ["s1c-iw1-osw-vv-x-001.nc", "s1c-iw2-osw-vv-x-002.nc"]
    assert sp._scene_measurement([*osw, "s1c-iw-ocn-vv-x-001.nc"]) == "s1c-iw-ocn-vv-x-001.nc"
    assert sp._scene_measurement(["s1a-iw-ocn-hh-x-001.nc"]) == "s1a-iw-ocn-hh-x-001.nc"
    assert sp._scene_measurement(osw) is None
    assert sp._scene_measurement([]) is None


def test_provider_layout_is_for_iw_and_ew_ocn_from_the_parquet_files(local_recipe, scenes):
    assert load_recipe(scenes).sar_archive_layout == "mpc"
    scenes.write_text(scenes.read_text() + 'sar_archive_layout = "provider"\n')
    assert load_recipe(scenes).sar_archive_layout == "provider"
    local_recipe.write_text(local_recipe.read_text() + 'sar_archive_layout = "provider"\n')
    with pytest.raises(ValueError, match="IW and EW OCN"):
        load_recipe(local_recipe)
    local_recipe.write_text(local_recipe.read_text().replace('"provider"', '"elsewhere"'))
    with pytest.raises(ValueError, match="must be 'mpc' or 'provider'"):
        load_recipe(local_recipe)


def test_edge_distances_are_great_circle_metres():
    # 1 degree of longitude on the equator, from a meridian arc beside it.
    one_degree = sar_angles.EARTH_RADIUS_M * np.pi / 180
    assert sar_angles.edge_distance_m((1, 0), (0, -1), (0, 1)) == pytest.approx(one_degree)
    # Past the end of the arc the distance is to the nearer end.
    assert sar_angles.edge_distance_m((0, 3), (0, -1), (0, 1)) == pytest.approx(2 * one_degree)
    # At the centre of an IW scene: half the swath to a range line.
    ring = sar_angles.library_ring(IW, "ascending")
    along, across = sar_angles.edge_distances_m(ring, *np.mean(IW, axis=0))
    assert along == pytest.approx(500 * sar_angles.across_track_km(ring), rel=0.01)
    assert 50e3 < across < along
    # Next to the first-near corner both distances are small.
    first_near = ring[1]
    near_corner = sar_angles.edge_distances_m(ring, first_near[0] + 0.01, first_near[1] + 0.01)
    assert max(near_corner) < 2e3


def test_scene_geometry_problems():
    assert sar_angles.scene_geometry_problem(IW, "ascending") is None
    assert "turning" in sar_angles.scene_geometry_problem(IW, "ascending", turning=True)
    assert "3 corners" in sar_angles.scene_geometry_problem(IW[:3], "ascending")
    assert "no pass direction" in sar_angles.scene_geometry_problem(IW, None)


def test_iw_points_carry_edge_distances_and_turning_scenes_are_rejected(scenes):
    assert main(["run", "--recipe", str(scenes)]) == 0
    result = scenes.parent / "iw_result"
    manifest = json.loads((result / "manifest.json").read_text())
    assert "sar_distance_to_along_track_edge" in manifest["units"]
    assert manifest["scenes_rejected_geometry"] == []
    name = IW_NAME.removesuffix(".SAFE") + "__KNMI_HSCAT_HY2B_25KM.json"
    item = json.loads((result / "items" / name).read_text())
    points = [p for g in item["properties"]["sarmatchup:matchups"] for p in g["points"]]
    for p in points:
        assert 0 < p["sar_distance_to_along_track_edge"] < 140e3  # footprint ~270 km wide here
        assert 0 < p["sar_distance_to_cross_track_edge"] < 140e3
    # A second slice of the same datatake at the same latitude: neither gives a direction, both
    # are at the turning latitude and rejected.
    table = pq.read_table(next((scenes.parent / "parquet").iterdir())).to_pylist()
    twin = dict(table[0])
    twin["Name"] = IW_NAME.replace("T120000_20260629T120025", "T120025_20260629T120050")
    twin["ContentDate"] = json.dumps(
        {"Start": f"{DAY}T12:00:25.2Z", "End": f"{DAY}T12:00:50Z"}
    )
    write_day(scenes.parent / "parquet", [*table, twin])
    shutil.rmtree(result)
    assert main(["run", "--recipe", str(scenes)]) == 0
    manifest = json.loads((result / "manifest.json").read_text())
    rejected = {r["safe"]: r["reason"] for r in manifest["scenes_rejected_geometry"]}
    assert IW_NAME in rejected and "turning" in rejected[IW_NAME]
    assert not (result / "items" / name).exists()


def test_footprint_tilt_tells_the_pass_direction_of_a_lone_slice():
    assert sar_angles.footprint_tilt_direction(IW)[0] == "ascending"
    # Mirrored east-west, the scene leans the other way: a descending pass.
    mirrored = [(2 * 163.6 - x, y) for x, y in IW]
    direction, margin = sar_angles.footprint_tilt_direction(mirrored)
    assert direction == "descending" and margin > sar_angles.TILT_MARGIN_DEGREES
    problem = sar_angles.scene_geometry_problem
    assert problem(IW, "ascending", source="catalogue") is None
    assert "tilt reads ascending" in problem(IW, "descending", source="catalogue")
    # Directions from the slice motion are not second-guessed by the tilt.
    assert problem(IW, "descending", source="slice motion") is None
    # A square footprint aligned with the meridians leans neither way.
    square = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert "ambiguous" in problem(square, "ascending", source="catalogue")


def test_lone_slice_whose_catalogue_direction_contradicts_its_tilt_is_rejected(scenes):
    table = pq.read_table(next((scenes.parent / "parquet").iterdir())).to_pylist()
    table[0]["Attributes"] = attributes("descending")  # the IW scene leans ascending
    write_day(scenes.parent / "parquet", table)
    assert main(["run", "--recipe", str(scenes)]) == 0
    manifest = json.loads((scenes.parent / "iw_result" / "manifest.json").read_text())
    rejected = {r["safe"]: r["reason"] for r in manifest["scenes_rejected_geometry"]}
    assert "tilt reads ascending" in rejected[IW_NAME]
    assert manifest["stac_items"] == 1  # the dateline scene only


def test_slice_published_twice_is_one_slice_for_the_motion():
    # The first and last slices each have a twin (same start, another checksum).
    slices = {
        ("S1D", "IW", "00A06B"): [
            ("T1", "a", 17.0), ("T1", "a_twin", 17.0), ("T2", "b", 15.4),
            ("T3", "c", 13.9), ("T3", "c_twin", 13.9),
        ],
        # Two copies of one slice and nothing else: a lone slice, not a turning one.
        ("S1D", "IW", "00A0BD"): [("T1", "d", 5.0), ("T1", "d_twin", 5.0)],
    }
    motion = sp.slice_motion(slices)
    assert {n: motion.get(n) for n in ("a", "a_twin", "b", "c", "c_twin")} == dict.fromkeys(
        ("a", "a_twin", "b", "c", "c_twin"), "descending"
    )
    assert "d" not in motion and "d_twin" not in motion


def test_partial_dual_polarization_names_decode():
    for pol, polarizations, co in (("HH", "HH", "HH"), ("VV", "VV", "VV"), ("HV", "HV", "HH")):
        name = IW_NAME.replace("_OCN__2SDV_", f"_OCN__2S{pol}_")
        fields = decode_safe_name(name)
        assert (fields["polarizations"], fields["polarization"]) == (polarizations, co)
    # WV is single polarization only.
    with pytest.raises(ValueError, match="Unsupported"):
        decode_safe_name(IW_NAME.replace("_IW_", "_WV_").replace("2SDV", "2SVV"))


def test_an_undecodable_name_skips_that_product_not_the_run(scenes):
    """A name the run cannot decode (here a made-up polarization) is listed in safes_skipped."""
    table = pq.read_table(next((scenes.parent / "parquet").iterdir())).to_pylist()
    odd = dict(table[0])
    odd["Name"] = IW_NAME.replace("2SDV", "2SXV").replace("_0001.SAFE", "_0009.SAFE")
    write_day(scenes.parent / "parquet", [*table, odd])
    assert main(["run", "--recipe", str(scenes)]) == 0
    manifest = json.loads((scenes.parent / "iw_result" / "manifest.json").read_text())
    assert manifest["stac_items"] == 2
    skipped = manifest["discovery"]["safes_skipped"]
    assert [s["safe"] for s in skipped] == [odd["Name"]] and "Unsupported" in skipped[0]["reason"]


GRD_NAME = IW_NAME.replace("_OCN__2SDV_", "_GRDH_1SDV_")


def test_grd_run_writes_one_item_per_scene_linking_the_safe_folder(scenes):
    """IW GRD scenes: the GRD row is matched, its cloud-optimized copy is not read."""
    table = pq.read_table(next((scenes.parent / "parquet").iterdir())).to_pylist()
    grd = {**table[0], "Name": GRD_NAME}
    cog = {**table[0], "Name": GRD_NAME.replace(".SAFE", "_COG.SAFE")}
    write_day(scenes.parent / "parquet", [*table, grd, cog])
    safe = scenes.parent / "esa/sentinel-1a/L1/IW/S1A_IW_GRDH_1S/2026/180" / GRD_NAME
    (safe / "measurement").mkdir(parents=True)
    scenes.write_text(scenes.read_text() + 'product_type = "GRD"\n')
    assert main(["run", "--recipe", str(scenes)]) == 0
    result = scenes.parent / "iw_result"
    manifest = json.loads((result / "manifest.json").read_text())
    assert manifest["stac_items"] == 1 and manifest["imagettes_unavailable"] == 0
    assert manifest["discovery"]["safes_found"] == 1  # the COG copy is not read
    name = GRD_NAME.removesuffix(".SAFE") + "__KNMI_HSCAT_HY2B_25KM.json"
    item = json.loads((result / "items" / name).read_text())
    props = item["properties"]
    assert props["sarmatchup:sar_product_type"] == "GRD"
    assert props["sarmatchup:sar_instrument_mode"] == "IW"
    assert item["assets"]["sar"]["href"] == safe.as_uri()
    points = [p for g in props["sarmatchup:matchups"] for p in g["points"]]
    assert points and all(p["sar_distance_to_along_track_edge"] > 0 for p in points)


def test_grd_names_and_recipes():
    fields = decode_safe_name(GRD_NAME)
    assert (fields["producttype"], fields["mode"]) == ("GRD", "IW")
    assert fields["polarizations"] == "VV+VH"
    assert decode_safe_name(GRD_NAME.replace("_IW_GRDH_", "_EW_GRDM_"))["mode"] == "EW"
    for bad in (
        GRD_NAME.replace("_GRDH_1", "_GRDH_2"),  # GRD is level 1
        IW_NAME.replace("_OCN__", "_OCNH_"),  # OCN has no resolution letter
        GRD_NAME.replace("_IW_", "_WV_").replace("1SDV", "1SSV"),  # no WV GRD
    ):
        with pytest.raises(ValueError, match="Unsupported"):
            decode_safe_name(bad)


def test_grd_recipe_rules(local_recipe, scenes):
    scenes.write_text(scenes.read_text() + 'product_type = "GRD"\n')
    assert load_recipe(scenes).product_type == "GRD"
    local_recipe.write_text(local_recipe.read_text() + 'product_type = "GRD"\n')
    with pytest.raises(ValueError, match="needs sar_parquet_root|only read for sar_mode IW"):
        load_recipe(local_recipe)
    scenes.write_text(scenes.read_text() + 'sar_archive_layout = "provider"\n')
    with pytest.raises(ValueError, match="provider"):
        load_recipe(scenes)


SLC_NAME = IW_NAME.replace("_OCN__2SDV_", "_SLC__1SDV_")


def test_iw_slc_run_writes_one_item_per_scene_linking_the_safe_folder(scenes):
    """IW SLC scenes: one Item per SLC SAFE, its link the SAFE folder (several sub-swath
    images per polarization); a SAFE not in the archive is matched and listed unavailable."""
    table = pq.read_table(next((scenes.parent / "parquet").iterdir())).to_pylist()
    slc = {**table[0], "Name": SLC_NAME}
    absent = {**table[1], "Name": table[1]["Name"].replace("_OCN__2SDV_", "_SLC__1SDV_")}
    write_day(scenes.parent / "parquet", [*table, slc, absent])
    safe = scenes.parent / "esa/sentinel-1a/L1/IW/S1A_IW_SLC__1S/2026/180" / SLC_NAME
    (safe / "measurement").mkdir(parents=True)
    scenes.write_text(scenes.read_text() + 'product_type = "SLC"\n')
    assert main(["run", "--recipe", str(scenes)]) == 0
    result = scenes.parent / "iw_result"
    manifest = json.loads((result / "manifest.json").read_text())
    assert manifest["stac_items"] == 2 and manifest["imagettes_unavailable"] == 1
    name = SLC_NAME.removesuffix(".SAFE") + "__KNMI_HSCAT_HY2B_25KM.json"
    item = json.loads((result / "items" / name).read_text())
    assert item["properties"]["sarmatchup:sar_product_type"] == "SLC"
    assert item["assets"]["sar"]["href"] == safe.as_uri()
    assert "sarmatchup:imagette_index" not in item["properties"]
