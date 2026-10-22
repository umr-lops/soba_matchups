# soba_matchups

[![CI](https://github.com/umr-lops/soba_matchups/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/umr-lops/soba_matchups/actions/workflows/ci.yml)
[![Build](https://github.com/umr-lops/soba_matchups/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/umr-lops/soba_matchups/actions/workflows/build.yml)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](.pre-commit-config.yaml)
[![Python](https://img.shields.io/badge/python-%3E%3D3.11-blue?logo=python)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Sentinel-1 Wave Mode matchups with SCAT, SWOT KaRIn and ALTI references.

## Development

```sh
conda env create -f environment.yml
conda activate soba_matchups
python -m pip install -e . --no-build-isolation
pre-commit install
pre-commit run --all-files
python -m pytest -q
python -m build
```

Hooks and CI follow `soba_reference_repo`: file hygiene, Ruff and tests. A separate
workflow builds distributions. Pytest is a manual local hook; CI always runs it.
Actions are pinned to verified commit SHAs.

Publish is prepared but disabled pending PyPI Trusted Publisher setup and the
`PYPI_PUBLISH_ENABLED=true` repository variable. It requires a publisher for
`umr-lops/soba_matchups`, workflow `publish.yml`, environment `pypi`. Bump the
version in `src/soba_matchups/__init__.py` before publishing a release.
