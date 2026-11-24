# soba_matchups

[![CI](https://github.com/umr-lops/soba_matchups/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/umr-lops/soba_matchups/actions/workflows/ci.yml)
[![Build](https://github.com/umr-lops/soba_matchups/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/umr-lops/soba_matchups/actions/workflows/build.yml)
[![Docs](https://img.shields.io/readthedocs/soba-matchups?logo=readthedocs)](https://soba-matchups.readthedocs.io/en/latest/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](.pre-commit-config.yaml)
[![Python](https://img.shields.io/badge/python-%3E%3D3.11-blue?logo=python)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Match Sentinel-1 WV L2 OCN imagettes with scatterometer winds (KNMI ASCAT, HSCAT), SWOT KaRIn waves and winds,
altimeter wave heights (CCI, CMEMS) or buoy winds (NDBC, TAO and CMEMS in situ).
The output is one STAC JSON file per matched imagette.


## Run it

```sh
pixi install                        # the environment, from pixi.lock (https://pixi.sh)

cp examples/hscat.toml hscat.toml   # then edit the paths and dates
pixi run soba_matchups validate --recipe hscat.toml
pixi run soba_matchups run --recipe hscat.toml
```

`pixi shell` opens a shell in the environment, where `soba_matchups` runs directly.
`pixi run test` runs the tests and `pixi run lint` the pre-commit hooks.

`output_dir` must not exist yet. A finished run holds `items/` (the STAC files),
`diagnostics.json`, `sar_metadata.json` and `manifest.json`.

## Apptainer

Build once with internet access. Running needs only local files. The image holds the
`prod` environment of `pixi.lock` (runtime only, about 210 MB), so it runs exactly the
versions the tests run.

```sh
apptainer build soba_matchups.sif apptainer.def
apptainer run \
  --bind /path/to/sar/archive:/path/to/sar/archive:ro \
  --bind /path/to/knmi/l2b:/path/to/knmi/l2b:ro \
  --bind /path/to/work:/work \
  soba_matchups.sif run --recipe /work/hscat.toml
```

## Output format

See [architecture_SAR_matchups_design_document.md](architecture_SAR_matchups_design_document.md). Usage notes are in the
[documentation](https://soba-matchups.readthedocs.io/en/latest/).
