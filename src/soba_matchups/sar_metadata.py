"""Global SAFE-level CDSE discovery with exact-query, complete-only snapshots."""

import hashlib
import json
import math
import os
import re
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import UUID

import requests
from shapely.geometry import shape
from shapely.validation import explain_validity

API_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"

_PATTERN = re.compile(
    r"S1[ABCD]_WV_(?P<producttype>OCN|SLC)__"
    r"(?P<level>[12])[A-Z](?P<pol>SV|SH)_"
    r"\d{8}T\d{6}_\d{8}T\d{6}_\d{6}_[0-9A-F]{6}_[0-9A-F]{4}\.SAFE"
)


def decode_safe_name(name):
    match = _PATTERN.fullmatch(name) if isinstance(name, str) else None
    if match is None or match["level"] != {"OCN": "2", "SLC": "1"}[match["producttype"]]:
        raise ValueError(f"Unsupported Sentinel-1 WV SAFE name: {name!r}")
    return {
        "polarization": {"SV": "VV", "SH": "HH"}[match["pol"]],
        "producttype": match["producttype"],
    }


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _utc(value):
    if not isinstance(value, str) or "T" not in value:
        raise ValueError(f"Expected UTC ISO8601 timestamp, got {value!r}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timedelta(0):
        raise ValueError(f"Expected timezone-aware UTC timestamp, got {value!r}")
    return parsed


def _validate(products, start, end, missions):
    if not isinstance(products, list):
        raise ValueError("CDSE products must be a list")
    seen = set()
    for item in products:
        identity = item.get("Id") if isinstance(item, dict) else None
        try:
            if not isinstance(identity, str) or str(UUID(identity)) != identity.lower():
                raise ValueError("Id must be a canonical UUID")
            if identity in seen:
                raise ValueError("duplicate Id in snapshot")
            seen.add(identity)
            name = item["Name"]
            if (
                not isinstance(name, str)
                or "_WV_OCN" not in name
                or not name.endswith(".SAFE")
                or not any(name.startswith(m + "_") for m in missions)
            ):
                raise ValueError("Name must be a requested mission WV_OCN SAFE")
            first, last = (_utc(item["ContentDate"][key]) for key in ("Start", "End"))
            if not (first < last and first < end and last > start):
                raise ValueError("ContentDate must be increasing and overlap requested coverage")
            geometry = item["GeoFootprint"]
            if not isinstance(geometry, dict) or geometry.get("type") not in {
                "Polygon",
                "MultiPolygon",
            }:
                raise ValueError("GeoFootprint must be Polygon or MultiPolygon")
            polygons = geometry["coordinates"]
            if geometry["type"] == "Polygon":
                polygons = [polygons]
            if not polygons:
                raise ValueError("empty GeoFootprint")
            for polygon in polygons:
                if not polygon:
                    raise ValueError("empty GeoFootprint polygon")
                for ring in polygon:
                    if len(ring) < 4 or ring[0] != ring[-1]:
                        raise ValueError("GeoFootprint rings must be closed with >=4 positions")
                    for position in ring:
                        if (
                            len(position) not in (2, 3)
                            or not all(
                                type(v) in (int, float) and math.isfinite(v) for v in position
                            )
                            or not -180 <= position[0] <= 180
                            or not -90 <= position[1] <= 90
                        ):
                            raise ValueError("GeoFootprint coordinates must be finite lon/lat")
            footprint = shape(geometry)
            if footprint.is_empty or not footprint.is_valid:
                raise ValueError(f"invalid GeoFootprint: {explain_validity(footprint)}")
        except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
            raise ValueError(f"CDSE invalid product {identity!r}: {exc}") from exc


def _page(session, url, params):
    for attempt in range(3):
        try:
            response = session.get(url, params=params, timeout=(10, 60), allow_redirects=False)
            if response.status_code in {408, 429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError(f"CDSE unexpected HTTP {response.status_code}: {url}")
            return response.json()
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == 2:
                raise ValueError(f"CDSE request failed after 3 attempts: {url}: {exc}") from exc
            time.sleep(2**attempt)
        except (requests.RequestException, ValueError) as exc:
            raise ValueError(f"CDSE request/JSON failure: {url}: {exc}") from exc


def _collect(query):
    products = {}
    seen = set()
    url, params = API_URL, query["params"]
    total = None
    with requests.Session() as session:
        while url:
            if url in seen:
                raise ValueError(f"CDSE pagination loop: {url}")
            seen.add(url)
            page = _page(session, url, params)
            if not isinstance(page, dict) or not isinstance(page.get("value"), list):
                raise ValueError(f"CDSE page missing list-valued 'value': {url}")
            if "error" in page:
                raise ValueError(f"CDSE API error: {page['error']}")
            if "@odata.count" in page:
                count = page["@odata.count"]
                if type(count) is not int or count < 0 or (total is not None and count != total):
                    raise ValueError(f"CDSE inconsistent declared total: {count!r}")
                total = count
            for item in page["value"]:
                if not isinstance(item, dict) or not isinstance(item.get("Id"), str):
                    raise ValueError("CDSE product missing string Id")
                identity = item["Id"]
                if identity in products and products[identity] != item:
                    raise ValueError(f"CDSE conflicting duplicate Id: {identity}")
                products[identity] = item
            link = page.get("@odata.nextLink")
            if link is None:
                break
            if not isinstance(link, str) or not link:
                raise ValueError("CDSE malformed continuation link")
            url = urljoin(url, link)
            target = urlsplit(url)
            if (
                target.scheme != "https"
                or target.netloc != urlsplit(API_URL).netloc
                or target.path != urlsplit(API_URL).path
                or target.fragment
            ):
                raise ValueError(f"CDSE unsafe continuation link: {url}")
            params = None
    if total is None:
        raise ValueError("CDSE response missing requested declared total")
    if len(products) != total:
        raise ValueError(f"CDSE declared total {total} != {len(products)} unique products")
    return list(products.values())


def discover(
    start: str, end: str, missions: tuple[str, ...], cache_dir: Path, offline: bool = False
) -> tuple[list[dict], dict]:
    """Return native products and provenance; offline never contacts the network."""
    timestamp = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z"
    if any(not isinstance(v, str) or not re.fullmatch(timestamp, v) for v in (start, end)):
        raise ValueError("start/end must be expanded UTC ISO8601 Z timestamps")
    first, last = _utc(start), _utc(end)
    if first >= last:
        raise ValueError("start must precede end")
    if (
        not isinstance(missions, tuple)
        or not missions
        or any(m not in {"S1A", "S1B", "S1C", "S1D"} for m in missions)
    ):
        raise ValueError("missions must be a nonempty tuple of S1A/S1B/S1C/S1D")
    missions = tuple(sorted(set(missions)))
    prefixes = " or ".join(f"startswith(Name,'{mission}_')" for mission in missions)
    query = {
        "cache_schema_version": 2,
        "endpoint": API_URL,
        "missions": list(missions),
        "params": {
            "$filter": "Collection/Name eq 'SENTINEL-1' and contains(Name,'_WV_OCN') "
            f"and ({prefixes}) and ContentDate/Start lt {end} "
            f"and ContentDate/End gt {start}",
            "$top": 100,
            "$count": "true",
            "$orderby": "ContentDate/Start asc",
        },
    }
    cache_file = Path(cache_dir).resolve() / (hashlib.sha256(_encoded(query)).hexdigest() + ".json")
    if cache_file.exists():
        try:
            raw = cache_file.read_bytes()
            snapshot = json.loads(raw)
            products = snapshot["products"]
            if (
                snapshot["query"] != query
                or snapshot["complete"] is not True
                or type(snapshot["count"]) is not int
                or snapshot["count"] != len(products)
                or snapshot["content_sha256"] != hashlib.sha256(_encoded(products)).hexdigest()
            ):
                raise ValueError("query, completeness, count or content hash mismatch")
            _validate(products, first, last, missions)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ValueError(
                f"Invalid cache {cache_file}; remove it and retry online: {exc}"
            ) from exc
        source = "cache"
    else:
        if offline:
            raise ValueError(f"No complete exact-query cache available: {cache_file}")
        products = _collect(query)
        _validate(products, first, last, missions)
        snapshot = {
            "query": query,
            "complete": True,
            "products": products,
            "count": len(products),
            "content_sha256": hashlib.sha256(_encoded(products)).hexdigest(),
        }
        raw = _encoded(snapshot)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=cache_file.parent, delete=False) as stream:
                temporary = stream.name
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, cache_file)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        source = "online"
    return products, {
        "cache_file": str(cache_file),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "source": source,
        "query": query,
        "complete": True,
        "coverage": {"start": start, "end": end},
    }
