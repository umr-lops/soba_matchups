"""Publish a complete run directory only after writing and reading its artifacts."""

import csv
import hashlib
import json
from pathlib import Path
import tempfile

MATCH_FIELDS = [
    "record_id",
    "safe_id",
    "safe_name",
    "mission",
    "polarization",
    "producttype",
    "sea_percent",
    "safe_start_utc",
    "safe_end_utc",
    "reference_file",
    "reference_path",
    "ref_row_0based",
    "ref_cell_0based",
    "ref_time_utc",
    "ref_lon",
    "ref_lat",
    "wind_speed",
    "wind_direction_to",
    "quality_flag",
    "ice_probability",
]
PAIR_FIELDS = ["safe_id", "reference_file", "matched_cells"]


def file_identity(path):
    path = Path(path).resolve()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "sha256": digest.hexdigest(), "bytes": path.stat().st_size}


def publish(output_dir, rows, diagnostics, products, manifest):
    output_dir = Path(output_dir)
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError(f"Output already exists; choose a fresh directory: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".soba-", dir=output_dir.parent) as temp:
        stage = Path(temp) / "result"
        stage.mkdir()
        for name, values, fields in [
            ("matchups.csv", rows, MATCH_FIELDS),
            ("pair_diagnostics.csv", diagnostics, PAIR_FIELDS),
        ]:
            with (stage / name).open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(values)
            with (stage / name).open(newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames != fields or sum(1 for _ in reader) != len(values):
                    raise ValueError(f"Failed artifact readback: {name}")
        identities = {row["record_id"] for row in rows}
        if len(identities) != len(rows):
            raise ValueError("Conflicting duplicate matchup identities")
        (stage / "sar_metadata.json").write_text(json.dumps({"value": products}, indent=2) + "\n")
        manifest["artifacts"] = [
            {**file_identity(stage / name), "path": name}
            for name in ["matchups.csv", "pair_diagnostics.csv", "sar_metadata.json"]
        ]
        target = stage / "manifest.json"
        target.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
        if json.loads(target.read_text())["matchup_safe_cell_rows"] != len(rows):
            raise ValueError("Manifest readback failed")
        # ponytail: one local-filesystem directory rename; object stores need a separate publisher.
        stage.rename(output_dir)
    return manifest
