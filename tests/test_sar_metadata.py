"""Synthetic native CDSE controls only; these are not live catalogue observations."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
import requests

from soba_matchups import sar_metadata

START = "2020-01-10T00:00:00Z"
END = "2020-01-11T00:00:00Z"


def product(number=1):
    return {
        "Id": f"00000000-0000-0000-0000-{number:012d}",
        "Name": f"S1A_WV_OCN__2SSV_SYNTHETIC_CONTROL_{number}.SAFE",
        "ContentDate": {"Start": "2020-01-10T01:00:00Z", "End": "2020-01-10T01:20:00Z"},
        "GeoFootprint": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
    }


def mock_pages(monkeypatch, pages):
    calls = []
    iterator = iter(pages)

    def get(self, url, **kwargs):
        calls.append((url, kwargs))
        value = next(iterator)
        if isinstance(value, Exception):
            raise value
        response = requests.Response()
        response.status_code = value if isinstance(value, int) else 200
        response.url = url
        response._content = json.dumps(value).encode()
        return response

    monkeypatch.setattr(requests.Session, "get", get)
    return calls


def test_online_snapshot_and_exact_offline_replay(tmp_path, monkeypatch):
    calls = mock_pages(monkeypatch, [{"value": [product()], "@odata.count": 1}])
    products, provenance = sar_metadata.discover(START, END, ("S1A",), tmp_path)
    assert products == [product()]
    assert provenance["source"] == "online"
    assert provenance["complete"] is True
    assert provenance["coverage"] == {"start": START, "end": END}
    cache_file = Path(provenance["cache_file"])
    assert cache_file.is_absolute() and cache_file.exists()
    assert provenance["sha256"] == hashlib.sha256(cache_file.read_bytes()).hexdigest()
    assert calls[0][1]["params"]["$top"] == 100
    assert calls[0][1]["params"]["$count"] == "true"
    assert calls[0][1]["params"]["$orderby"] == "ContentDate/Start asc"
    query = calls[0][1]["params"]["$filter"]
    assert f"ContentDate/Start lt {END}" in query
    assert f"ContentDate/End gt {START}" in query
    assert "Intersects" not in query
    again, replay = sar_metadata.discover(START, END, ("S1A",), tmp_path, offline=True)
    assert again == products and replay["source"] == "cache"
    assert replay["sha256"] == provenance["sha256"]
    assert len(calls) == 1
    with pytest.raises(ValueError, match="cache"):
        sar_metadata.discover(START, END, ("S1B",), tmp_path, offline=True)
    original = json.loads(cache_file.read_text())
    for field, value in [
        ("complete", False),
        ("count", 999),
        ("query", {}),
        ("content_sha256", "invalid"),
        ("products", []),
    ]:
        corrupted = dict(original, **{field: value})
        cache_file.write_text(json.dumps(corrupted))
        with pytest.raises(ValueError, match="cache"):
            sar_metadata.discover(START, END, ("S1A",), tmp_path, offline=True)
    assert len(calls) == 1
    corrupted = copy.deepcopy(original)
    corrupted["products"][0]["ContentDate"]["End"] = START
    corrupted["content_sha256"] = hashlib.sha256(
        json.dumps(corrupted["products"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    cache_file.write_text(json.dumps(corrupted))
    with pytest.raises(ValueError, match="cache"):
        sar_metadata.discover(START, END, ("S1A",), tmp_path, offline=True)
    assert len(calls) == 1


def test_all_pages_preserve_multipolygon_and_deduplicate_identical_ids(tmp_path, monkeypatch):
    second = product(2)
    second["GeoFootprint"] = {
        "type": "MultiPolygon",
        "coordinates": [
            product()["GeoFootprint"]["coordinates"],
            [[[10, 0], [11, 0], [11, 1], [10, 1], [10, 0]]],
        ],
    }
    continuation = sar_metadata.API_URL + "?$skip=100"
    calls = mock_pages(
        monkeypatch,
        [
            {"value": [product()], "@odata.count": 2, "@odata.nextLink": continuation},
            {"value": [product(), second], "@odata.count": 2},
        ],
    )
    products, _ = sar_metadata.discover(START, END, ("S1A",), tmp_path)
    assert products == [product(), second]
    assert len(calls) == 2 and calls[1][0] == continuation
    assert not calls[1][1].get("params")


def test_bad_metadata_or_incomplete_pages_never_create_snapshot(tmp_path, monkeypatch):
    controls = [
        [{"value": []}],  # Missing requested total must not become a successful empty search.
        [{"error": {"message": "synthetic API error"}}],
        [{"value": None}],
        [{"value": [product()], "@odata.count": 2}],
        [{"value": [], "@odata.count": True}],
        [{"value": [], "@odata.nextLink": "http://catalogue.dataspace.copernicus.eu/a"}],
        [{"value": [], "@odata.nextLink": "https://untrusted.example/a"}],
        [{"value": [], "@odata.nextLink": sar_metadata.API_URL}],
        [{"value": [], "@odata.nextLink": 12}],
        [
            {"value": [product()], "@odata.nextLink": sar_metadata.API_URL + "?$skip=1"},
            {"value": [dict(product(), Name="S1A_WV_OCN_CONFLICT.SAFE")]},
        ],
        [
            {"value": [], "@odata.count": 0, "@odata.nextLink": sar_metadata.API_URL + "?$skip=1"},
            {"value": [], "@odata.count": 1},
        ],
    ]
    bad_products = [
        dict(product(), Id="not-a-uuid"),
        dict(product(), Name="S1B_WV_OCN.SAFE"),
        dict(product(), Name="S1A_IW_OCN.SAFE"),
        dict(product(), ContentDate={"Start": START, "End": START}),
    ]
    for coordinates in [
        [],
        [[[0, 0], [2, 2], [0, 2], [2, 0], [0, 0]]],
        [[[181, 0], [182, 0], [182, 1], [181, 0]]],
        [[[0, 0], [1, 0], [1, float("nan")], [0, 0]]],
    ]:
        bad_products.append(
            dict(product(), GeoFootprint={"type": "Polygon", "coordinates": coordinates})
        )
    bad_date = copy.deepcopy(product())
    bad_date["ContentDate"]["Start"] = "2020-01-10T01:00:00"
    bad_products.append(bad_date)
    controls.extend([{"value": [item]}] for item in bad_products)
    for pages in controls:
        mock_pages(monkeypatch, pages)
        with pytest.raises(ValueError):
            sar_metadata.discover(START, END, ("S1A",), tmp_path)
        assert not list(tmp_path.glob("*.json"))
    for start, end, missions in [
        (START[:10], END, ("S1A",)),
        (END, START, ("S1A",)),
        (START, END, ()),
        (START, END, ("bogus",)),
    ]:
        calls = mock_pages(monkeypatch, [])
        with pytest.raises(ValueError):
            sar_metadata.discover(start, end, missions, tmp_path)
        assert not calls


def test_http_failure_is_not_an_empty_catalogue(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    for failure in [503, 400, requests.Timeout("synthetic timeout")]:
        calls = mock_pages(monkeypatch, [failure] * 3)
        with pytest.raises(ValueError, match="CDSE"):
            sar_metadata.discover(START, END, ("S1A",), tmp_path)
        assert len(calls) == (1 if failure == 400 else 3)
        assert all(call[1]["timeout"] == (10, 60) for call in calls)
        assert not list(tmp_path.glob("*.json"))
    calls = mock_pages(monkeypatch, [503, {"value": [], "@odata.count": 0}])
    products, provenance = sar_metadata.discover(START, END, ("S1A",), tmp_path)
    assert products == [] and provenance["complete"] is True and len(calls) == 2


def test_safe_fields_are_extracted_not_hardcoded():
    from soba_matchups.sar_metadata import decode_safe_name

    name = "S1A_WV_OCN__2SSV_20200110T120000_20200110T123000_000001_000001_0001.SAFE"
    assert decode_safe_name(name) == {"polarization": "VV", "producttype": "OCN"}
    assert decode_safe_name(name.replace("2SSV", "2SSH"))["polarization"] == "HH"
    assert decode_safe_name(name.replace("OCN__2", "SLC__1"))["producttype"] == "SLC"
    with pytest.raises(ValueError, match="SAFE name"):
        decode_safe_name(name.replace("2SSV", "2SSX"))
