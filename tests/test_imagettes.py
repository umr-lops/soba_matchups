"""Native geolocation edge cases and archive-day boundaries."""

import os

import netCDF4
import numpy as np
import pytest
from shapely.geometry import Point, box, mapping, shape

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


def test_file_that_copies_another_centre_is_skipped_and_listed(tmp_path):
    # Both files sit in the frame of imagette 2; only file 002 carries that number.
    safe, names = archive(tmp_path, centres=((0.5, 0.5), (0.5, 0.5)))
    products, provenance = sar.discover_local(START, END, ("S1C",), tmp_path)
    (product,) = products
    assert [row["Name"] for row in product["Imagettes"]] == [names[1]]
    (skipped,) = product["SkippedImagettes"]
    assert skipped["Name"] == names[0]
    assert skipped["index"] == 1
    assert skipped["reason"] == "OSW centres lie in the frame of imagette 2"
    assert len(provenance["ocn_inputs"]) == 2


def test_two_files_in_one_frame_with_no_matching_number_stop_the_run(tmp_path):
    safe, names = archive(tmp_path, centres=((0.5, 0.5), (0.5, 0.5)))
    # Renumber file 002 as 005: neither file is now numbered like the frame (number 2).
    renamed = names[1].replace("-002.nc", "-005.nc")
    (safe / "measurement" / names[1]).rename(safe / "measurement" / renamed)
    manifest = safe / "manifest.safe"
    manifest.write_text(manifest.read_text().replace(names[1], renamed))
    with pytest.raises(ValueError, match="one-to-one"):
        sar.discover_local(START, END, ("S1C",), tmp_path)


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


def add_wind_grid(path, lon, lat):
    """Give one imagette file a small wind grid centred on (lon, lat), as real OCN files have."""
    with netCDF4.Dataset(path, "a") as ds:
        ds.createDimension("az", 3)
        ds.createDimension("ra", 3)
        grid_lon = ds.createVariable("owiLon", "f8", ("az", "ra"))
        grid_lat = ds.createVariable("owiLat", "f8", ("az", "ra"))
        offsets = [-0.1, 0.0, 0.1]
        grid_lon[:] = [[lon + dx for dx in offsets] for _ in offsets]
        grid_lat[:] = [[lat + dy] * 3 for dy in offsets]


def test_osw_centre_in_no_frame_takes_the_wind_grid_centre(tmp_path, caplog):
    # File 001 belongs to frame 1 (lon 3..4) but its OSW point is in no frame; file 002 is fine.
    safe, names = archive(tmp_path, centres=((2.0, 5.0), (0.5, 0.5)))
    add_wind_grid(safe / "measurement" / names[0], 3.5, 0.5)
    with caplog.at_level("WARNING", logger="soba_matchups.sar_metadata"):
        products, provenance = sar.discover_local(START, END, ("S1C",), tmp_path)
    (product,) = products
    assert product["SkippedImagettes"] == []
    first = next(row for row in product["Imagettes"] if row["index"] == 1)
    assert first["Centre"] == pytest.approx([3.5, 0.5])
    assert shape(first["GeoFootprint"]).covers(Point(3.5, 0.5))
    assert first["CentreRepair"]["osw_centre"] == [2.0, 5.0]
    assert first["CentreRepair"]["offset_km"] > 100
    other = next(row for row in product["Imagettes"] if row["index"] == 2)
    assert "CentreRepair" not in other
    assert provenance["imagettes_centre_repaired"] == [
        {
            "safe": NAME,
            "index": 1,
            "name": names[0],
            "osw_offset_km": first["CentreRepair"]["offset_km"],
        }
    ]
    assert "taken from the wind grid" in caplog.text
    sar.verify_local_inputs(provenance)


def test_osw_centre_in_no_frame_and_no_wind_grid_skips_the_file(tmp_path):
    safe, names = archive(tmp_path, centres=((2.0, 5.0), (0.5, 0.5)))
    products, provenance = sar.discover_local(START, END, ("S1C",), tmp_path)
    (product,) = products
    assert [row["index"] for row in product["Imagettes"]] == [2]
    (skipped,) = product["SkippedImagettes"]
    assert skipped["index"] == 1 and "no frame" in skipped["reason"]
    assert provenance["imagettes_centre_repaired"] == []


def test_wind_grid_in_another_frame_is_not_trusted(tmp_path):
    # The grid of file 001 lies in frame 2, not in the frame of its own number.
    safe, names = archive(tmp_path, centres=((2.0, 5.0), (0.5, 0.5)))
    add_wind_grid(safe / "measurement" / names[0], 0.5, 0.5)
    products, _ = sar.discover_local(START, END, ("S1C",), tmp_path)
    (product,) = products
    assert [row["index"] for row in product["Imagettes"]] == [2]
    assert [row["index"] for row in product["SkippedImagettes"]] == [1]


def test_repaired_file_wins_a_clash_over_a_file_that_copies_its_frame(tmp_path):
    # Files 001 and 002 both end up in frame 1; file 001 is the one numbered like it.
    safe, names = archive(tmp_path, centres=((2.0, 5.0), (3.5, 0.5)))
    add_wind_grid(safe / "measurement" / names[0], 3.5, 0.5)
    products, _ = sar.discover_local(START, END, ("S1C",), tmp_path)
    (product,) = products
    assert [row["Name"] for row in product["Imagettes"]] == [names[0]]
    assert [row["Name"] for row in product["SkippedImagettes"]] == [names[1]]


def test_wind_grid_across_the_dateline_gives_a_centre_in_range(tmp_path):
    # Frame 1 is split at 180. The wind grid has columns on both sides (179.9, 180.0, -179.9).
    safe, names = archive(
        tmp_path,
        frames=("0,179 0,-179 1,-179 1,179", "0,3 0,4 1,4 1,3"),
        centres=((100.0, 50.0), (3.5, 0.5)),
    )
    with netCDF4.Dataset(safe / "measurement" / names[0], "a") as ds:
        ds.createDimension("az", 2)
        ds.createDimension("ra", 3)
        ds.createVariable("owiLon", "f8", ("az", "ra"))[:] = [[179.9, 180.0, -179.9]] * 2
        ds.createVariable("owiLat", "f8", ("az", "ra"))[:] = [[0.4] * 3, [0.6] * 3]
    products, _ = sar.discover_local(START, END, ("S1C",), tmp_path)
    (product,) = products
    first = next(row for row in product["Imagettes"] if row["index"] == 1)
    # Averaged on 0..360 the columns give 180, which is written as -180, not as the plain mean 60.
    assert abs(first["Centre"][0]) == pytest.approx(180)
    assert first["Centre"][1] == pytest.approx(0.5)
    assert product["SkippedImagettes"] == []


def test_wind_grid_helper_refuses_missing_or_mismatched_input():
    frames = [mapping(box(0, 0, 1, 1))]
    good = (np.array([[0.4, 0.6]]), np.array([[0.5, 0.5]]))
    assert sar._wind_grid_centre(good, 1, frames) == pytest.approx((0.5, 0.5))
    assert sar._wind_grid_centre(None, 1, frames) is None
    assert sar._wind_grid_centre(good, 2, frames) is None
    assert sar._wind_grid_centre((good[0], np.array([[0.5]])), 1, frames) is None
    assert sar._wind_grid_centre((np.full((1, 2), np.nan), good[1]), 1, frames) is None
