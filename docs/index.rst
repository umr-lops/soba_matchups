soba_matchups
=============

.. toctree::
   :maxdepth: 1

   stac-format

Match local Sentinel-1 WV L2 OCN imagettes with HY-2B HSCAT 25 km observations
(processing version 2202). No L1 input, catalogue API, cache or credentials.

Run
---

Install from the checkout::

   conda env create -f environment.yml
   conda activate soba_matchups
   python -m pip install -e . --no-build-isolation

Copy ``examples/hscat.toml`` and edit the paths and dates. Set
``reference_root`` to the KNMI ``satellite/l2b`` root and ``sar_archive_root``
to the common ESA root, not a particular mission/day directory. The expected
SAR hierarchy is ``sentinel-1a/L2/WV/S1A_WV_OCN__2S/YYYY/DDD/*.SAFE`` for S1A,
with corresponding mission paths for S1B/C/D. Each extracted SAFE needs its
``manifest.safe`` and all listed ``measurement/*.nc`` files::

   soba_matchups validate --recipe hscat.toml
   soba_matchups run --recipe hscat.toml

``validate`` checks the recipe and reference files; ``run`` also reads SAR metadata.
``stop_date`` is exclusive. ``output_dir`` must be new. Replace the previous
``sar_cache_dir`` / ``offline`` keys with ``sar_archive_root``.

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
Quality/ice flags remain unfiltered. ``sea_percent`` uses the imagette footprint
and selected mask in EPSG:6933, without a sea-percent filter.

Outputs: one STAC 1.0.0 JSON in ``items/`` per matched imagette, plus
``diagnostics.json``, ``sar_metadata.json`` and ``manifest.json``. Custom
``soba:matchups`` groups preserve source granules, original row/cell indices,
times, winds and flags. SAR identifiers are local hashes, not catalogue UUIDs.
Local assets use the runtime mount paths. This is not a co-aligned Parquet exporter.
See :doc:`stac-format` for filenames, fields and time differences.

See the `repository README <https://github.com/umr-lops/soba_matchups#readme>`_
for the Apptainer build and local archive binds. The SIF bundles the OSM mask;
runtime needs no network access.
