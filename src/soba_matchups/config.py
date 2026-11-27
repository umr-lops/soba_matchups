"""Strict standalone TOML recipes; paths are relative to the recipe file."""

from dataclasses import asdict, dataclass
from datetime import date, timedelta
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
    ("karin", "swot", "2km"): ("SWOT KaRIn", "2 km", "PODAAC_SWOT_KARIN_L2_WINDWAVE"),
    ("alti", "cci", "l2p"): ("CCI altimeters", "1 Hz", "ESACCI_SEASTATE_L2P"),
    ("alti", "cmems", "l3"): ("CMEMS altimeters", "1 Hz", "CMEMS_WAV_L3_ALTI_VAVH"),
    # NDBC buoys (NCEI), TAO moorings, and the CMEMS in situ platforms in neither; each file
    # keeps its own source ("NDBC buoy", "TAO mooring", "CMEMS in situ").
    ("buoy", "ndbc-tao-cmems", "point"): ("Buoys", "point", "INSITU_NDBC_TAO_CMEMS_WIND"),
}
# SAR product types a run can read.
SAR_PRODUCT_TYPES = ("OCN", "SLC", "GRD")
# Recipe keys that only one instrument takes, and that it requires. The *_root ones are folders.
INSTRUMENT_KEYS = {"cmems_insitu_root": "buoy", "swot_release": "karin"}
# SWOT releases a run can read, one per run (recipe key swot_release). Release D2 reads the
# sub-folders PGD2 (ground reprocessing) and PID2 (forward processing) of reference_root, and the
# release ends the Item file names (PODAAC_SWOT_KARIN_L2_WINDWAVE_D2).
SWOT_RELEASES = ("D0", "D2")
# Sentinel-1 acquisition modes a run can match, one per run. WV is matched per imagette; IW, EW
# and SM (StripMap, beams S1 to S6) per scene (one OCN product), from the daily parquet files only.
SAR_MODES = ("WV", "IW", "EW", "SM")
# Placeholder of a per-day recipe's output_dir (see batch.py): replaced by the day, YYYY-MM-DD.
DAY_FIELD = "{day}"


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
    swot_release: str | None = None
    product_type: str = "OCN"
    sar_mode: str = "WV"
    sar_archive_layout: str = "mpc"

    def resolved(self):
        """Return the recipe as plain values that can be written to JSON."""
        return {k: str(v) if isinstance(v, (Path, date)) else v for k, v in asdict(self).items()}


def load_recipe(path, day=None):
    """Load and strictly validate a TOML recipe; relative paths resolve against its folder.

    With ``day`` (a date), the recipe is a per-day template: ``output_dir`` must contain
    ``{day}``, replaced by the day (YYYY-MM-DD), and the run covers that day alone. Without
    ``day``, an ``output_dir`` holding ``{day}`` is refused.
    """
    path = Path(path).resolve()
    if path.suffix != ".toml":
        raise ValueError("Recipe must be a .toml file")
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    output = data.get("output_dir")
    per_day = isinstance(output, str) and DAY_FIELD in output
    if per_day and day is None:
        raise ValueError(f"output_dir holds {DAY_FIELD}: run the recipe day by day (run-day)")
    if day is not None:
        if not per_day:
            raise ValueError(f"run-day needs a per-day recipe: output_dir must contain {DAY_FIELD}")
        data["start_date"] = day.isoformat()
        data["stop_date"] = (day + timedelta(days=1)).isoformat()
        data["output_dir"] = output.replace(DAY_FIELD, day.isoformat())
    # Reject unknown keys and missing required keys.
    optional = {"sar_archive_root", "sar_parquet_root"}
    # The distance-to-coast grid is a file, and a run goes on without it. The CMEMS in situ root
    # belongs to the buoy reference only (INSTRUMENT_KEYS), checked below.
    extra = {
        "distance_to_coast_file", "product_type", "sar_mode", "sar_archive_layout",
        *INSTRUMENT_KEYS,
    }
    required = set(Recipe.__dataclass_fields__) - {"path"} - optional - extra
    unknown = set(data) - required - optional - extra
    if unknown or required - set(data):
        raise ValueError(
            f"Unknown recipe keys: {sorted(unknown)}; missing keys: {sorted(required - set(data))}"
        )
    # The SAR metadata comes from the archive, the daily parquet files, or both.
    if not optional & set(data):
        raise ValueError("Recipe needs sar_archive_root, sar_parquet_root, or both")
    # One run reads one SAR product type: L2 OCN (the default), L1 SLC or L1 GRD. SLC and GRD are
    # read from the daily parquet files only: the archive source reads the OCN NetCDF headers.
    data.setdefault("product_type", "OCN")
    if data["product_type"] not in SAR_PRODUCT_TYPES:
        raise ValueError(f"product_type must be one of {list(SAR_PRODUCT_TYPES)}")
    if data["product_type"] in ("SLC", "GRD") and "sar_parquet_root" not in data:
        raise ValueError(f"product_type {data['product_type']} needs sar_parquet_root")
    product = (data["instrument"], data["platform"], data["resolution"])
    if any(not isinstance(v, str) for v in product) or product not in REFERENCE_PRODUCTS:
        raise ValueError("Unsupported reference instrument / platform / resolution combination")
    for key, instrument in INSTRUMENT_KEYS.items():
        if (key in data) != (data["instrument"] == instrument):
            raise ValueError(f"{key} is required for instrument {instrument!r} and only for it")
    if "swot_release" in data and data["swot_release"] not in SWOT_RELEASES:
        raise ValueError(f"swot_release must be one of {list(SWOT_RELEASES)}")
    mode = data.setdefault("sar_mode", "WV")
    if mode not in SAR_MODES:
        raise ValueError(f"sar_mode must be one of {list(SAR_MODES)}")
    # IW, EW and SM scenes are described by the daily parquet files alone; the archive, if
    # given, is only asked whether each product's measurement file exists.
    if mode != "WV" and "sar_parquet_root" not in data:
        raise ValueError(f"sar_mode {mode!r} needs sar_parquet_root (the daily parquet files)")
    # SLC is read per WV imagette or per IW, EW or SM scene; GRD per IW, EW or SM scene (WV has
    # no GRD product).
    if mode == "WV" and data["product_type"] == "GRD":
        raise ValueError("product_type 'GRD' is only read for sar_mode IW, EW or SM")
    # Where the SAFEs of sar_archive_root are filed (sar_parquet.ARCHIVE_LAYOUTS). The provider
    # tree holds IW and EW L2 OCN only, and is read through the daily parquet files.
    layout = data.setdefault("sar_archive_layout", "mpc")
    if layout not in ("mpc", "provider"):
        raise ValueError("sar_archive_layout must be 'mpc' or 'provider'")
    if layout == "provider" and (
        "sar_parquet_root" not in data or data["product_type"] != "OCN" or mode == "WV"
    ):
        raise ValueError(
            "sar_archive_layout 'provider' holds IW and EW OCN products: it needs "
            "sar_mode IW or EW, product_type OCN and sar_parquet_root"
        )
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
    folder_keys = {k for k in INSTRUMENT_KEYS if k.endswith("_root")}
    for key in sorted((optional | folder_keys) & set(data)):
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
