#!/bin/bash
# One task of a turboblast job array (https://github.com/umr-lops/turboblast): soba_matchups
# with the arguments of one line of `soba_matchups listing`, e.g.
#   run-day --recipe /abs/path/iw_d2.toml --day 2026-11-22
#
# Environment (exported in the shell that runs turboblaster; Slurm passes it to the tasks):
#   SOBA_MATCHUPS_SIF   Apptainer image to run soba_matchups from; unset: `soba_matchups`
#                       from PATH (e.g. inside `pixi shell`).
#   SOBA_MATCHUPS_BIND  comma-separated bind paths for apptainer
#                       (default: /scratch,/scale,/ontap,/legacy, the Ifremer HPC data spaces).
set -euo pipefail
# Group-writable outputs, for project folders shared with other members.
umask 0002
if [ -n "${SOBA_MATCHUPS_SIF:-}" ]; then
    exec apptainer run --bind "${SOBA_MATCHUPS_BIND:-/scratch,/scale,/ontap,/legacy}" \
        "$SOBA_MATCHUPS_SIF" "$@"
fi
exec soba_matchups "$@"
