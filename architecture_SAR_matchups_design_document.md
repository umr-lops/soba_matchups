# SAR matchups design document

This document specifies the matchup collections and what `soba_matchups run` writes:
STAC 1.0.0 Items and three run files. The run manifest layout is `schema_version` 11.

The output is a matchup index. Each Item links one Sentinel-1 WV OCN imagette to
reference observations that fall inside it: KNMI L2B scatterometer winds (ASCAT or
HSCAT), SWOT KaRIn L2 wave heights and winds, or ESA CCI Sea State altimeter wave
heights. It is not an enriched co-aligned Parquet or NetCDF product.

One adapter per reference instrument finds the reference files, reads them and names
the point fields (`soba_matchups.adapters`: `scat` for ASCAT and HSCAT, `swot` for
KaRIn, `alti` for the CCI altimeters). The SAR side and the matching rule are the same
for every adapter. Section 12 describes the altimeter reference.

## 1. Product types and collections

The tool reads Sentinel-1 WV Level-2 OCN products only. Discovery rejects any other
product type.

The co-aligned catalogue links each WV acquisition to two products, OCN and SLC. Its
rows carry `sar_path_ocn`, `sar_path_slc`, `sar_safe_ocn` and `sar_safe_slc`, and no
GRD column. On the one OCN/SLC pair compared, both manifests had 64 frames, and the
frame of imagette 3 had an intersection over union of 0.9984 (largest vertex offset
0.00016°). The same imagette has the same number, the same datatake and nearly the
same times in both products.

One collection per product type:

| Proposed collection id | Product | Status |
| --- | --- | --- |
| `sar-matchups-wv-ocn` | Sentinel-1 WV L2 OCN | Items implemented; no collection document |
| `sar-matchups-wv-slc` | Sentinel-1 WV L1 SLC | not implemented |
| none | GRD | no WV GRD product appears in the catalogue; confirm it exists before defining a collection |

- One run reads one product type. A run or a collection never mixes types: the OCN
  Item and the SLC Item of one imagette have the same footprint and time, so they
  match the same reference points, and every association would appear twice.
- The product type is visible in the SAFE name (`OCN__2S`, `SLC__1S`) and in
  `sarmatchup:sar_product_type`. The file naming in section 3 needs no change.
- A recipe key `product_type` (default `OCN`) would select the collection. Not
  implemented.
- SLC has no OSW cell centres, so the footprint rule changes: the imagette frame
  would come from the annotation's geolocation grid. On one imagette, the grid's
  centroid fell in the same frame that the OCN rule selects. Not implemented.
- Today `links` is `[]` and Items carry no `collection` field. Writing one
  `collection.json` per product type, and linking each Item to it, is part of this
  design. Not implemented.

## 2. Run folder

```
<output_dir>/
  items/<name>.json   one STAC Item per matched imagette
  diagnostics.json    matching counts per imagette and reference granule
  sar_metadata.json   SAFEs and imagettes read from the SAR archive
  manifest.json       resolved recipe, input checksums, counts, rules, limitations
                      and the SHA-256 of every file above
```

`output_dir` must not exist. The tool writes a temporary folder and renames it, so a
run is complete or absent. An imagette without a match produces no Item.

## 3. File naming

```
<SAFE name without .SAFE>__IMG<NNN>__<PRODUCT>.json
```

Example (SAFE `S1B_WV_OCN__2SSV_20200101T011231_20200101T012925_019616_02512F_8F63`,
imagette 4, HY-2B HSCAT at 25 km from KNMI):

```
S1B_WV_OCN__2SSV_20200101T011231_20200101T012925_019616_02512F_8F63__IMG004__KNMI_HSCAT_HY2B_25KM.json
```

| Part | Meaning |
| --- | --- |
| SAFE name | Parent product, as named in the archive. |
| `IMG<NNN>` | Imagette number from the measurement file name, padded to three digits. |
| `PRODUCT` | Label of the reference product: provider, instrument, platform and resolution. It comes from the product table `REFERENCE_PRODUCTS` in `soba_matchups.config` and is written to `manifest.reference_product`. The decimal point of the resolution is written `P`. |

Product label by recipe:

| `instrument` / `platform` / `resolution` | `PRODUCT` |
| --- | --- |
| `hscat` / `hy-2b` / `25km` | `KNMI_HSCAT_HY2B_25KM` |
| `hscat` / `hy-2c` / `25km` | `KNMI_HSCAT_HY2C_25KM` |
| `hscat` / `hy-2d` / `25km` | `KNMI_HSCAT_HY2D_25KM` |
| `ascat` / `metop-b` / `25km` | `KNMI_ASCAT_METOPB_25KM` |
| `ascat` / `metop-b` / `12.5km` | `KNMI_ASCAT_METOPB_12P5KM` |
| `ascat` / `metop-b` / `5.7km` | `KNMI_ASCAT_METOPB_5P7KM` |
| `ascat` / `metop-c` / `12.5km` | `KNMI_ASCAT_METOPC_12P5KM` |
| `karin` / `swot` / `2km` | `PODAAC_SWOT_KARIN_L2_WINDWAVE_D0` |
| `alti` / `jason-3` / `l2p` | `ESACCI_SEASTATE_L2P_JASON3` |
| `alti` / `cryosat-2`, `jason-2`, `saral`, `sentinel-3a`, `sentinel-3b`, `sentinel-6a` / `l2p` | `ESACCI_SEASTATE_L2P_` plus `CRYOSAT2`, `JASON2`, `SARAL`, `S3A`, `S3B`, `S6A` |

The same imagette matched to SWOT is named
`S1D_WV_OCN__2SSV_20260622T213558_20260622T214335_003354_005E47_1F38__IMG010__PODAAC_SWOT_KARIN_L2_WINDWAVE_D0.json`.

The processing version of the reference files is not part of the name. The Item
records it per granule (`reference_version`). The name does not encode the time
tolerance either, so use a fresh output folder for each run. A duplicate name inside
one run stops publication.

The convention is implemented in `soba_matchups.output.item_filename`.

The SAFE name already holds the product type (`OCN__2S`, `SLC__1S`), so the same
convention applies to every collection.

## 4. Reference points per SAR image

One Item per imagette holds every reference observation that passes the matching
rule in section 8: a measured cell, inside the footprint, within the time tolerance. Nothing is dropped for quality or ice flags, and nothing is reduced to a
single best point.

- Points are grouped by reference granule in `sarmatchup:matchups` (section 7), with
  one group and one asset per granule.
- `sarmatchup:n_coloc_points` is the total. `sarmatchup:delta_diff_time_minutes_min`
  and `_max` summarise the signed time differences.
- An imagette without a point has no Item.
- Counts seen in tests (KNMI): 1,151 points on 1,150 imagettes for HY-2D HSCAT 25 km, and
  52 points on 21 imagettes for MetOp-B ASCAT 12.5 km (version 2401). One imagette
  holds one or several points, and several more often with ASCAT 12.5 km.
- The co-aligned catalogue holds one row, one reference cell, per imagette. In every
  comparison run, each catalogue pair is also in the Items, and the Items add the
  other cells of the footprint.
- With SWOT, one imagette holds the cells of every SWOT line that crosses it, from about
  3 to 117 cells in the test run (median 98). An imagette seen by two SWOT passes gets
  two granule groups in one Item.
- Choosing one point per imagette, for example the one nearest the centre, is a
  downstream step. The footprint is the Item geometry, and each point has its position
  and time difference. The distance to the centre is not stored.

## 5. Mandatory STAC variables

STAC 1.0.0 requires these fields in an Item. Each was removed in turn from a real
Item and the result checked with stac-validator 4.2.2 against the STAC 1.0.0 Item
schema.

| Field | STAC 1.0.0 | Value here |
| --- | --- | --- |
| `stac_version` | required | `1.0.0` |
| `type` | required | `Feature` |
| `id` | required | local hash |
| `geometry` | required | `Polygon` or `MultiPolygon` |
| `bbox` | required (every Item has a geometry) | bounds of `geometry` |
| `properties` | required | object |
| `properties.datetime` | required key; `null` is accepted when `start_datetime` and `end_datetime` are both set (removing `end_datetime` is refused) | `null`, with the interval |
| `links` | required | `[]` |
| `assets` | required | object |
| `assets.*.href` | required | absolute `file:` URI |
| `stac_extensions` | optional | `[]` |
| `assets.*.type`, `roles`, `title` | optional | set on every asset |

The `sarmatchup:*` properties and everything under `sarmatchup:matchups` are not
required by STAC, and no schema checks them. If an Item links to a collection, the
STAC specification also requires its `collection` field. Our Items have no such link
yet (section 1).

The same Item with only `stac_version` set to `1.1.0` also passes (one Item tested).

## 6. Item

### Core fields

| Field | Value |
| --- | --- |
| `type` | `Feature` |
| `stac_version` | `1.0.0` |
| `stac_extensions` | `[]`. No extension schema is claimed. |
| `id` | `local_` plus a SHA-256 of the SAFE name and the measurement file's path inside the SAFE. Deterministic and unique per imagette. Not a CDSE UUID, and not equal to the file name. |
| `geometry` | Imagette footprint in WGS84, `Polygon` or `MultiPolygon`. Positions are `[longitude, latitude]`, the GeoJSON order that STAC requires. |
| `bbox` | `[west, south, east, north]`, the bounds of `geometry`. |
| `properties.datetime` | `null`. The interval is in `start_datetime` and `end_datetime`. |
| `properties.start_datetime`, `end_datetime` | Imagette acquisition interval, UTC, from the `firstMeasurementTime` and `lastMeasurementTime` attributes of the OCN file. |
| `assets` | Source files, see section 9. |
| `links` | `[]`. No catalogue or collection URL is invented. |

A footprint that crosses the dateline remains one imagette, stored as a
`MultiPolygon` cut at ±180°. Its `bbox` is the bounds of that geometry,
`[-180, south, 180, north]`. `stac-check` 1.14 reports such a box as one that
"belts the globe".

### Properties

All project properties use the `sarmatchup:` prefix. They borrow names from the
SAR Matchup draft and do not claim compliance with it.

| Field | Meaning |
| --- | --- |
| `sarmatchup:sar_platform` | SAR platform, for example `sentinel-1a`. |
| `sarmatchup:sar_instrument_mode` | `WV` |
| `sarmatchup:sar_product_type` | `OCN` |
| `sarmatchup:sar_safe` | Full parent SAFE name. |
| `sarmatchup:other_instrument` | Reference source, for example `HY-2B HSCAT`, `SWOT KaRIn` or `Jason-3 altimeter`. |
| `sarmatchup:other_type` | `scatterometer` for ASCAT and HSCAT, `altimeter` for SWOT KaRIn and the CCI altimeters. |
| `sarmatchup:other_geometry_type` | `MultiPoint`: the reference observations are points, given by `ref_lon` and `ref_lat`, not a swath outline. |
| `sarmatchup:searching_window_hours` | Time tolerance in hours, applied on each side of the acquisition interval. |
| `sarmatchup:lib_version` | `soba_matchups` version. |
| `sarmatchup:n_coloc_points` | Number of retained associations across all granule groups. |
| `sarmatchup:delta_diff_time_minutes_min`, `_max` | Smallest and largest signed point time difference in this Item. |
| `sarmatchup:imagette_index` | Imagette number from the measurement file name. |
| `sarmatchup:measurement_name` | Name of the imagette NetCDF file. |
| `sarmatchup:polarization` | `VV` or `HH`. |
| `sarmatchup:mapping_policy` | `local_ocn_geolocation_to_manifest_frame`: how the imagette footprint was chosen. |
| `sarmatchup:matchups` | Array of granule groups, section 7. |

`sarmatchup:n_coloc_points` counts associations, not distinct coordinates. Two
observations at one location stay two associations. Repeated identical associations
are discarded by their `record_id`.

Not in the Item: the sea percentage of the footprint. It belongs to a later
ancillary step, not to the matchup step.

## 7. Granule groups

Each object in `sarmatchup:matchups` describes one reference granule.

| Field | Meaning |
| --- | --- |
| `reference_asset` | Key of this granule in `assets`. |
| `reference_file` | Original granule file name. |
| `reference_source` | Validated `source` attribute (KNMI); `SWOT KaRIn` for SWOT; `<mission> altimeter` for CCI. |
| `reference_version` | KNMI: `software_identification_wind` attribute, as a string. SWOT: processing level and version from the file name, for example `PID0_01`. CCI: dataset release from the file's `references` attribute and the `fv` number of the name, for example `v4.0_fv01`. |
| `reference_resolution` | KNMI: `pixel_size_on_horizontal` attribute, for example `12.5 km`. SWOT: the recipe's resolution, `2 km`, since the file has no such attribute. CCI: `1 Hz`, the sampling of the points. |
| `sarmatchup:other_id` | Same value as `reference_file`, scoped to this group. |
| `points` | Matched observations, section 8. |

## 8. Points

Fields common to every reference:

| Field | Meaning |
| --- | --- |
| `record_id` | SHA-256 of the imagette id, the reference file checksum and the original flat cell index. |
| `ref_row_0based` | Row index in the reference NetCDF. |
| `ref_cell_0based` | Cross-track cell index in the reference NetCDF. |
| `ref_time_utc` | Observation time, UTC. |
| `ref_lon` | Longitude in degrees east, in [-180, 180). |
| `ref_lat` | Latitude in degrees north. |
| `delta_diff_time_minutes` | Observation time minus the imagette midpoint, in minutes. Positive means the observation is later. |

KNMI fields (ASCAT, HSCAT):

| Field | Meaning |
| --- | --- |
| `wind_speed` | m s-1 |
| `wind_direction_to` | Degrees, direction the wind blows toward. |
| `quality_flag` | Original numeric flag, `null` if not finite. |
| `ice_probability` | Original `ice_prob`, `null` if not finite. No unit conversion. |

SWOT fields (a value is `null` if the file has none for that cell):

| Field | Meaning |
| --- | --- |
| `swh_karin` | Significant wave height, m. |
| `swh_karin_qual` | Quality flag of the wave height, original integer. |
| `swh_karin_uncert` | Wave height uncertainty, m. |
| `wind_speed_karin`, `wind_speed_karin_2` | The two KaRIn wind speeds, m s-1. |
| `wind_speed_karin_qual`, `wind_speed_karin_2_qual` | Quality flags of the two wind speeds, original integers. |
| `dynamic_ice_flag`, `rain_flag` | Original integer flags. |

SWOT has no wind direction field, so none is written.

CCI altimeter fields (a value is `null` if the file has none for that point; the row index
is `ref_row_0based` and `ref_cell_0based` is always 0, since a file has one point per row):

| Field | Meaning |
| --- | --- |
| `swh` | Significant wave height, m. |
| `swh_adjusted` | Wave height after the project's adjustment, m. |
| `swh_denoised` | Wave height after denoising, m. |
| `swh_uncertainty` | Wave height uncertainty, m. |
| `swh_quality_level` | 0 undefined, 1 bad, 2 acceptable, 3 good. Original integer. |
| `swh_rejection_flags` | Bit mask: 1 too few valid values, 2 validity, 4 sea ice, 8 rms outlier, 16 outlier test. |
| `sea_ice_fraction` | Fraction of sea ice, 1. |

### Matching rule

```
imagette_start - tolerance < observation_time < imagette_end + tolerance
```

The observation must also lie strictly inside the footprint of the same imagette.
The dateline cut is not a footprint boundary: a cell at 179.9° and a cell at -179.9°
both match a footprint that straddles it. Quality and ice flags are recorded, never
used to filter. What counts as a measured cell depends on the reference: every finite
wind vector for KNMI, a finite wave height or a finite wind speed for SWOT, and a finite
value of `swh`, `swh_adjusted` or `swh_denoised` for the CCI altimeters.

A SWOT file holds lines across the swath, each with one time shared by its pixels. The
time test is made on each line's time, and the footprint test on each pixel's centre.
A cell matches only if both pass.

## 9. Assets

| Key | Points to | Role | Type | Checksum |
| --- | --- | --- | --- | --- |
| `sar` | The imagette NetCDF in the SAR archive | `data` | `application/x-netcdf` | none |
| `sar_manifest` | The SAFE `manifest.safe` | `metadata` | `application/xml` | `sarmatchup:sha256` |
| `reference_001`, `reference_002`, ... | Each contributing reference granule | `data` | `application/x-netcdf`, or `application/gzip` for `.nc.gz` | `sarmatchup:sha256` |

Every `href` is an absolute `file:` URI. The reference `href` starts with the recipe's
`reference_root`, so it names the real file only if `reference_root` is the real path.
In a container, bind the KNMI tree at its real path and use that path as
`reference_root`. The tool copies no data into the output folder.

## 10. Run files

- `diagnostics.json`: for each imagette and reference granule, the number of
  candidate and retained cells.
- `sar_metadata.json`: the SAFEs and imagettes read from the archive, with the
  identity of each input file.
- `manifest.json`: the resolved recipe, the checksum and size of every input,
  `reference_product` (the label in the file names), `reference_versions` (each version used in the run), `reference_cells` (cells read from the reference files that carry a measurement), counts, the matching rules,
  known limitations, and the checksum of every output file. Reference inputs carry
  the `source`, `version` and `resolution` read from the file, not the requested ones.

## 11. Validation status

`stac-check` 1.14.0 passes every Item against the STAC 1.0.0 Item schema. That check
covers the core fields only. The `sarmatchup:*` properties, the granule groups and the
points are not described by any schema, so no tool checks their content.

Messages from `stac-check` on a normal Item:

- File name differs from `id`. The name carries the SAFE, imagette and reference
  product, which the hash does not.
- `datetime` is `null`. The interval is in `start_datetime` and `end_datetime`, as
  STAC allows.
- "Coordinates may be in the wrong order". This is a heuristic that fires for any
  position with |longitude| > 90 and |longitude| > 2 × |latitude|. The order is
  `[longitude, latitude]`.

## 12. CCI altimeter reference

The reference is the ESA CCI Sea State L2P altimeter wave height (`ESACCI-SEASTATE-L2P-SWH`), along-track
points at about 1 per second (about 6 km apart). Recipe:

```toml
instrument = "alti"
platform = "jason-3"
resolution = "l2p"
reference_root = "<folder holding data/satellite/altimeter/l2p>"
start_date = "2020-01-01"
stop_date = "2020-01-02"
time_tolerance_minutes = 30
sar_missions = ["S1A", "S1B"]
```

- **Missions.** `cryosat-2`, `jason-2`, `jason-3`, `saral`, `sentinel-3a`, `sentinel-3b` and
  `sentinel-6a`. The recipe names `sentinel-3a`, `sentinel-3b` and `sentinel-6a` differ from the
  public folders (`sentinel-3_a`, `sentinel-3_b`, `sentinel-6`); the adapter holds the table.
  Other missions are not supported.
- **Files.** The files sit under `reference_root/data/satellite/altimeter/l2p/<mission>/<year>/<day of year>/`.
  A file belongs to the day it starts on. The tool reads the files of the requested days
  for the one mission of the recipe. This is the layout of the public v4 server
  (`https://data-cersat.ifremer.fr/data/ocean-waves/cci-seastate/v4/`, no login). The copy
  on the HPC is not checked.
- **Checks on reading.** A file must have the expected variables, units, the same platform as
  its name and the recipe, level L2P, and a `time_coverage_start` equal to the start in its
  name. A file with no point carrying a wave height is rejected. The recipe's source is
  `<platform> altimeter`.
- **Points.** Each row of the file is one point. The tool drops points with no position,
  time or wave height, and keeps the rest with their quality level and rejection flags.
  Nothing is filtered on quality: the old tool kept `swh_quality_level` 3 only, and about
  16 to 21% of the points of the two files checked are level 1.
- **Space and time.** A point matches if its position lies in the imagette footprint and its
  time within the window. The old tool used one cell centre with a radius and kept only the
  point closest in time; a track holds about 4 points in a footprint. The window of 30
  minutes is a pick to move forward, not a calibrated value.
- **Gaps.** A file can hold a jump between two points of thousands of km. These points sit
  outside any footprint, so they are never matched.
- **Not covered.** CMEMS wave L3 files, the HPC version of the CCI files, and any statistic
  per imagette. The wave height statistics are computed downstream from the points.

Tested: 15 tests on synthetic files, and a run on one real Jason-3 and one real Sentinel-3A
track (5 of 5 and 3 of 3 points found, as an independent count expected), with a synthetic
Sentinel-1 SAFE. Not tested with a real Sentinel-1 and altimeter pair, because the public
files end in 2023 (2024 for Jason-3 and SARAL) and the local SAFEs are later.
