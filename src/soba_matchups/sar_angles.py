"""SAR incidence, elevation and ground heading of matched points, from the s1-geometry library.

The library works from the product footprint alone (one ring per imagette, in time order) and
needs no orbit file. A product it cannot handle never stops a run: its points get null angles
and the product is named in the manifest.
"""

import logging
import re

import numpy as np

logger = logging.getLogger(__name__)

# Point fields written to the Items, with their units.
FIELDS = {
    "sar_incidence_angle": "degrees, from the local vertical",
    "sar_elevation_angle": "degrees, off-nadir angle at the satellite (ESA OCN convention)",
    "sar_ground_heading": "degrees clockwise from north",
    "sar_ground_heading_uncertainty": "degrees, p95 of the s1-geometry WV heading error",
    "sar_wv_subswath": "WV subswath of the imagette, 1 or 2",
}
# The model fits the track on the two subswath chains, so it needs two imagettes of each.
MIN_IMAGETTES = 4
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


def library_version():
    """Return the installed s1-geometry version, or None when it cannot be found."""
    try:
        from importlib.metadata import version

        return version("s1-geometry")
    except Exception:
        return None
