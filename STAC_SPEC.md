# soba_matchups output specification

This file specifies what `soba_matchups run` writes: STAC 1.0.0 Items and three
run files. The run manifest layout is `schema_version` 9.

The output is a matchup index. Each Item links one Sentinel-1 WV OCN imagette to
the KNMI L2B scatterometer (ASCAT or HSCAT) observations that fall inside it. It is
not an enriched co-aligned Parquet or NetCDF product.

## 1. Run folder

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

## 2. File naming

```
<SAFE name without .SAFE>__IMG<NNN>__<INSTRUMENT>_<RESOLUTION>.json
```

Example (SAFE `S1B_WV_OCN__2SSV_20200101T011231_20200101T012925_019616_02512F_8F63`,
imagette 4, HY-2B HSCAT at 25 km):

```
S1B_WV_OCN__2SSV_20200101T011231_20200101T012925_019616_02512F_8F63__IMG004__HY2B_HSCAT_25KM.json
```

| Part | Meaning |
| --- | --- |
| SAFE name | Parent product, as named in the archive. |
| `IMG<NNN>` | Imagette number from the measurement file name, padded to three digits. |
| `INSTRUMENT` | Validated `source` attribute of the reference file, upper case, hyphens removed, spaces replaced by `_`. |
| `RESOLUTION` | Pixel size of the reference product in km, with the decimal point written as `P`. |

Instrument and resolution values by recipe:

| `scat` / `met` / `resolution` | Suffix |
| --- | --- |
| `hscat` / `hy-2b` / `25km` | `HY2B_HSCAT_25KM` |
| `hscat` / `hy-2c` / `25km` | `HY2C_HSCAT_25KM` |
| `hscat` / `hy-2d` / `25km` | `HY2D_HSCAT_25KM` |
| `ascat` / `metop-b` / `25km` | `METOPB_ASCAT_25KM` |
| `ascat` / `metop-b` / `12.5km` | `METOPB_ASCAT_12P5KM` |
| `ascat` / `metop-b` / `5.7km` | `METOPB_ASCAT_5P7KM` |
| `ascat` / `metop-c` / `12.5km` | `METOPC_ASCAT_12P5KM` |

The processing version of the reference files is not part of the name. The Item
records it per granule (`reference_version`). The name does not encode the time
tolerance either, so use a fresh output folder for each run. A duplicate name inside
one run stops publication.

The convention is implemented in `soba_matchups.output.item_filename`.

## 3. Item

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
| `assets` | Source files, see section 6. |
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
| `sarmatchup:other_instrument` | Validated reference source, for example `HY-2B HSCAT`. |
| `sarmatchup:other_type` | `scatterometer` |
| `sarmatchup:other_geometry_type` | `MultiPoint`: the reference observations are points, given by `ref_lon` and `ref_lat`, not a swath. |
| `sarmatchup:searching_window_hours` | Time tolerance in hours, applied on each side of the acquisition interval. |
| `sarmatchup:lib_version` | `soba_matchups` version. |
| `sarmatchup:n_coloc_points` | Number of retained associations across all granule groups. |
| `sarmatchup:delta_diff_time_minutes_min`, `_max` | Smallest and largest signed point time difference in this Item. |
| `sarmatchup:imagette_index` | Imagette number from the measurement file name. |
| `sarmatchup:measurement_name` | Name of the imagette NetCDF file. |
| `sarmatchup:polarization` | `VV` or `HH`. |
| `sarmatchup:mapping_policy` | `local_ocn_geolocation_to_manifest_frame`: how the imagette footprint was chosen. |
| `sarmatchup:matchups` | Array of granule groups, section 4. |

`sarmatchup:n_coloc_points` counts associations, not distinct coordinates. Two
observations at one location stay two associations. Repeated identical associations
are discarded by their `record_id`.

Not in the Item: the sea percentage of the footprint. It belongs to a later
ancillary step, not to the matchup step.

## 4. Granule groups

Each object in `sarmatchup:matchups` describes one reference granule.

| Field | Meaning |
| --- | --- |
| `reference_asset` | Key of this granule in `assets`. |
| `reference_file` | Original granule file name. |
| `reference_source` | Validated `source` attribute. |
| `reference_version` | `software_identification_wind` attribute, as a string. |
| `reference_resolution` | `pixel_size_on_horizontal` attribute, for example `12.5 km`. |
| `sarmatchup:other_id` | Same value as `reference_file`, scoped to this group. |
| `points` | Matched observations, section 5. |

## 5. Points

| Field | Meaning |
| --- | --- |
| `record_id` | SHA-256 of the imagette id, the reference file checksum and the original flat cell index. |
| `ref_row_0based` | Row index in the reference NetCDF. |
| `ref_cell_0based` | Cross-track cell index in the reference NetCDF. |
| `ref_time_utc` | Observation time, UTC. |
| `ref_lon` | Longitude in degrees east, in [-180, 180). |
| `ref_lat` | Latitude in degrees north. |
| `wind_speed` | m s-1 |
| `wind_direction_to` | Degrees, direction the wind blows toward. |
| `quality_flag` | Original numeric flag, `null` if not finite. |
| `ice_probability` | Original `ice_prob`, `null` if not finite. No unit conversion. |
| `delta_diff_time_minutes` | Observation time minus the imagette midpoint, in minutes. Positive means the observation is later. |

### Matching rule

```
imagette_start - tolerance < observation_time < imagette_end + tolerance
```

The observation must also lie strictly inside the footprint of the same imagette.
The dateline cut is not a footprint boundary: a cell at 179.9° and a cell at -179.9°
both match a footprint that straddles it. Every finite wind vector is kept. Quality
and ice flags are recorded, never used to filter.

## 6. Assets

| Key | Points to | Role | Type | Checksum |
| --- | --- | --- | --- | --- |
| `sar` | The imagette NetCDF in the SAR archive | `data` | `application/x-netcdf` | none |
| `sar_manifest` | The SAFE `manifest.safe` | `metadata` | `application/xml` | `sarmatchup:sha256` |
| `reference_001`, `reference_002`, ... | Each contributing reference granule | `data` | `application/x-netcdf`, or `application/gzip` for `.nc.gz` | `sarmatchup:sha256` |

Every `href` is an absolute `file:` URI. The reference `href` starts with the recipe's
`reference_root`, so it names the real file only if `reference_root` is the real path.
In a container, bind the KNMI tree at its real path and use that path as
`reference_root`. The tool copies no data into the output folder.

## 7. Run files

- `diagnostics.json`: for each imagette and reference granule, the number of
  candidate and retained cells.
- `sar_metadata.json`: the SAFEs and imagettes read from the archive, with the
  identity of each input file.
- `manifest.json`: the resolved recipe, the checksum and size of every input,
  `reference_versions` (each version used in the run), counts, the matching rules,
  known limitations, and the checksum of every output file. Reference inputs carry
  the `source`, `version` and `resolution` read from the file, not the requested ones.

## 8. Validation status

`stac-check` 1.14.0 passes every Item against the STAC 1.0.0 Item schema. That check
covers the core fields only. The `sarmatchup:*` properties, the granule groups and the
points are not described by any schema, so no tool checks their content.

Messages from `stac-check` on a normal Item:

- File name differs from `id`. The name carries the SAFE, imagette, instrument and
  resolution, which the hash does not.
- `datetime` is `null`. The interval is in `start_datetime` and `end_datetime`, as
  STAC allows.
- "Coordinates may be in the wrong order". This is a heuristic that fires for any
  position with |longitude| > 90 and |longitude| > 2 × |latitude|. The order is
  `[longitude, latitude]`.
