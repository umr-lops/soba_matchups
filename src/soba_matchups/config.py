"""Strict standalone TOML recipes; paths are relative to the recipe file."""

from dataclasses import asdict, dataclass
from datetime import date
import math
from pathlib import Path
import tomllib


# Surveyed KNMI L2B identities. Any processing version is accepted; each file is
# checked against the expected variables, units and conventions instead.
REFERENCE_PRODUCTS = {
    ("ascat", "metop-b", "25km"): ("MetOp-B ASCAT", "25.0 km"),
    ("ascat", "metop-b", "12.5km"): ("MetOp-B ASCAT", "12.5 km"),
    ("ascat", "metop-b", "5.7km"): ("MetOp-B ASCAT", "5.7 km"),
    ("ascat", "metop-c", "12.5km"): ("MetOp-C ASCAT", "12.5 km"),
    ("hscat", "hy-2b", "25km"): ("HY-2B HSCAT", "25.0 km"),
    ("hscat", "hy-2c", "25km"): ("HY-2C HSCAT", "25.0 km"),
    ("hscat", "hy-2d", "25km"): ("HY-2D HSCAT", "25.0 km"),
}


@dataclass(frozen=True)
class Recipe:
    """A validated recipe; every path is absolute and every value has been checked."""

    path: Path
    reference_root: Path
    start_date: date
    stop_date: date
    sar_missions: tuple[str, ...]
    time_tolerance_minutes: float
    sar_archive_root: Path
    output_dir: Path
    land_mask: Path
    scat: str
    met: str
    resolution: str
    land_mask_source: str = "local file; origin not asserted"

    def resolved(self):
        """Return the recipe as plain values that can be written to JSON."""
        return {k: str(v) if isinstance(v, (Path, date)) else v for k, v in asdict(self).items()}


def load_recipe(path):
    """Load and strictly validate a TOML recipe; relative paths resolve against its folder."""
    path = Path(path).resolve()
    if path.suffix != ".toml":
        raise ValueError("Recipe must be a .toml file")
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    # Reject unknown keys and missing required keys.
    optional = {"land_mask_source"}
    required = set(Recipe.__dataclass_fields__) - {"path"} - optional
    unknown = set(data) - required - optional
    if unknown or required - set(data):
        raise ValueError(
            f"Unknown recipe keys: {sorted(unknown)}; missing keys: {sorted(required - set(data))}"
        )
    product = (data["scat"], data["met"], data["resolution"])
    if any(not isinstance(v, str) for v in product) or product not in REFERENCE_PRODUCTS:
        raise ValueError("Unsupported KNMI L2B sensor / platform / resolution combination")
    # Dates may be TOML dates or exact YYYY-MM-DD strings.
    for key in ["start_date", "stop_date"]:
        value = data[key]
        if isinstance(value, str):
            try:
                parsed = date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"{key} must be YYYY-MM-DD") from exc
            if parsed.isoformat() != value:
                raise ValueError(f"{key} must be YYYY-MM-DD")
            data[key] = parsed
        elif type(value) is not date:
            raise ValueError(f"{key} must be a date")
    if data["start_date"] >= data["stop_date"]:
        raise ValueError("start_date must precede exclusive stop_date")
    tolerance = data["time_tolerance_minutes"]
    if (
        isinstance(tolerance, bool)
        or not isinstance(tolerance, (int, float))
        or not math.isfinite(tolerance)
        or tolerance <= 0
        or tolerance > 1440
    ):
        raise ValueError("Time tolerance must be finite and in (0, 1440] minutes")
    missions = data["sar_missions"]
    if (
        not isinstance(missions, list)
        or not missions
        or any(not isinstance(m, str) or m not in {"S1A", "S1B", "S1C", "S1D"} for m in missions)
        or len(set(missions)) != len(missions)
    ):
        raise ValueError("sar_missions must contain distinct supported Sentinel-1 missions")
    if (
        not isinstance(data.get("land_mask_source", "local file; origin not asserted"), str)
        or not data.get("land_mask_source", "local file; origin not asserted").strip()
    ):
        raise ValueError("land_mask_source must be a nonempty provenance label")
    # Make every path absolute, relative to the recipe file.
    for key in ["reference_root", "sar_archive_root", "output_dir", "land_mask"]:
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"{key} must be a nonempty path")
        data[key] = (path.parent / data[key]).resolve()
    if not data["land_mask"].is_file():
        raise ValueError(f"Land mask does not exist: {data['land_mask']}")
    # Inputs must exist, and results must never be written inside them.
    for key in ["reference_root", "sar_archive_root"]:
        if not data[key].is_dir():
            raise ValueError(f"{key} directory does not exist: {data[key]}")
        if data["output_dir"].is_relative_to(data[key]):
            raise ValueError(f"output_dir must stay outside {key}")
    return Recipe(path=path, **{**data, "sar_missions": tuple(missions)})
