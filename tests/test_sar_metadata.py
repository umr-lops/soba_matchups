"""Local native XML/NetCDF controls; no catalogue or suffix geometry assumptions."""

from pathlib import Path
import xml.etree.ElementTree as ET

import netCDF4
import pytest
from shapely.geometry import Point, shape

from soba_matchups import sar_metadata as sar

START = "2025-06-01T01:14:29Z"
END = "2025-06-01T01:14:42Z"
NAME = "S1C_WV_OCN__2SSV_20250601T011429_20250601T011442_002582_0055BB_ABCD.SAFE"


def archive(root, frames=("0,3 0,4 1,4 1,3", "0,0 0,1 1,1 1,0"), centres=((0.5, 0.5), (3.5, 0.5))):
    safe = root / "sentinel-1c/L2/WV/S1C_WV_OCN__2S/2025/152" / NAME
    (safe / "measurement").mkdir(parents=True)
    names = [
        f"s1c-wv{i}-ocn-vv-20250601t011429-20250601t011442-002582-0055bb-{i:03}.nc" for i in (1, 2)
    ]
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
            f'<safe:frame><safe:footPrint srsName="http://www.opengis.net/gml/srs/epsg.xml#4326"><gml:coordinates>{f}</gml:coordinates></safe:footPrint></safe:frame>'
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
        f'<dataObject ID="m{i}"><byteStream>'
        f'<fileLocation href="./measurement/{n}"/></byteStream></dataObject>'
        for i, n in enumerate(names)
    )
    xml += "</dataObjectSection><informationPackageMap>"
    xml += "".join(
        '<contentUnit unitType="Measurement Data Unit">'
        f'<dataObjectPointer dataObjectID="m{i}"/></contentUnit>'
        for i in range(2)
    )
    xml += "</informationPackageMap></XFDU>"
    (safe / "manifest.safe").write_text(xml)
    for name, (lon, lat) in zip(names, centres):
        with netCDF4.Dataset(safe / "measurement" / name, "w") as ds:
            ds.createDimension("cell", 1)
            ds.createVariable("oswLon", "f8", ("cell",))[:] = lon
            ds.createVariable("oswLat", "f8", ("cell",))[:] = lat
            ds.firstMeasurementTime = START
            ds.lastMeasurementTime = END
            ds.createVariable("oswWindSpeed", "f8", ("cell",))[:] = 999
    return safe, names


def test_native_centres_not_frame_order_and_verified_inputs(tmp_path):
    safe, names = archive(tmp_path)
    products, provenance = sar.discover_local(START, END, ("S1C",), tmp_path)
    (product,) = products
    assert product["Name"] == NAME
    assert product["ContentDate"] == {"Start": START, "End": END}
    assert product["Id"].startswith("local_")
    for row, lon in zip(product["Imagettes"], (0.5, 3.5)):
        assert shape(row["GeoFootprint"]).covers(Point(lon, 0.5))
        assert row["Name"] in names
        assert row["href"].startswith("file:")
        assert row["mapping_policy"] == "local_ocn_geolocation_to_manifest_frame"
    assert shape(product["GeoFootprint"]).area == 2
    assert provenance["source"] == "local_ocn"
    assert len(provenance["manifest_inputs"]) == 1
    assert len(provenance["ocn_inputs"]) == 2
    sar.verify_local_inputs(provenance)
    (safe / "manifest.safe").write_text((safe / "manifest.safe").read_text() + " ")
    with pytest.raises(ValueError, match="changed"):
        sar.verify_local_inputs(provenance)


@pytest.mark.parametrize(
    "start_offset,stop_offset,valid", [(0, 1, True), (1, 1, False), (0, -1, False)]
)
def test_native_orbit_start_matches_filename_but_stop_can_differ(
    tmp_path, start_offset, stop_offset, valid
):
    safe, _ = archive(tmp_path)
    path = safe / "manifest.safe"
    root = ET.parse(path).getroot()
    metadata = root.find("metadataSection")
    node = ET.SubElement(metadata, "metadataObject", {"ID": "measurementOrbitReference"})
    orbit = ET.SubElement(node, "{http://www.esa.int/safe/sentinel-1.0}orbitReference")
    expected = int(NAME.split("_")[7])
    for kind, offset in [("start", start_offset), ("stop", stop_offset)]:
        ET.SubElement(
            orbit, "{http://www.esa.int/safe/sentinel-1.0}orbitNumber", {"type": kind}
        ).text = str(expected + offset)
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)
    if valid:
        products, _ = sar.discover_local(START, END, ("S1C",), tmp_path)
        assert len(products) == 1
    else:
        with pytest.raises(ValueError, match="orbit"):
            sar.discover_local(START, END, ("S1C",), tmp_path)


def test_whole_second_nc_header_can_precede_fractional_manifest_start(tmp_path):
    safe, _ = archive(tmp_path)
    manifest = safe / "manifest.safe"
    text = manifest.read_text().replace(
        "2025-06-01T01:14:29</safe:startTime>",
        "2025-06-01T01:14:29.013725</safe:startTime>",
    )
    manifest.write_text(text)
    products, _ = sar.discover_local(START, END, ("S1C",), tmp_path)
    assert len(products) == 1
    assert products[0]["Imagettes"][0]["ContentDate"]["Start"] == START


def test_safe_fields_preserved():
    assert sar.decode_safe_name(NAME) == {"polarization": "VV", "producttype": "OCN"}
    assert sar.decode_safe_name(NAME.replace("OCN__2", "SLC__1"))["producttype"] == "SLC"


def test_real_slc_manifest_structure_only():
    path = Path("/mnt/z/shared/manifest.safe")
    if not path.exists():
        pytest.skip("external read-only SLC structural example unavailable")
    frames = sar.read_manifest_frames(path)
    assert len(frames) == 104
    assert all(shape(f).is_valid for f in frames)
    assert shape(frames[0]).covers(Point(-103.1, 14.4)) or shape(frames[0]).bounds[0] < -100
    root = ET.parse(path).getroot()
    units = [e for e in root.iter() if e.attrib.get("unitType") == "Measurement Data Unit"]
    assert len(units) == 104
