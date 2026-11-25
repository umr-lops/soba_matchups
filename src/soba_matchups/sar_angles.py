"""SAR incidence, elevation and ground heading of matched points, from the s1-geometry library.

The library works from the product footprint alone and needs no orbit file: for WV one ring per
imagette, in time order; for an IW, EW or SM scene its four corners. A product it cannot handle
never stops a run: its points get null angles and the product is named in the manifest.
"""

import logging
import re

import numpy as np
from shapely.geometry import Point, Polygon

logger = logging.getLogger(__name__)

# Point fields written to the Items, with their units.
FIELDS = {
    "sar_incidence_angle": "degrees, from the local vertical",
    "sar_elevation_angle": "degrees, off-nadir angle at the satellite (ESA OCN convention)",
    "sar_ground_heading": "degrees clockwise from north",
    "sar_ground_heading_uncertainty": (
        "degrees: WV, p95 of the s1-geometry WV heading error; IW/EW/SM, the library's"
        " latitude-dependent uncertainty of its IW heading model"
    ),
    "sar_wv_subswath": "WV subswath of the imagette, 1 or 2; null for IW, EW and SM",
}
# The model fits the track on the two subswath chains, so it needs two imagettes of each.
MIN_IMAGETTES = 4
# Largest relative difference between the swath width the library measured and the scene's own
# across-track width: beyond it the library measured across the wrong pair of edges.
SWATH_WIDTH_TOLERANCE = 0.10
_SUBSWATH = re.compile(r"-wv(\d)-")


def subswath_of(name):
    """Return the WV subswath (1 or 2) named in a measurement file name, or None."""
    match = _SUBSWATH.search(name or "")
    return int(match[1]) if match else None


def footprint_from_frames(frames):
    """Return the GeoJSON MultiPolygon of the imagette rings, in the order of the frames.

    A frame cut at the dateline is a MultiPolygon; its pieces stay next to each other, which
    is how the library re-joins them.
    """
    rings = []
    for frame in frames:
        pieces = frame["coordinates"] if frame["type"] == "MultiPolygon" else [frame["coordinates"]]
        rings.extend([piece[0]] for piece in pieces)
    return {"type": "MultiPolygon", "coordinates": rings}


def _report(name, text, warning=False):
    """Log why a product has no angles, as a warning when that is expected, and return the text."""
    (logger.warning if warning else logger.error)("SAR angles unavailable for %s: %s", name, text)
    return text


def _finite(value):
    """Return a float, or None when the value is not finite."""
    return float(value) if np.isfinite(value) else None


def add_angles(name, footprint, points, expected=None):
    """Write the SAR angle fields on each point in place; return the problem text, or None.

    footprint is the product footprint with the rings in time order. expected, when given, has
    one (imagette number, subswath or None) per point. A point outside the footprint, or whose
    imagette number or subswath disagrees with the model, gets null. A product the library
    cannot handle leaves null on all its points.
    """
    for point in points:
        point.update({key: None for key in FIELDS})
    if not points:
        return None
    if footprint is None:
        return _report(name, "no footprint for this product")
    try:
        from s1_geometry import estimate_wv_angles
        from s1_geometry.footprint import as_multipolygon, merge_antimeridian_splits
        from s1_geometry.wv import WV_HEADING_UNCERTAINTY_DEG

        rings = len(merge_antimeridian_splits(as_multipolygon(footprint)))
        if rings < MIN_IMAGETTES:
            # The end of a datatake leaves short products; this is normal.
            text = f"only {rings} imagette rings in the footprint, the model needs {MIN_IMAGETTES}"
            return _report(name, text, warning=True)
        angles = estimate_wv_angles(
            footprint,
            None,
            [p["ref_lon"] for p in points],
            [p["ref_lat"] for p in points],
            on_outside="nan",
        )
    except Exception as exc:  # the library raises ValueError; any failure leaves null
        return _report(name, f"{type(exc).__name__}: {exc}")
    disagree = 0
    for k, point in enumerate(points):
        if not np.isfinite(angles.incidence_deg[k]):
            continue
        # Free check: the model must place the point on the imagette it was matched to.
        if expected is not None:
            index, subswath = expected[k]
            if int(angles.ring_index[k]) != index - 1 or (
                subswath is not None and int(angles.subswath[k]) != subswath
            ):
                disagree += 1
                continue
        point["sar_incidence_angle"] = _finite(angles.incidence_deg[k])
        point["sar_elevation_angle"] = _finite(angles.elevation_deg[k])
        point["sar_ground_heading"] = _finite(angles.heading_deg[k])
        if point["sar_ground_heading"] is not None:
            point["sar_ground_heading_uncertainty"] = WV_HEADING_UNCERTAINTY_DEG
        point["sar_wv_subswath"] = int(angles.subswath[k])
    if disagree:
        text = f"{disagree} points whose imagette number or subswath disagree with the model"
        return _report(name, text + "; their angles are null")
    return None


def _signed_area(ring):
    """Shoelace area of a ring of (lon, lat); positive when counter-clockwise."""
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1])) / 2


def _corners_by_role(corners, direction):
    """Return the four corners as (last-near, first-near, first-far, last-far) for a pass.

    The range lines (near and far, along the flight) are the pair of opposite edges most
    parallel to the flight, whose bearing is s1-geometry's heading for this pass direction at
    the footprint's mean latitude. The bearing only has to be within 45 degrees, which keeps
    the roles right near the turning latitude, where the heading model is extrapolated. Near
    range is the range line on the left, the radar looking right of the flight; first and last
    follow the flight. Positions are taken on a plane tangent at the footprint's centre.
    """
    from s1_geometry import heading_from_lat

    lat0 = float(np.mean([y for _, y in corners]))
    lon0 = float(np.mean([x for x, _ in corners]))
    bearing = np.radians(heading_from_lat(lat0, direction))
    flight = np.array([np.sin(bearing), np.cos(bearing)])
    right = np.array([np.cos(bearing), -np.sin(bearing)])
    # Local east/north in km, each vertex scaled by the cosine of its own latitude.
    xy = np.array(
        [[(x - lon0) * np.cos(np.radians(y)) * 111.195, (y - lat0) * 111.195] for x, y in corners]
    )
    ring = list(range(4)) if _signed_area(corners) > 0 else [0, 3, 2, 1]

    def parallel(edges):
        return sum(
            abs(np.dot(xy[j] - xy[i], flight)) / (np.linalg.norm(xy[j] - xy[i]) or 1)
            for i, j in edges
        )

    pairs = [
        [(ring[k], ring[(k + 1) % 4]) for k in (0, 2)],
        [(ring[k], ring[(k + 1) % 4]) for k in (1, 3)],
    ]
    lines = max(pairs, key=parallel)
    near_line, far_line = sorted(lines, key=lambda edge: np.dot(xy[list(edge)].mean(axis=0), right))
    first_near, last_near = sorted(near_line, key=lambda i: np.dot(xy[i], flight))
    first_far, last_far = sorted(far_line, key=lambda i: np.dot(xy[i], flight))
    return [list(corners[i]) for i in (last_near, first_near, first_far, last_far)]


def _km(a, b):
    """Great-circle distance in km between two (lon, lat) points."""
    p = np.pi / 180
    h = (
        np.sin((b[1] - a[1]) * p / 2) ** 2
        + np.cos(a[1] * p) * np.cos(b[1] * p) * np.sin((b[0] - a[0]) * p / 2) ** 2
    )
    return float(12742.0 * np.arcsin(np.sqrt(h)))


def across_track_km(ring):
    """Distance in km between the middles of the near and far range lines of a library ring."""
    last_near, first_near, first_far, last_far = ring
    near = [(last_near[0] + first_near[0]) / 2, (last_near[1] + first_near[1]) / 2]
    far = [(first_far[0] + last_far[0]) / 2, (first_far[1] + last_far[1]) / 2]
    return _km(near, far)


def library_ring(corners, direction):
    """Return four corners in the order s1-geometry reads best: last-near, first-near,
    first-far, last-far (counter-clockwise, the first edge along the near range line against
    the flight).

    It is the library's "standard" order. The catalogue's own order is not reliable for this:
    on S1A/S1B it equals this order for a descending pass and is one corner off for an
    ascending one (which the library misreads on about a quarter of ascending EW footprints),
    and S1C footprints use yet another order. Built from the corners' positions, it does not
    depend on the catalogue's order, nor on a footprint having been cut at the dateline. With
    it the library read the given direction on every S1B IW and EW scene of January 2021 and
    on 72 of 80 SM scenes.
    """
    return _corners_by_role(corners, direction)


def add_scene_angles(name, corners, mode, points, orbit_direction=None):
    """Write the SAR angle fields of an IW, EW or SM scene's points in place; return the problem.

    corners are the scene's corners, longitudes continuous across 180; each point is placed in
    the same longitude frame. For SM the beam (S1 to S6) is read from the SAFE name.

    orbit_direction is the pass direction (ascending or descending): from the motion of the
    datatake's slices, else the catalogue's orbitDirection (sar_parquet.slice_motion). The
    heading depends only on latitude and direction: it is the library's heading for that
    direction, with its latitude-dependent uncertainty. Incidence and elevation come from the
    corners put in the library's order for that direction (library_ring), and are left null
    when the library still reads another direction from them (some SM scenes), or when the
    footprint is not four corners. Without a direction the footprint's order is read by the
    library as it is and the product is reported; a "turning" reading gives no angles. A point
    outside the footprint gets null angles without being reported (the library does not
    extrapolate). Any other failure is reported once per product.
    """
    for point in points:
        point.update({key: None for key in FIELDS})
    if not points:
        return None
    if not corners:
        return _report(name, "no footprint for this product")
    try:
        from s1_geometry import (
            estimate_incidence_elevation,
            heading_from_lat,
            orbit_direction_from_ring,
            sar_ground_heading_uncertainty,
        )

        four = len(corners) == 4
        direction, problems = orbit_direction, []
        if direction is None:
            ring = [list(c) for c in corners]
            seen_as = orbit_direction_from_ring(ring) if four else None
            direction = seen_as if seen_as in ("ascending", "descending") else None
            problems.append(
                (
                    f"no pass direction; the footprint reads as {seen_as}"
                    if four
                    else f"no pass direction, and the footprint has {len(corners)} corners"
                )
                + ("" if direction else ", so no angles")
            )
            trusted = direction is not None
        else:
            ring = library_ring(corners, direction) if four else [list(c) for c in corners]
            seen_as = orbit_direction_from_ring(ring) if four else None
            trusted = seen_as == direction
    except Exception as exc:
        return _report(name, f"{type(exc).__name__}: {exc}")
    footprint = Polygon(ring)
    width = across_track_km(ring) if four and orbit_direction is not None else None
    beam = {"beam": name[4:6]} if mode == "SM" else {}
    middle = float(np.mean([lon for lon, _ in ring]))
    failures, untrusted, wrong_edges = {}, 0, 0
    for point in points:
        lon = middle + ((point["ref_lon"] - middle + 180) % 360 - 180)
        if not footprint.covers(Point(lon, point["ref_lat"])):
            continue
        if direction is not None:
            point["sar_ground_heading"] = _finite(heading_from_lat(point["ref_lat"], direction))
            point["sar_ground_heading_uncertainty"] = _finite(
                sar_ground_heading_uncertainty(point["ref_lat"])
            )
        if not trusted:
            untrusted += 1
            continue
        try:
            angles = estimate_incidence_elevation(
                ring, ref_lon=lon, ref_lat=point["ref_lat"], mode=mode, **beam
            )
        except Exception as exc:  # the library raises ValueError
            text = f"{type(exc).__name__}: {exc}"
            failures[text] = failures.get(text, 0) + 1
            continue
        # A nearly square footprint can lead the library to measure across the along-track
        # edges; its swath width then differs from the scene's own across-track width.
        if width and abs(angles.swath_width_km / width - 1) > SWATH_WIDTH_TOLERANCE:
            wrong_edges += 1
            measured = angles.swath_width_km
            continue
        point["sar_incidence_angle"] = _finite(angles.incidence_deg)
        point["sar_elevation_angle"] = _finite(angles.elevation_deg)
    problems += [f"{n} of {len(points)} points: {t}" for t, n in failures.items()]
    if wrong_edges:
        problems.append(
            f"{wrong_edges} of {len(points)} points: the library measured a {measured:.0f} km"
            f" swath across a {width:.0f} km wide scene; incidence left null"
        )
    if untrusted and direction is not None:
        reason = (
            f"the footprint has {len(corners)} corners"
            if not four
            else f"the footprint reads as {seen_as} while the pass is {direction}"
        )
        problems.append(f"{untrusted} of {len(points)} points: {reason}; incidence left null")
    return _report(name, "; ".join(problems)) if problems else None


def library_version():
    """Return the installed s1-geometry version, or None when it cannot be found."""
    try:
        from importlib.metadata import version

        return version("s1-geometry")
    except Exception:
        return None
