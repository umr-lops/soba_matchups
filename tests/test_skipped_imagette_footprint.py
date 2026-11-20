"""A skipped imagette must not leave a hole in the footprint given to the SAR angle model."""

import json
from pathlib import Path

import netCDF4
import numpy as np

from soba_matchups import sar_metadata as sar
from soba_matchups.sar_angles import add_angles, subswath_of
from test_sar_metadata import END, NAME, START

FOOTPRINT = json.loads((Path(__file__).parent / "data" / "wv_footprint.json").read_text())[
    "footprint"
]
RINGS = [poly[0] for poly in FOOTPRINT["coordinates"]]
SKIPPED = 4  # imagette number whose file has no cell centre


def _centre(ring):
    """Return the mean (lon, lat) of a closed ring."""
    return (
        sum(x for x, _ in ring[:-1]) / (len(ring) - 1),
        sum(y for _, y in ring[:-1]) / (len(ring) - 1),
    )


def ten_imagette_archive(root):
    """Write a SAFE with the 10 real WV rings; imagette SKIPPED has no finite cell centre."""
    safe = root / "sentinel-1c/L2/WV/S1C_WV_OCN__2S/2025/152" / NAME
    (safe / "measurement").mkdir(parents=True)
    names = [
        f"s1c-wv{1 if i % 2 else 2}-ocn-vv-20250601t011429-20250601t011442-002582-0055bb-{i:03}.nc"
        for i in range(1, len(RINGS) + 1)
    ]
    # The manifest writes "lat,lon" pairs, four corners per frame (the ring closes itself).
    frames = ["".join(f"{y},{x} " for x, y in ring[:-1]).strip() for ring in RINGS]
    xml = '<XFDU xmlns:safe="http://www.esa.int/safe/sentinel-1.0" xmlns:gml="http://www.opengis.net/gml"><metadataSection>'
    metadata = {
        "acquisitionPeriod": (
            "<safe:acquisitionPeriod><safe:startTime>2025-06-01T01:14:29</safe:startTime>"
            "<safe:stopTime>2025-06-01T01:14:42</safe:stopTime></safe:acquisitionPeriod>"
        ),
        "platform": (
            "<safe:platform><safe:familyName>SENTINEL-1</safe:familyName>"
            "<safe:number>C</safe:number><safe:instrument><safe:mode>WV</safe:mode>"
            "</safe:instrument></safe:platform>"
        ),
        "generalProductInformation": (
            "<productType>OCN</productType>"
            "<transmitterReceiverPolarisation>VV</transmitterReceiverPolarisation>"
        ),
        "measurementFrameSet": "<safe:frameSet>"
        + "".join(
            '<safe:frame><safe:footPrint srsName="http://www.opengis.net/gml/srs/epsg.xml#4326">'
            f"<gml:coordinates>{f}</gml:coordinates></safe:footPrint></safe:frame>"
            for f in frames
        )
        + "</safe:frameSet>",
    }
    xml += "".join(
        f'<metadataObject ID="{key}"><metadataWrap><xmlData>{value}</xmlData>'
        "</metadataWrap></metadataObject>"
        for key, value in metadata.items()
    )
    xml += "</metadataSection><dataObjectSection>"
    xml += "".join(
        f'<dataObject ID="m{i}"><byteStream><fileLocation href="./measurement/{n}"/>'
        "</byteStream></dataObject>"
        for i, n in enumerate(names)
    )
    xml += "</dataObjectSection><informationPackageMap>"
    xml += "".join(
        f'<contentUnit unitType="Measurement Data Unit"><dataObjectPointer dataObjectID="m{i}"/>'
        "</contentUnit>"
        for i in range(len(names))
    )
    xml += "</informationPackageMap></XFDU>"
    (safe / "manifest.safe").write_text(xml)
    for number, (name, ring) in enumerate(zip(names, RINGS), 1):
        lon, lat = _centre(ring)
        if number == SKIPPED:
            lon = lat = float("nan")
        with netCDF4.Dataset(safe / "measurement" / name, "w") as ds:
            ds.createDimension("cell", 1)
            ds.createVariable("oswLon", "f8", ("cell",))[:] = lon
            ds.createVariable("oswLat", "f8", ("cell",))[:] = lat
            ds.firstMeasurementTime = START
            ds.lastMeasurementTime = END
            ds.createVariable("oswWindSpeed", "f8", ("cell",))[:] = 999
    return names


def test_skipped_imagette_keeps_its_ring_in_the_footprint(tmp_path):
    """The footprint for the angle model holds all 10 rings, in order, skipped one included."""
    ten_imagette_archive(tmp_path)
    (product,), _ = sar.discover_local(START, END, ("S1C",), tmp_path)
    assert [row["index"] for row in product["Imagettes"]] == [
        i for i in range(1, 11) if i != SKIPPED
    ]
    assert [row["index"] for row in product["SkippedImagettes"]] == [SKIPPED]
    rings = [poly[0] for poly in product["WVFootprint"]["coordinates"]]
    assert len(rings) == len(RINGS)
    # Same corners, same order, as the catalogue footprint the Parquet path would pass.
    for ring, real in zip(rings, RINGS):
        assert np.allclose(sorted(map(tuple, ring[:4])), sorted(map(tuple, real[:4])))


def test_points_after_the_skipped_imagette_still_get_matching_angles(tmp_path):
    """Every kept imagette is found by the model under its own number and subswath."""
    ten_imagette_archive(tmp_path)
    (product,), _ = sar.discover_local(START, END, ("S1C",), tmp_path)
    points, expected = [], []
    for row in product["Imagettes"]:
        lon, lat = row["Centre"]
        points.append({"ref_lon": lon, "ref_lat": lat})
        expected.append((row["index"], subswath_of(row["Name"])))
    assert add_angles(NAME, product["WVFootprint"], points, expected) is None
    assert all(p["sar_incidence_angle"] is not None for p in points)
    assert len(points) == len(RINGS) - 1


def test_a_footprint_with_the_skipped_ring_removed_never_gives_wrong_angles(tmp_path):
    """Dropping a ring is refused by the model or caught by the check: no point keeps an angle."""
    ten_imagette_archive(tmp_path)
    (product,), _ = sar.discover_local(START, END, ("S1C",), tmp_path)
    holed = {
        "type": "MultiPolygon",
        "coordinates": [
            poly for k, poly in enumerate(product["WVFootprint"]["coordinates"], 1) if k != SKIPPED
        ],
    }
    points, expected = [], []
    for row in product["Imagettes"]:
        lon, lat = row["Centre"]
        points.append({"ref_lon": lon, "ref_lat": lat})
        expected.append((row["index"], subswath_of(row["Name"])))
    error = add_angles(NAME, holed, points, expected)
    # Either the library rejects the broken alternation, or the numbering check nulls the
    # points placed on the wrong imagette; a ring that vanished is never silent.
    assert error
    later = [p for p, (index, _) in zip(points, expected) if index > SKIPPED]
    assert later and all(p["sar_incidence_angle"] is None for p in later)
