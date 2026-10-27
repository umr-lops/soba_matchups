# soba_matchups

[![CI](https://github.com/umr-lops/soba_matchups/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/umr-lops/soba_matchups/actions/workflows/ci.yml)
[![Build](https://github.com/umr-lops/soba_matchups/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/umr-lops/soba_matchups/actions/workflows/build.yml)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](.pre-commit-config.yaml)
[![Python](https://img.shields.io/badge/python-%3E%3D3.11-blue?logo=python)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Match local Sentinel-1 WV L2 OCN imagettes with HY-2B HSCAT 25 km observations
(processing version 2202). Output: one STAC JSON per matched imagette, with
reference points grouped by source granule. No L1 files, API access or credentials.

## Install and run

```sh
conda env create -f environment.yml
conda activate soba_matchups
python -m pip install -e . --no-build-isolation
cp examples/hscat.toml hscat.toml
# Edit archive paths, dates, mask and output directory.
soba_matchups validate --recipe hscat.toml
soba_matchups run --recipe hscat.toml
```

## Apptainer

Build from this checkout with internet access. Runtime needs only local files:

```sh
apptainer build soba_matchups.sif apptainer.def
META=/path/to/local/metadata
apptainer run --bind "$META:$META:ro" \
  --bind /path/to/knmi/satellite/l2b:/reference:ro \
  --bind /path/to/work:/work soba_matchups.sif run --recipe /work/hscat.toml
```
