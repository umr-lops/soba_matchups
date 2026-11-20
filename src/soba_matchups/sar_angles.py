"""SAR incidence, elevation and ground heading of matched points, from the s1-geometry library.

The library works from the product footprint alone (one ring per imagette, in time order) and
needs no orbit file. A product it cannot handle never stops a run: its points get null angles
and the product is named in the manifest.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)

# Point fields written to the Items, with their units.
FIELDS = {
    "sar_incidence_angle": "degrees, from the local vertical",
    "sar_elevation_angle": "degrees, off-nadir angle at the satellite (ESA OCN convention)",
    "sar_ground_heading": "degrees clockwise from north",
}


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


def add_angles(footprint, points):
    """Write the three SAR angles on each point in place; return the error text, or None.

    footprint is the product footprint with the rings in time order. A point outside it, or a
    value that is not finite, gets null.
    """
    for point in points:
        point.update({key: None for key in FIELDS})
    if not points:
        return None
    if footprint is None:
        return "no footprint for this product"
    try:
        from s1_geometry import estimate_wv_angles

        angles = estimate_wv_angles(
            footprint,
            None,
            [p["ref_lon"] for p in points],
            [p["ref_lat"] for p in points],
            on_outside="nan",
        )
    except Exception as exc:  # the library raises ValueError; any failure leaves null
        return f"{type(exc).__name__}: {exc}"
    values = {
        "sar_incidence_angle": angles.incidence_deg,
        "sar_elevation_angle": angles.elevation_deg,
        "sar_ground_heading": angles.heading_deg,
    }
    for key, array in values.items():
        for point, value in zip(points, np.asarray(array, dtype="float64"), strict=True):
            point[key] = float(value) if np.isfinite(value) else None
    return None


def library_version():
    """Return the installed s1-geometry version, or None when it cannot be found."""
    try:
        from importlib.metadata import version

        return version("s1-geometry")
    except Exception:
        return None
