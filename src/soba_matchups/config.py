"""Strict standalone TOML recipes; paths are relative to the recipe file."""

from dataclasses import asdict, dataclass
from datetime import date
import math
from pathlib import Path
import tomllib


# Supported reference products, keyed by (instrument, platform, resolution). Each value is
# (expected "source" attribute, expected pixel size, product label used in the Item file
# names). The KNMI L2B entries are checked against their files' attributes; the SWOT file has
# none, so its source and pixel size are the recipe's own. Any processing version is
# accepted; each file is checked against the expected variables, units and conventions.
REFERENCE_PRODUCTS = {
    ("ascat", "metop-b", "25km"): ("MetOp-B ASCAT", "25.0 km", "KNMI_ASCAT_METOPB_25KM"),
    ("ascat", "metop-b", "12.5km"): ("MetOp-B ASCAT", "12.5 km", "KNMI_ASCAT_METOPB_12P5KM"),
    ("ascat", "metop-b", "5.7km"): ("MetOp-B ASCAT", "5.7 km", "KNMI_ASCAT_METOPB_5P7KM"),
    ("ascat", "metop-c", "12.5km"): ("MetOp-C ASCAT", "12.5 km", "KNMI_ASCAT_METOPC_12P5KM"),
    ("hscat", "hy-2b", "25km"): ("HY-2B HSCAT", "25.0 km", "KNMI_HSCAT_HY2B_25KM"),
    ("hscat", "hy-2c", "25km"): ("HY-2C HSCAT", "25.0 km", "KNMI_HSCAT_HY2C_25KM"),
    ("hscat", "hy-2d", "25km"): ("HY-2D HSCAT", "25.0 km", "KNMI_HSCAT_HY2D_25KM"),
    ("karin", "swot", "2km"): ("SWOT KaRIn", "2 km", "PODAAC_SWOT_KARIN_L2_WINDWAVE_D0"),
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
    instrument: str
    platform: str
    resolution: str

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
    required = set(Recipe.__dataclass_fields__) - {"path"}
    unknown = set(data) - required
    if unknown or required - set(data):
        raise ValueError(
            f"Unknown recipe keys: {sorted(unknown)}; missing keys: {sorted(required - set(data))}"
        )
    product = (data["instrument"], data["platform"], data["resolution"])
    if any(not isinstance(v, str) for v in product) or product not in REFERENCE_PRODUCTS:
        raise ValueError("Unsupported reference instrument / platform / resolution combination")
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
    # Make every path absolute, relative to the recipe file.
    for key in ["reference_root", "sar_archive_root", "output_dir"]:
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"{key} must be a nonempty path")
        data[key] = (path.parent / data[key]).resolve()
    # Inputs must exist, and results must never be written inside them.
    for key in ["reference_root", "sar_archive_root"]:
        if not data[key].is_dir():
            raise ValueError(f"{key} directory does not exist: {data[key]}")
        if data["output_dir"].is_relative_to(data[key]):
            raise ValueError(f"output_dir must stay outside {key}")
    return Recipe(path=path, **{**data, "sar_missions": tuple(missions)})
