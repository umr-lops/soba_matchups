soba_matchups
=============

Match Sentinel-1 Wave Mode SAFE metadata with reference observations.
Currently supports HY-2B HSCAT 25 km, processing version 2202.

Install
-------

From a repository checkout::

   conda env create -f environment.yml
   conda activate soba_matchups
   python -m pip install -e . --no-build-isolation

Run
---

Copy ``examples/hscat.toml`` to ``hscat.toml`` and edit the paths and dates.
Set ``reference_root`` to the KNMI ``satellite/l2b`` archive root.
``stop_date`` is exclusive. Set ``land_mask`` to a local OSM land-polygon
shapefile with its sidecars, or use the bundled path inside the SIF::

   soba_matchups validate --recipe hscat.toml
   soba_matchups run --recipe hscat.toml

Matching uses full SAFE footprints and acquisition intervals, not resolved
imagettes. Quality and ice flags are retained without filtering.
``offline = true`` requires a complete SAR metadata cache for the same query.
``output_dir`` must be new.

Outputs are ``matchups.csv``, ``pair_diagnostics.csv``, ``sar_metadata.json``
and ``manifest.json``. These are SAFE/reference-cell matchups, not enriched
co-aligned catalogues.

See the `repository README <https://github.com/umr-lops/soba_matchups#readme>`_
for Apptainer build/run instructions and the example recipe.
