soba_matchups
=============

Match local Sentinel-1 WV L2 OCN imagettes with KNMI L2B scatterometer observations
(ASCAT and HSCAT) or SWOT KaRIn L2 wave and wind cells. No L1 input, catalogue API, cache
or credentials.

The output format is specified in
`architecture_SAR_matchups_design_document.md <https://github.com/umr-lops/soba_matchups/blob/main/architecture_SAR_matchups_design_document.md>`_.

Run
---

Install from the checkout::

   conda env create -f environment.yml
   conda activate soba_matchups
   python -m pip install -e . --no-build-isolation

Copy ``examples/hscat.toml`` and edit the paths and dates. Set
``reference_root`` to the KNMI ``satellite/l2b`` root and ``sar_archive_root``
to the common ESA root, not a particular mission/day directory. In a container,
bind the KNMI tree at its real path and use that path as ``reference_root``, so
the asset links in the output name the real files. The expected
SAR hierarchy is ``sentinel-1a/L2/WV/S1A_WV_OCN__2S/YYYY/DDD/*.SAFE`` for S1A,
with corresponding mission paths for S1B/C/D. Each extracted SAFE needs its
``manifest.safe`` and all listed ``measurement/*.nc`` files::

   soba_matchups validate --recipe hscat.toml
   soba_matchups run --recipe hscat.toml

``validate`` checks the recipe and reference files; ``run`` also reads SAR metadata.
``stop_date`` is exclusive. ``output_dir`` must be new. Replace the previous
``sar_cache_dir`` / ``offline`` keys with ``sar_archive_root``.

Reference products
------------------

Recipes select an explicit ``instrument`` / ``platform`` / ``resolution`` combination:

* ``ascat`` / ``metop-b`` / ``25km``, ``12.5km`` or ``5.7km``.
* ``ascat`` / ``metop-c`` / ``12.5km``.
* ``hscat`` / ``hy-2b``, ``hy-2c`` or ``hy-2d`` / ``25km``.
* ``karin`` / ``swot`` / ``2km``: SWOT KaRIn L2 wave and wind (``examples/swot.toml``).

Other combinations, including ``metop-d``, are rejected, not substituted.
Any processing version is accepted, so the recipe has no version key. Each file's
global ``software_identification_wind`` is recorded in the manifest and Items, and
``manifest.reference_versions`` lists every version used in the run.

Every file must also pass source, pixel-size, variable/dimension, unit and direction
checks. The common reader derives grid sizes dynamically and applies CF scale,
offset, fill and time decoding, including gzip inputs and either ``ovw.l2`` or
``ovw_l2`` filename spelling. Required variables are ``lon``, ``lat``, ``time``,
``wind_speed``, ``wind_dir``, ``wvc_quality_flag`` and ``ice_prob``, each on
``NUMROWS`` x ``NUMCELLS``. Coordinates require ``degrees_east`` / ``degrees_north``,
wind requires ``m s-1`` / ``degree``, and ice probability requires ``1``.

Directions require ``standard_name=wind_to_direction``. If that attribute is absent,
the reader accepts only the explicit global-comment declaration found in the real
ASCAT 2101 header: ``All wind directions in oceanographic convention (0 deg. flowing North)``.
An unknown convention or contradictory standard name fails; no direction is inferred
from degrees alone.

The combined header and readable-cell time range must not exceed 24 hours. This is
a conservative granule sanity bound, not a matching tolerance; it rejects the
surveyed malformed ASCAT 5.7 km header spanning 2000 to 2018, and cell-time outliers
that could otherwise expand SAR discovery across years. Coverage can still extend
beyond the header within that bound. The strict SAR matching rule is unchanged.

The seven-product integration tests use manufactured small arrays and surveyed
header encodings; they do not establish measurement-level scientific parity or
archive completeness.

Matching and output
-------------------

Each OCN file's ``oswLon``/``oswLat`` centres identify its unique native manifest
frame, independent of file or frame order. Its own ``firstMeasurementTime`` and
``lastMeasurementTime`` attributes supply the UTC interval. Missing or ambiguous
associations fail explicitly; there is no suffix-order fallback. Dateline pieces
remain one imagette. SAR discovery includes tolerance and the previous archive
day to include acquisitions crossing midnight. Missing day folders are recorded;
local coverage is not checked against an external catalogue.

A point must lie inside the same imagette and within its configured time tolerance.
Quality/ice flags remain unfiltered.

Outputs: one STAC 1.0.0 JSON in ``items/`` per matched imagette, plus
``diagnostics.json``, ``sar_metadata.json`` and ``manifest.json``. The
``sarmatchup:matchups`` groups preserve source granules, original row/cell indices,
times, winds and flags. The Item ``id`` is a local hash, not a catalogue UUID.
This is not a co-aligned Parquet exporter.

See the `repository README <https://github.com/umr-lops/soba_matchups#readme>`_
for the Apptainer build and local archive binds. Runtime needs no network access.
