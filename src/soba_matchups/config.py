"""Strict standalone TOML recipes; paths are relative to the recipe file."""

from dataclasses import asdict, dataclass
from datetime import date
import math
from pathlib import Path
import tomllib


# Supported reference products, keyed by (instrument, platform, resolution). Each value is
# (expected "source" attribute, expected pixel size, product label used in the Item file
# names). The KNMI L2B entries are checked against their files' attributes; the SWOT and CCI
# altimeter files have none that fit, so their source and sampling are the recipe's own (CCI:
# every mission found under the root, each file keeping its own mission as source). Any
# processing version is accepted; each file is checked against the expected variables, units and
# conventions.
REFERENCE_PRODUCTS = {
    ("ascat", "metop-b", "25km"): ("MetOp-B ASCAT", "25.0 km", "KNMI_ASCAT_METOPB_25KM"),
    ("ascat", "metop-b", "12.5km"): ("MetOp-B ASCAT", "12.5 km", "KNMI_ASCAT_METOPB_12P5KM"),
    ("ascat", "metop-b", "5.7km"): ("MetOp-B ASCAT", "5.7 km", "KNMI_ASCAT_METOPB_5P7KM"),
    ("ascat", "metop-c", "12.5km"): ("MetOp-C ASCAT", "12.5 km", "KNMI_ASCAT_METOPC_12P5KM"),
    ("hscat", "hy-2b", "25km"): ("HY-2B HSCAT", "25.0 km", "KNMI_HSCAT_HY2B_25KM"),
    ("hscat", "hy-2c", "25km"): ("HY-2C HSCAT", "25.0 km", "KNMI_HSCAT_HY2C_25KM"),
    ("hscat", "hy-2d", "25km"): ("HY-2D HSCAT", "25.0 km", "KNMI_HSCAT_HY2D_25KM"),
    ("karin", "swot", "2km"): ("SWOT KaRIn", "2 km", "PODAAC_SWOT_KARIN_L2_WINDWAVE_D0"),
    ("alti", "cci", "l2p"): ("CCI altimeters", "1 Hz", "ESACCI_SEASTATE_L2P"),
    ("alti", "cmems", "l3"): ("CMEMS altimeters", "1 Hz", "CMEMS_WAV_L3_ALTI_VAVH"),
    # NDBC buoys (NCEI), TAO moorings, and the CMEMS in situ platforms in neither; each file
    # keeps its own source ("NDBC buoy", "TAO mooring", "CMEMS in situ").
    ("buoy", "ndbc-tao-cmems", "point"): ("Buoys", "point", "INSITU_NDBC_TAO_CMEMS_WIND"),
}
# SAR product types a run can read.
SAR_PRODUCT_TYPES = ("OCN", "SLC")
# Recipe keys that only one instrument takes, and that it requires.
INSTRUMENT_KEYS = {"cmems_insitu_root": "buoy"}
# Sentinel-1 acquisition modes a run can match, one per run. WV is matched per imagette; IW, EW
# and SM (StripMap, beams S1 to S6) per scene (one OCN product), from the daily parquet files only.
SAR_MODES = ("WV", "IW", "EW", "SM")


@dataclass(frozen=True)
class Recipe:
    """A validated recipe; every path is absolute and every value has been checked."""

    path: Path
    reference_root: Path
    start_date: date
    stop_date: date
    sar_missions: tuple[str, ...]
    time_tolerance_minutes: float
    output_dir: Path
    instrument: str
    platform: str
    resolution: str
    sar_archive_root: Path | None = None
    sar_parquet_root: Path | None = None
    distance_to_coast_file: Path | None = None
    cmems_insitu_root: Path | None = None
    product_type: str = "OCN"
    sar_mode: str = "WV"

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
    optional = {"sar_archive_root", "sar_parquet_root"}
    # The distance-to-coast grid is a file, and a run goes on without it. The CMEMS in situ root
    # belongs to the buoy reference only (INSTRUMENT_KEYS), checked below.
    extra = {"distance_to_coast_file", "product_type", "sar_mode", *INSTRUMENT_KEYS}
    required = set(Recipe.__dataclass_fields__) - {"path"} - optional - extra
    unknown = set(data) - required - optional - extra
    if unknown or required - set(data):
        raise ValueError(
            f"Unknown recipe keys: {sorted(unknown)}; missing keys: {sorted(required - set(data))}"
        )
    # The SAR metadata comes from the archive, the daily parquet files, or both.
    if not optional & set(data):
        raise ValueError("Recipe needs sar_archive_root, sar_parquet_root, or both")
    # One run reads one SAR product type: WV L2 OCN (the default) or WV L1 SLC. SLC is read from
    # the daily parquet files only: the archive source reads the OCN NetCDF headers.
    data.setdefault("product_type", "OCN")
    if data["product_type"] not in SAR_PRODUCT_TYPES:
        raise ValueError(f"product_type must be one of {list(SAR_PRODUCT_TYPES)}")
    if data["product_type"] == "SLC" and "sar_parquet_root" not in data:
        raise ValueError("product_type SLC needs sar_parquet_root")
    product = (data["instrument"], data["platform"], data["resolution"])
    if any(not isinstance(v, str) for v in product) or product not in REFERENCE_PRODUCTS:
        raise ValueError("Unsupported reference instrument / platform / resolution combination")
    for key, instrument in INSTRUMENT_KEYS.items():
        if (key in data) != (data["instrument"] == instrument):
            raise ValueError(f"{key} is required for instrument {instrument!r} and only for it")
    mode = data.setdefault("sar_mode", "WV")
    if mode not in SAR_MODES:
        raise ValueError(f"sar_mode must be one of {list(SAR_MODES)}")
    # IW, EW and SM scenes are described by the daily parquet files alone; the archive, if
    # given, is only asked whether each product's measurement file exists.
    if mode != "WV" and "sar_parquet_root" not in data:
        raise ValueError(f"sar_mode {mode!r} needs sar_parquet_root (the daily parquet files)")
    # SLC is read per WV imagette; IW, EW and SM scenes are OCN products only.
    if mode != "WV" and data["product_type"] != "OCN":
        raise ValueError(f"product_type {data['product_type']!r} is only read for sar_mode WV")
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
    paths = ["reference_root", "output_dir"]
    inputs = ["reference_root"]
    for key in sorted((optional | set(INSTRUMENT_KEYS)) & set(data)):
        paths.append(key)
        inputs.append(key)
    for key in paths:
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"{key} must be a nonempty path")
        data[key] = (path.parent / data[key]).resolve()
    if "distance_to_coast_file" in data:
        value = data["distance_to_coast_file"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("distance_to_coast_file must be a nonempty path")
        # Not required to exist here: the run flags a missing file and carries on.
        data["distance_to_coast_file"] = (path.parent / value).resolve()
    # Inputs must exist, and results must never be written inside them.
    for key in inputs:
        if not data[key].is_dir():
            raise ValueError(f"{key} directory does not exist: {data[key]}")
        if data["output_dir"].is_relative_to(data[key]):
            raise ValueError(f"output_dir must stay outside {key}")
    return Recipe(path=path, **{**data, "sar_missions": tuple(missions)})
