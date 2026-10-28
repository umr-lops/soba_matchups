# soba_matchups

[![CI](https://github.com/umr-lops/soba_matchups/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/umr-lops/soba_matchups/actions/workflows/ci.yml)
[![Build](https://github.com/umr-lops/soba_matchups/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/umr-lops/soba_matchups/actions/workflows/build.yml)
[![Docs](https://app.readthedocs.org/projects/soba-matchups/badge/?version=latest)](https://soba-matchups.readthedocs.io/en/latest/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](.pre-commit-config.yaml)
[![Python](https://img.shields.io/badge/python-%3E%3D3.11-blue?logo=python)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Match Sentinel-1 WV L2 OCN imagettes with HY-2B HSCAT 25 km wind observations
The output is one STAC JSON file per matched imagette.

It reads local files: the SAR archive, the HSCAT granules and a land mask.

## Run it

```sh
conda env create -f environment.yml
conda activate soba_matchups
python -m pip install -e . --no-build-isolation

cp examples/hscat.toml hscat.toml   # then edit the paths and dates
soba_matchups validate --recipe hscat.toml
soba_matchups run --recipe hscat.toml
```

`output_dir` must not exist yet. A finished run holds `items/` (the STAC files),
`diagnostics.json`, `sar_metadata.json` and `manifest.json`.

## Apptainer

Build once with internet access. Running needs only local files.

```sh
apptainer build soba_matchups.sif apptainer.def
apptainer run \
  --bind /path/to/sar/archive:/path/to/sar/archive:ro \
  --bind /path/to/knmi/l2b:/reference:ro \
  --bind /path/to/work:/work \
  soba_matchups.sif run --recipe /work/hscat.toml
```

## Documentation

The output format is described in the [documentation](https://soba-matchups.readthedocs.io/en/latest/).
