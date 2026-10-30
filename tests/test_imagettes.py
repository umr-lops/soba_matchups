"""Native geolocation edge cases and archive-day boundaries."""

import os

import netCDF4
import pytest
from shapely.geometry import Point, shape

from soba_matchups import sar_metadata as sar
from test_sar_metadata import START, END, NAME, archive


def test_dateline_is_one_split_frame_not_global_polygon(tmp_path):
    archive(
        tmp_path,
        frames=("0,179 0,-179 1,-179 1,179", "0,3 0,4 1,4 1,3"),
        centres=((-179.5, 0.5), (3.5, 0.5)),
    )
    products, _ = sar.discover_local(START, END, ("S1C",), tmp_path)
    frame = products[0]["Imagettes"][0]["GeoFootprint"]
    polygon = shape(frame)
    assert frame["type"] == "MultiPolygon"
    assert polygon.area == 2
    assert polygon.covers(Point(179.5, 0.5))
    assert polygon.covers(Point(-179.5, 0.5))
    assert not polygon.covers(Point(0, 0.5))


@pytest.mark.parametrize(
    "bad", ["missing", "ambiguous", "time", "traversal", "slc", "mission", "duplicate"]
)
def test_invalid_native_metadata_fails_closed(tmp_path, bad):
    safe, names = archive(tmp_path)
    path = safe / "measurement" / names[0]
    manifest = safe / "manifest.safe"
    if bad == "missing":
        path.unlink()
    elif bad == "ambiguous":
        manifest.write_text(manifest.read_text().replace("0,3 0,4 1,4 1,3", "0,0 0,1 1,1 1,0"))
    elif bad == "time":
        with netCDF4.Dataset(path, "a") as ds:
            ds.lastMeasurementTime = "2025-06-01T01:14:43Z"
    elif bad == "traversal":
        manifest.write_text(manifest.read_text().replace("./measurement/", "../measurement/"))
    elif bad == "slc":
        manifest.write_text(manifest.read_text().replace("<productType>OCN", "<productType>SLC"))
    elif bad == "mission":
        manifest.write_text(manifest.read_text().replace("<safe:number>C", "<safe:number>A"))
    elif bad == "duplicate":
        manifest.write_text(manifest.read_text().replace(names[1], names[0]))
    with pytest.raises(ValueError):
        sar.discover_local(START, END, ("S1C",), tmp_path)


def empty_centres(path):
    """Blank the cell centres of one imagette file, as in real files with no OSW cell."""
    with netCDF4.Dataset(path, "a") as ds:
        ds["oswLon"][:] = float("nan")
        ds["oswLat"][:] = float("nan")


def test_imagette_without_cell_centres_is_skipped_and_listed(tmp_path):
    safe, names = archive(tmp_path)
    empty_centres(safe / "measurement" / names[0])
    products, provenance = sar.discover_local(START, END, ("S1C",), tmp_path)
    (product,) = products
    assert [row["Name"] for row in product["Imagettes"]] == [names[1]]
    (skipped,) = product["SkippedImagettes"]
    assert skipped["Name"] == names[0]
    assert skipped["index"] == 1
    assert skipped["reason"] == "no finite OSW cell centres"
    # The skipped file is still an input: a change to it is caught.
    assert len(provenance["ocn_inputs"]) == 2
    sar.verify_local_inputs(provenance)
    (safe / "measurement" / names[0]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        sar.verify_local_inputs(provenance)


def test_safe_with_only_empty_imagettes_has_none_left(tmp_path):
    safe, names = archive(tmp_path)
    for name in names:
        empty_centres(safe / "measurement" / name)
    products, _ = sar.discover_local(START, END, ("S1C",), tmp_path)
    (product,) = products
    assert product["Imagettes"] == []
    assert len(product["SkippedImagettes"]) == 2


def test_imagette_with_only_longitude_missing_is_skipped(tmp_path):
    safe, names = archive(tmp_path)
    with netCDF4.Dataset(safe / "measurement" / names[0], "a") as ds:
        ds["oswLon"][:] = float("nan")
    # A centre needs both coordinates, so no centre is finite here.
    products, _ = sar.discover_local(START, END, ("S1C",), tmp_path)
    assert len(products[0]["SkippedImagettes"]) == 1


def test_day_selection_inclusive_year_boundary_filters_before_nc(tmp_path):
    safe, _ = archive(tmp_path)
    target = tmp_path / "sentinel-1c/L2/WV/S1C_WV_OCN__2S/2024/366" / NAME
    target.parent.mkdir(parents=True)
    safe.rename(target)
    # Manifest is outside requested coverage; deliberately unreadable NCs must not be opened.
    for nc in target.glob("measurement/*.nc"):
        nc.write_bytes(b"not netcdf")
    products, provenance = sar.discover_local(
        "2024-12-31T23:59:00Z", "2025-01-01T00:01:00Z", ("S1C",), tmp_path
    )
    assert products == []
    assert [p.rsplit("/", 2)[-2:] for p in provenance["scanned_dirs"]] == [
        ["2024", "365"],
        ["2024", "366"],
        ["2025", "001"],
    ]
    assert len(provenance["missing_dirs"]) == 2
    assert provenance["ocn_inputs"] == []
    with pytest.raises(ValueError, match="No SAFEs found"):
        sar.discover_local(START, END, ("S1A",), tmp_path)


def test_midnight_overlap_uses_previous_archive_day(tmp_path):
    safe, names = archive(tmp_path)
    text = (safe / "manifest.safe").read_text()
    text = text.replace("2025-06-01T01:14:29", "2025-05-31T23:59:59")
    text = text.replace("2025-06-01T01:14:42", "2025-06-01T00:00:02")
    for name in names:
        old_path = safe / "measurement" / name
        with netCDF4.Dataset(old_path, "a") as ds:
            ds.firstMeasurementTime = "2025-05-31T23:59:59.000123Z"
            ds.lastMeasurementTime = "2025-06-01T00:00:02Z"
        new_name = name.replace(
            "20250601t011429-20250601t011442", "20250531t235959-20250601t000002"
        )
        old_path.rename(old_path.with_name(new_name))
        text = text.replace(name, new_name)
    (safe / "manifest.safe").write_text(text)
    name = NAME.replace("20250601T011429_20250601T011442", "20250531T235959_20250601T000002")
    target = safe.parent.parent / "151" / name
    target.parent.mkdir()
    safe.rename(target)
    products, _ = sar.discover_local(
        "2025-06-01T00:00:00Z", "2025-06-01T00:00:01Z", ("S1C",), tmp_path
    )
    assert len(products) == 1
    assert products[0]["Imagettes"][0]["ContentDate"]["Start"] == "2025-05-31T23:59:59.000123Z"


def test_nc_stat_change_blocks_publication(tmp_path):
    archive(tmp_path)
    _, provenance = sar.discover_local(START, END, ("S1C",), tmp_path)
    identity = provenance["ocn_inputs"][0]
    os.utime(identity["path"], ns=(identity["mtime_ns"], identity["mtime_ns"] + 1))
    with pytest.raises(ValueError, match="changed"):
        sar.verify_local_inputs(provenance)


def test_unlisted_nc_is_not_silently_ignored(tmp_path):
    safe, _ = archive(tmp_path)
    (safe / "measurement" / "unlisted.nc").write_bytes(b"incomplete archive")
    with pytest.raises(ValueError, match="unlisted|Unlisted"):
        sar.discover_local(START, END, ("S1C",), tmp_path)
