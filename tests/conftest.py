"""Synthetic NetCDF/XML fixtures for the local archive integration tests."""

import pytest
from datetime import date
import xml.etree.ElementTree as ET

import numpy as np
import xarray as xr

SAFE = "S1A_WV_OCN__2SSV_20260629T120000_20260629T121003_000001_000001_0001.SAFE"
NS = "http://www.esa.int/safe/sentinel-1.0"
GML = "http://www.opengis.net/gml"
XFDU = "urn:ccsds:schema:xfdu:1"


@pytest.fixture
def local_recipe(tmp_path):
    destination = (tmp_path / "synthetic").resolve()
    destination.mkdir(parents=True, exist_ok=False)
    day = date(2026, 6, 29)
    sar = (
        destination
        / "esa"
        / "sentinel-1a/L2/WV/S1A_WV_OCN__2S"
        / str(day.year)
        / day.strftime("%j")
        / SAFE
    )
    (sar / "measurement").mkdir(parents=True)
    names = []
    for index, (lon, lat, start, end) in enumerate(
        [
            (0.5, 0.5, "120000", "120003"),
            (179.9, 10.5, "121000", "121003"),
        ],
        1,
    ):
        name = f"s1a-wv{index}-ocn-vv-20260629t{start}-20260629t{end}-000001-000001-{index:03d}.nc"
        names.append(name)

        def stamp(clock):
            return f"2026-06-29T{clock[:2]}:{clock[2:4]}:{clock[4:]}Z"

        xr.Dataset(
            {
                "oswLon": (("oswAzSize", "oswRaSize"), [[lon]]),
                "oswLat": (("oswAzSize", "oswRaSize"), [[lat]]),
            },
            attrs={
                "firstMeasurementTime": stamp(start),
                "lastMeasurementTime": stamp(end),
                "synthetic_fixture": "true",
            },
        ).to_netcdf(sar / "measurement" / name)
    ET.register_namespace("safe", NS)
    ET.register_namespace("gml", GML)
    ET.register_namespace("xfdu", XFDU)
    root = ET.Element(
        f"{{{XFDU}}}XFDU",
        {
            "version": "esa/safe/sentinel-1.0/sentinel-1/sar/level-2/ocn/standard/wvsp",
            "synthetic_fixture": "true",
        },
    )
    package = ET.SubElement(root, "informationPackageMap")
    outer = ET.SubElement(
        package, f"{{{XFDU}}}contentUnit", {"unitType": "SAFE Archive Information Package"}
    )
    metadata = ET.SubElement(root, "metadataSection")

    def section(identity):
        node = ET.SubElement(metadata, "metadataObject", {"ID": identity})
        return ET.SubElement(
            ET.SubElement(node, "metadataWrap", {"mimeType": "text/xml"}), "xmlData"
        )

    period = ET.SubElement(section("acquisitionPeriod"), f"{{{NS}}}acquisitionPeriod")
    ET.SubElement(period, f"{{{NS}}}startTime").text = "2026-06-29T12:00:00.000000"
    ET.SubElement(period, f"{{{NS}}}stopTime").text = "2026-06-29T12:10:03.000000"
    platform = ET.SubElement(section("platform"), f"{{{NS}}}platform")
    ET.SubElement(platform, f"{{{NS}}}familyName").text = "SENTINEL-1"
    ET.SubElement(platform, f"{{{NS}}}number").text = "A"
    ET.SubElement(platform, f"{{{NS}}}mode").text = "WV"
    info = ET.SubElement(
        section("generalProductInformation"), f"{{{NS}}}standAloneProductInformation"
    )
    ET.SubElement(info, f"{{{NS}}}productType").text = "OCN"
    ET.SubElement(info, f"{{{NS}}}transmitterReceiverPolarisation").text = "VV"
    frames = ET.SubElement(section("measurementFrameSet"), f"{{{NS}}}frameSet")
    # Intentionally reverse frame order: the reader must use NC geolocation, not suffix order.
    for coordinates in ["10,179.8 10,-179.8 11,-179.8 11,179.8", "0,0 0,1 1,1 1,0"]:
        frame = ET.SubElement(frames, f"{{{NS}}}frame")
        footprint = ET.SubElement(
            frame, f"{{{NS}}}footPrint", {"srsName": "http://www.opengis.net/gml/srs/epsg.xml#4326"}
        )
        ET.SubElement(footprint, f"{{{GML}}}coordinates").text = coordinates
    objects = ET.SubElement(root, "dataObjectSection")
    for index, name in enumerate(names, 1):
        identity = f"measurement{index}"
        unit = ET.SubElement(outer, f"{{{XFDU}}}contentUnit", {"unitType": "Measurement Data Unit"})
        ET.SubElement(unit, "dataObjectPointer", {"dataObjectID": identity})
        obj = ET.SubElement(objects, "dataObject", {"ID": identity})
        stream = ET.SubElement(obj, "byteStream", {"mimeType": "application/x-netcdf"})
        ET.SubElement(
            stream, "fileLocation", {"locatorType": "URL", "href": f"./measurement/{name}"}
        )
    ET.indent(root)
    ET.ElementTree(root).write(sar / "manifest.safe", encoding="UTF-8", xml_declaration=True)

    reference = destination / "reference/hy-2b/hscat/25km/data" / str(day.year) / day.strftime("%j")
    reference.mkdir(parents=True)
    for index, wind in enumerate([5.0, 6.0], 1):
        values = {
            "lon": [0.5, 179.9, -179.9, 180.0, 0.5, 0.0, 10.0],
            "lat": [0.5, 10.5, 10.5, 10.5, 0.5, 0.5, 0.5],
            "time": np.array(
                ["2026-06-29T12:00:01"]
                + ["2026-06-29T12:10:01"] * 3
                + ["2026-06-29T12:20:00", "2026-06-29T12:00:01", "2026-06-29T12:00:01"],
                dtype="datetime64[ns]",
            ),
            "wind_speed": [wind] * 7,
            "wind_dir": [90.0] * 7,
            "wvc_quality_flag": [2048.0] + [0.0] * 6,
            "ice_prob": [0.5] * 7,
        }
        ds = xr.Dataset(
            {k: (("NUMROWS", "NUMCELLS"), [v]) for k, v in values.items()},
            attrs={
                "source": "HY-2B HSCAT",
                "software_identification_wind": "2202",
                "pixel_size_on_horizontal": "25.0 km",
                "start_date": "2026-06-29",
                "start_time": "12:00:00",
                "stop_date": "2026-06-29",
                "stop_time": "12:21:00",
                "synthetic_fixture": "true",
            },
        )
        ds.wind_speed.attrs["units"] = "m s-1"
        ds.wind_dir.attrs["units"] = "degree"
        ds.wind_dir.attrs["standard_name"] = "wind_to_direction"
        ds.lon.attrs["units"] = "degrees_east"
        ds.lat.attrs["units"] = "degrees_north"
        ds.ice_prob.attrs["units"] = "1"
        ds.to_netcdf(reference / f"SYNTHETIC_HSCAT_{index:03d}.nc")
    recipe = destination / "hscat.toml"
    recipe.write_text("""instrument = "hscat"
platform = "hy-2b"
resolution = "25km"
reference_root = "reference"
sar_archive_root = "esa"
start_date = "2026-06-29"
stop_date = "2026-06-30"
sar_missions = ["S1A"]
time_tolerance_minutes = 1
output_dir = "result"
""")
    return recipe
