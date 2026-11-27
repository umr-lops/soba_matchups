# soba_matchups

[![CI](https://github.com/umr-lops/soba_matchups/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/umr-lops/soba_matchups/actions/workflows/ci.yml)
[![Build](https://github.com/umr-lops/soba_matchups/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/umr-lops/soba_matchups/actions/workflows/build.yml)
[![Docs](https://img.shields.io/readthedocs/soba-matchups?logo=readthedocs)](https://soba-matchups.readthedocs.io/en/latest/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](.pre-commit-config.yaml)
[![Python](https://img.shields.io/badge/python-%3E%3D3.11-blue?logo=python)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Match Sentinel-1 L2 OCN products (WV imagettes, or IW, EW and SM scenes) with scatterometer winds (KNMI ASCAT, HSCAT), SWOT KaRIn waves and winds,
altimeter wave heights (CCI, CMEMS) or buoy winds (NDBC, TAO and CMEMS in situ).
The output is one STAC JSON file per matched imagette, or per matched IW/EW/SM scene.


## Run it

```sh
pixi install                        # the environment, from pixi.lock (https://pixi.sh)

cp examples/hscat.toml hscat.toml   # then edit the paths and dates
pixi run soba_matchups validate --recipe hscat.toml
pixi run soba_matchups run --recipe hscat.toml
```

`pixi shell` opens a shell in the environment, where `soba_matchups` runs directly.
`pixi run test` runs the tests and `pixi run lint` the pre-commit hooks.

There is one example per reference product (`hscat`, `alti`, `swot`, `buoy`), each with the same
SAR-side keys. For IW, EW or SM, set `sar_mode = "IW"` (or `"EW"`, `"SM"`); these need
`sar_parquet_root`, the CDSE daily parquet files (`daily_parquet_files_v2`), which does not list
SM products yet, so SM runs find none until its producer is fixed. `output_dir` must not exist yet. A finished run holds `items/` (the STAC files),
`diagnostics.json`, `sar_metadata.json` and `manifest.json`.

## Many days on Slurm (job arrays, turboblast)

A per-day recipe has `{day}` in its `output_dir` (`start_date` / `stop_date` are then the
default range). `listing` writes one task line per day whose output folder does not exist yet;
`run-day` runs one of them, and skips a day already done (`--overwrite` replaces it, keeping the
old folder until the new run succeeds) or without any reference file yet.
[turboblast](https://github.com/umr-lops/turboblast) submits the lines as Slurm job arrays, under
your own user id, with `scripts/slurm/soba_matchups_task.sh` as the task:

```sh
# output_dir = "/scale/.../soba_matchups/swot/d2/{day}/ocn/iw" in iw_d2.toml
pixi run soba_matchups validate --recipe iw_d2.toml --day 2026-11-22   # check one day first
pixi run soba_matchups listing --recipe iw_d0.toml >  tasks.txt
pixi run soba_matchups listing --recipe iw_d2.toml --start 2026-11-16 >> tasks.txt
export SOBA_MATCHUPS_SIF=/path/to/soba_matchups.sif   # unset: soba_matchups from PATH
pixi run -e slurm turboblaster --listing-input tasks.txt \
  --bash-slurm-exec scripts/slurm/soba_matchups_task.sh \
  --timeout-min 30 --mem 4G --slurm-array-parallelism 30 --output-dir logs_soba
pixi run -e slurm slurm-logs logs_soba/<timestamp>     # successes, failures and their causes
```

One task per day and recipe; listings of several recipes can be concatenated. Running the
listing again after a failure lists only the days still missing. The `slurm` environment adds
turboblast to the runtime; turboblaster blocks until every task ends, so run it in tmux/byobu.
An IW day takes 1 – 1.5 min and under 1.5 GB (SWOT, 2026-11); a WV day longer: size `--timeout-min` and `--mem` on a
few days first.

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
