# soba_matchups

[![CI](https://github.com/umr-lops/soba_matchups/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/umr-lops/soba_matchups/actions/workflows/ci.yml)
[![Build](https://github.com/umr-lops/soba_matchups/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/umr-lops/soba_matchups/actions/workflows/build.yml)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](.pre-commit-config.yaml)
[![Python](https://img.shields.io/badge/python-%3E%3D3.11-blue?logo=python)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Match Sentinel-1 Wave Mode SAFE metadata with reference observations.
Currently supports HY-2B HSCAT 25 km, processing version 2202. SWOT and ALTI are planned.

## Install

```sh
conda env create -f environment.yml
conda activate soba_matchups
python -m pip install -e . --no-build-isolation
```

## Run

Copy [examples/hscat.toml](examples/hscat.toml) to `hscat.toml` and edit the paths
and dates. Set `reference_root` to the KNMI `satellite/l2b` archive root;
`scat`, `met`, `resolution` and the dates select the day folders. `stop_date` is exclusive.

```sh
soba_matchups validate --recipe hscat.toml
soba_matchups run --recipe hscat.toml
```

Matching uses full SAFE footprints and SAFE start/end times with the configured
tolerance. Dates select reference-granule start days. Quality/ice flags are retained,
not filtered. `sar_cache_dir` stores reusable API metadata; `output_dir` must be new.

Outputs: `matchups.csv`, `pair_diagnostics.csv`, `sar_metadata.json` and `manifest.json`.
These are SAFE/reference-cell matchups, not enriched co-aligned catalogues.

## Checks

```sh
pre-commit install
pre-commit run --all-files
python -m pytest -q
```
