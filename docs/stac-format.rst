STAC output format
==================

Each JSON in ``items/`` is a standalone STAC 1.0.0 Item for one matched
Sentinel-1 WV OCN imagette. It contains every retained association with the
selected KNMI L2B ASCAT or HSCAT granules. An imagette without matches produces no Item.
This is a matchup index with reference measurements, not an enriched co-aligned
Parquet or NetCDF product.

Filename and identity
---------------------

Files use this convention::

   <SAFE name without .SAFE>__IMG<NNN>__HY2B_HSCAT.json

For example::

   S1A_WV_OCN__2SSV_20260629T120000_20260629T121003_000001_000001_0001__IMG001__HY2B_HSCAT.json

The SAFE name identifies the parent product, including its processing checksum;
``IMG001`` is the numeric identifier from the measurement filename, padded to
three digits. It does not assign the footprint by sequence. The times in the
filename belong to the parent SAFE; the JSON contains the imagette's own times.
The suffix identifies the reference platform and sensor: ``HY2B_HSCAT``,
``HY2C_HSCAT``, ``HY2D_HSCAT``, ``METOPB_ASCAT`` or ``METOPC_ASCAT``.
Resolution and processing version are recorded in provenance rather than filenames;
use separate output directories for different selections.

Offline reference support
-------------------------

Recipes select an explicit ``scat`` / ``met`` / ``resolution`` combination:

* ``ascat`` / ``metop-b`` / ``25km``, ``12.5km`` or ``5.7km``.
* ``ascat`` / ``metop-c`` / ``12.5km``.
* ``hscat`` / ``hy-2b``, ``hy-2c`` or ``hy-2d`` / ``25km``.

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
from degrees alone. Synthetic legacy fixtures now declare their convention explicitly.

The combined header and readable-cell time range must not exceed 24 hours. This is
a conservative granule sanity bound, not a matching tolerance; it rejects the
surveyed malformed ASCAT 5.7 km header spanning 2000 to 2018, and cell-time outliers
that could otherwise expand SAR discovery across years. Coverage can still extend
beyond the header within that bound. The strict SAR matching rule is unchanged.

The seven-product integration tests use manufactured small arrays and surveyed
header encodings; they do not establish measurement-level scientific parity or
archive completeness. Connected, bounded real-product matching remains necessary.

The Item's ``id`` remains an opaque, deterministic local hash derived from the
parent SAFE identity and measurement-relative path. It is not a CDSE UUID and
need not equal the filename stem. Changing the selected granules or tolerance
can change the contents without changing this source-based identity or filename.
Use separate, fresh output directories for different runs. Duplicate filenames
or Item IDs within a run stop publication rather than overwrite an Item.

Core STAC fields
----------------

.. csv-table::
   :header: "Field", "Meaning"
   :widths: 30, 70

   "type", "Feature"
   "stac_version", "1.0.0"
   "stac_extensions", "Empty array: no hosted extension schema is claimed"
   "id", "Stable local imagette identifier"
   "geometry", "Imagette footprint in WGS84 longitude/latitude; Polygon or MultiPolygon"
   "bbox", "[minimum longitude, minimum latitude, maximum longitude, maximum latitude]"
   "properties.datetime", "null; the start/end fields describe an interval"
   "properties.start_datetime", "Imagette acquisition start, UTC"
   "properties.end_datetime", "Imagette acquisition end, UTC"
   "assets", "Actual source files; see below"
   "links", "Empty array; no catalogue or collection URLs are invented"

A dateline-crossing footprint remains one imagette represented as a MultiPolygon.
Its bounding box follows the coordinate extrema and can span -180 to 180 degrees.
The acquisition interval comes from the OCN file's ``firstMeasurementTime`` and
``lastMeasurementTime`` attributes, not the parent SAFE interval or filename clock.

Matchup properties
------------------

These fields live under ``properties``. They borrow names from the supplied
SAR Matchup draft but do not claim full compliance with that extension.

.. csv-table::
   :header: "Field", "Meaning"
   :widths: 48, 52

   "sarmatchup:sar_platform", "SAR platform, such as sentinel-1a"
   "sarmatchup:sar_instrument_mode", "WV"
   "sarmatchup:sar_product_type", "OCN"
   "sarmatchup:sar_safe", "Full parent OCN SAFE name"
   "sarmatchup:other_instrument", "Validated file source, e.g. HY-2B HSCAT or MetOp-B ASCAT"
   "sarmatchup:other_type", "scatterometer"
   "sarmatchup:other_geometry_type", "MultiPoint: retained observation locations, not a swath polygon"
   "sarmatchup:searching_window_hours", "Configured tolerance in hours, applied on each side of the acquisition interval"
   "sarmatchup:lib_version", "soba_matchups library version"
   "sarmatchup:n_coloc_points", "Number of retained point associations across all granule groups"
   "sarmatchup:delta_diff_time_minutes_min", "Minimum signed point time difference in this Item"
   "sarmatchup:delta_diff_time_minutes_max", "Maximum signed point time difference in this Item"

Point counts count associations, not unique coordinate pairs. Different source
observations at the same location remain distinct. Repeated identical associations
are discarded using their deterministic record identifiers.

Each value has one authoritative field: ``sarmatchup:sar_safe``,
``sarmatchup:sar_product_type``, ``sarmatchup:sar_platform``, and the core
``start_datetime`` / ``end_datetime`` interval. Duplicate SAFE, product-type,
platform and acquisition-time aliases are omitted. The primary SAFE is already
OCN, so no separate ``sarmatchup:sar_ocn_safe`` alias is emitted.

Project-specific properties retain the distinct identity and provenance details:

.. csv-table::
   :header: "Field", "Meaning"
   :widths: 40, 60

   "soba:safe_id", "Local hash of the parent SAFE name"
   "soba:imagette_index", "Numeric identifier from the OCN measurement filename"
   "soba:measurement_name", "Actual imagette NC filename"
   "soba:polarization", "VV or HH"
   "soba:sea_percent", "Percentage of the imagette footprint outside the selected land mask, computed in EPSG:6933; not a filter"
   "soba:mapping_policy", "local_ocn_geolocation_to_manifest_frame"
   "soba:safe_id_source", "Description of the local SAFE identifier convention"
   "soba:time_source", "OCN UTC attribute names used for the acquisition interval"
   "soba:time_tolerance_minutes", "Configured acquisition-interval margin in minutes"
   "soba:matchups", "Array of source-granule groups described below"

Granules and points
-------------------

Each object in ``properties.soba:matchups`` represents one source granule:

.. csv-table::
   :header: "Field", "Meaning"
   :widths: 38, 62

   "reference_asset", "Key of this granule in the Item's assets object"
   "reference_file", "Original granule filename"
   "reference_source", "Actual validated global source attribute"
   "reference_version", "Actual software_identification_wind as a string"
   "reference_resolution", "Actual pixel_size_on_horizontal attribute, e.g. 12.5 km"
   "sarmatchup:other_id", "Same granule filename; scoped to this group rather than the entire Item"
   "reference_geometry", "GeoJSON MultiPoint with one [longitude, latitude] coordinate per retained point, in points-array order"
   "points", "Matched observations with their original source indices and values"

``reference_geometry`` describes only the selected observation centres. It is
neither the whole granule footprint nor a reconstruction of cell boundaries.
Positions can repeat when observations share a location.

Each object in ``points`` contains:

.. csv-table::
   :header: "Field", "Meaning"
   :widths: 38, 62

   "record_id", "SHA256 derived from imagette ID, reference file checksum and original flat cell index"
   "ref_row_0based", "Original NetCDF row index, starting at zero"
   "ref_cell_0based", "Original NetCDF cross-track cell index, starting at zero"
   "ref_time_utc", "Observation time, UTC"
   "ref_lon", "Longitude in degrees east, normalized to [-180, 180)"
   "ref_lat", "Latitude in degrees north"
   "wind_speed", "Wind speed in m/s"
   "wind_direction_to", "Wind direction in degrees, flowing toward"
   "quality_flag", "Original numeric quality flag; null if nonfinite"
   "ice_probability", "Original ice_prob value; null if nonfinite; no unit conversion"
   "delta_diff_time_minutes", "Signed observation time minus imagette acquisition midpoint, in minutes"

Positive time differences mean the reference observation is later than the SAR
midpoint; negative differences mean it is earlier. The Item-level minimum and
maximum summarize these exact per-point values without choosing one representative
observation. Times retain their available source precision; differences are JSON
numbers without display rounding.

Matching still uses the interval, not a midpoint-distance cutoff. Whole-second
OCN header times may differ from fractional manifest endpoints: validation uses
the manifest start rounded down and stop rounded up only for whole-second header
values. Discovery and the coarse SAFE prefilter include one second of precision
padding; the final imagette/point test below does not receive that extra padding::

   imagette_start - tolerance < observation_time < imagette_end + tolerance

A point must also lie strictly inside the same imagette footprint. The artificial
GeoJSON cut at the dateline is not treated as a physical scene boundary. Finite
wind observations survive regardless of quality/ice flags; those flags remain
available for downstream filtering.

Assets and run files
--------------------

``assets.sar`` points to the original OCN imagette NC, and
``assets.sar_manifest`` points to its native ``manifest.safe`` with a
``soba:sha256`` checksum. ``assets.reference_001``, ``reference_002``, and so on
point to the contributing granules, each with its checksum. SAR and reference
assets use the ``data`` role; the manifest uses ``metadata``. NC assets use
``application/x-netcdf``, compressed references use ``application/gzip``, and the
manifest uses ``application/xml``.

Assets use absolute ``file:`` URIs reflecting runtime paths. Those paths must
remain accessible, including container mount paths. No data files are copied into
the result folder. No ``coloc_netcdf`` asset is advertised because none is produced.

The output directory also contains:

* ``diagnostics.json``: imagette/granule matching counts.
* ``sar_metadata.json``: selected local SAFE/imagette metadata and source identities.
* ``manifest.json``: resolved recipe, source provenance, matching rules, counts,
  and hashes of output artifacts. Run-manifest schema version 7 describes this layout;
  it is separate from STAC's version number.

Each ``manifest.reference_inputs`` entry retains its path, SHA256 and byte size,
and adds the actual per-file ``source``, ``version`` and ``resolution``. These values
come from validated file attributes, not merely the requested recipe. Item granule
groups carry the same values under the ``reference_*`` names above, so numeric
quality flags remain tied to their source processing version without reinterpretation.

Differences from the SAR Matchup draft
--------------------------------------

* One Item groups all matched granules for an imagette, rather than treating every
  SAR/reference-granule pair as a separate event.
* Reference IDs and MultiPoint geometries belong to granule groups. We do not
  invent scatterometer swath polygons.
* Each point carries its signed time difference, with Item-level minimum/maximum;
  the draft's single ``sarmatchup:delta_diff_time_minutes`` is absent.
* Geometry accepts MultiPolygon for dateline imagettes, rather than Polygon only.
* Core start/end times describe the SAR acquisition, not the entire reference-time range.
* Unavailable SLC/GRD/RAW products, external catalogue URLs and institution-wide
  availability flags remain absent. So do redundant WKT footprints.
* The draft's extension URL is a placeholder. Both namespaces are custom properties;
  an empty ``stac_extensions`` array declares no external schema compliance.
