"""Strict standalone TOML recipes; paths are relative to the recipe file."""

from dataclasses import asdict, dataclass
from datetime import date
import math
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Recipe:
    path: Path
    scat: str
    met: str
    resolution: str
    reference_version: str
    reference_root: Path
    start_date: date
    stop_date: date
    sar_missions: tuple[str, ...]
    time_tolerance_minutes: float
    sar_cache_dir: Path
    output_dir: Path
    offline: bool = False

    def resolved(self):
        return {k: str(v) if isinstance(v, (Path, date)) else v for k, v in asdict(self).items()}


def load_recipe(path):
    path = Path(path).resolve()
    if path.suffix != ".toml":
        raise ValueError("Recipe must be a .toml file")
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    required = set(Recipe.__dataclass_fields__) - {"path", "offline"}
    unknown = set(data) - required - {"offline"}
    if unknown or required - set(data):
        raise ValueError(
            f"Unknown recipe keys: {sorted(unknown)}; missing keys: {sorted(required - set(data))}"
        )
    if (data["scat"], data["met"], data["resolution"], data["reference_version"]) != (
        "hscat",
        "hy-2b",
        "25km",
        "2202",
    ):
        raise ValueError("Only hscat / hy-2b / 25km / version 2202 is supported")
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
    if not isinstance(data.get("offline", False), bool):
        raise ValueError("offline must be a boolean")
    for key in ["reference_root", "sar_cache_dir", "output_dir"]:
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"{key} must be a nonempty path")
        data[key] = (path.parent / data[key]).resolve()
    if not data["reference_root"].is_dir():
        raise ValueError(f"Reference directory does not exist: {data['reference_root']}")
    for key in ["output_dir", "sar_cache_dir"]:
        if data[key].is_relative_to(data["reference_root"]):
            raise ValueError(f"{key} must stay outside reference_root")
    if data["sar_cache_dir"] == data["output_dir"]:
        raise ValueError("Cache and output directories must differ")
    return Recipe(path=path, **{**data, "sar_missions": tuple(missions)})
