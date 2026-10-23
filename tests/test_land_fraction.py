"""Analytical controls use synthetic land, never an actual OSM extract."""

import hashlib
import json

import pytest
from shapely.geometry import MultiPolygon, Polygon, box, mapping


def test_multipart_dateline_holes_use_component_bbox_reads(tmp_path, monkeypatch):
    import pyogrio.raw
    from soba_matchups.land_fraction import LandMask

    east = box(178, 0, 180, 2)
    west = box(-180, 0, -178, 2)
    donut = Polygon(box(10, -2, 14, 2).exterior.coords, [box(11, -1, 13, 1).exterior.coords])
    path = synthetic_mask(tmp_path, [east, west, donut])
    mask = LandMask(path)
    read = pyogrio.raw.read
    bboxes = []

    def record(*args, **kwargs):
        bboxes.append(kwargs["bbox"])
        return read(*args, **kwargs)

    monkeypatch.setattr(pyogrio.raw, "read", record)
    footprint = MultiPolygon([east, west])
    assert mask.sea_percent(mapping(footprint)) == pytest.approx(0)
    assert bboxes == [east.bounds, west.bounds]
    assert mask.sea_percent(mapping(box(11, -1, 13, 1))) == pytest.approx(100)
    # Preserve holes in the SAFE footprint as well as in the mask.
    assert mask.sea_percent(mapping(donut)) == pytest.approx(0)
    crossing = Polygon([(179, 0), (-179, 0), (-179, 2), (179, 2), (179, 0)])
    with pytest.raises(ValueError, match="antimeridian"):
        mask.sea_percent(mapping(crossing))


def test_invalid_inputs_fail_without_fallback(tmp_path):
    from soba_matchups.land_fraction import LandMask

    with pytest.raises(ValueError, match="land mask"):
        LandMask(tmp_path / "missing.shp")
    path = synthetic_mask(
        tmp_path,
        [box(0, 0, 2, 2)],
        crs={
            "type": "name",
            "properties": {"name": "EPSG:3857"},
        },
    )
    with pytest.raises(ValueError, match="WGS84"):
        LandMask(path)
    path = synthetic_mask(
        tmp_path,
        [box(0, 0, 2, 2)],
        crs={
            "type": "name",
            "properties": {"name": "garbage"},
        },
    )
    with pytest.raises(ValueError, match="CRS"):
        LandMask(path)
    path.write_text("not valid geojson")
    with pytest.raises(ValueError, match="land mask"):
        LandMask(path)
    path = synthetic_mask(tmp_path, [box(0, 0, 2, 2)])
    mask = LandMask(path)
    for geometry in [
        mapping(box(0, 0, 0, 2)),
        {"type": "Polygon", "coordinates": []},
        mapping(Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])),
        mapping(box(0, 90, 1, 91)),
        {"type": "Point", "coordinates": [0, 0]},
        {"type": "garbage"},
    ]:
        with pytest.raises(ValueError, match="footprint"):
            mask.sea_percent(geometry)
    path = synthetic_mask(tmp_path, [Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])])
    with pytest.raises(ValueError, match="land mask"):
        LandMask(path).sea_percent(mapping(box(0, 0, 2, 2)))
    path = synthetic_mask(
        tmp_path,
        [
            Polygon(
                [
                    (179, 0),
                    (-179, 0),
                    (-179, 2),
                    (179, 2),
                    (179, 0),
                ]
            )
        ],
    )
    with pytest.raises(ValueError, match="antimeridian"):
        LandMask(path).sea_percent(mapping(box(178, 0, 180, 2)))


def test_provenance_hashes_local_files_and_detects_changes(tmp_path):
    import numpy as np
    import pyogrio.raw
    from shapely import to_wkb
    from soba_matchups.land_fraction import LandMask

    path = synthetic_mask(tmp_path, [box(0, 0, 2, 2)])
    mask = LandMask(path, source="synthetic test fixture, not OSM")
    assert mask.provenance["source"] == "synthetic test fixture, not OSM"
    assert mask.provenance["expected_dataset"] == "OSM land-polygons-split-4326"
    assert mask.provenance["equal_area_crs"] == "EPSG:6933"
    assert "union" in mask.provenance["calculation_rule"]
    assert mask.provenance["files"] == [
        {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    ]
    assert "OSM" not in LandMask(path).provenance["source"]
    mask.verify_unchanged()
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="changed"):
        mask.verify_unchanged()

    # The same synthetic geometry exercises the shapefile sidecar contract.
    shp = tmp_path / "synthetic-land.shp"
    pyogrio.raw.write(
        shp,
        np.array([to_wkb(box(0, 0, 2, 2))], dtype=object),
        [],
        [],
        geometry_type="Polygon",
        crs="EPSG:4326",
        driver="ESRI Shapefile",
        encoding="UTF-8",
        layer_options={"SPATIAL_INDEX": "YES"},
    )
    mask = LandMask(shp)
    assert mask.sea_percent(mapping(box(0, 0, 4, 2))) == pytest.approx(50)
    assert {entry["path"] for entry in mask.provenance["files"]} == {
        str(shp.with_suffix(suffix)) for suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix")
    }
    for entry in mask.provenance["files"]:
        from pathlib import Path

        assert entry["sha256"] == hashlib.sha256(Path(entry["path"]).read_bytes()).hexdigest()
    mask.verify_unchanged()
    shp.with_suffix(".prj").unlink()
    with pytest.raises(ValueError, match="changed"):
        mask.verify_unchanged()
    with pytest.raises(ValueError, match="sidecar"):
        LandMask(shp)


def synthetic_mask(tmp_path, geometries, **extra):
    path = tmp_path / "synthetic-land.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "properties": {"synthetic": True}, "geometry": mapping(g)}
                    for g in geometries
                ],
                **extra,
            }
        )
    )
    return path


def test_full_footprint_sea_percent_and_overlapping_land_union(tmp_path):
    from soba_matchups.land_fraction import LandMask

    path = synthetic_mask(tmp_path, [box(0, 0, 1.5, 2), box(1, 0, 2, 2)])
    mask = LandMask(path)
    # Same latitude extent makes longitude width an exact equal-area control.
    assert mask.sea_percent(mapping(box(0, 0, 2, 2))) == pytest.approx(0)
    assert mask.sea_percent(mapping(box(0, 0, 4, 2))) == pytest.approx(50)
    assert mask.sea_percent(mapping(box(3, 0, 4, 2))) == pytest.approx(100)
