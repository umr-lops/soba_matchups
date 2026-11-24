# SAR matchups design document

This document specifies the matchup collections and what `soba_matchups run` writes:
STAC 1.0.0 Items and three run files.


## 1. Product types and collections

The tool reads **Sentinel-1 WV Level-2 OCN products only (No support for SLC)**. Discovery rejects any other
product type.

You can also use the **sar_parquet_root** flag in recipes to specify the path for the daily parquets if you want to use only the SAR metadata for matchups.

One collection per product type:

| Proposed collection id | Product | Status |
| --- | --- | --- |
| `sar-matchups-wv-ocn` | Sentinel-1 WV L2 OCN | Items implemented; no collection document |
| `sar-matchups-wv-slc` | Sentinel-1 WV L1 SLC | not implemented |


- One run reads one product type. A run or a collection never mixes types: the OCN
  Item and the SLC Item of one imagette have the same footprint and time, so they
  match the same reference points, and every association would appear twice.
- The product type is visible in the SAFE name (`OCN__2S`, `SLC__1S`) and in
  `sarmatchup:sar_product_type`. The file naming in section 3 needs no change.
- A recipe key `product_type` (default `OCN`) would select the collection. Not
  implemented.


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
| `alti` / `cci` / `l2p` | `ESACCI_SEASTATE_L2P_V<release>`, for example `ESACCI_SEASTATE_L2P_V5` (all CCI altimeter missions of the root) |
| `alti` / `cmems` / `l3` | `CMEMS_WAV_L3_ALTI_VAVH` (all CMEMS altimeter missions of the root; no release in the label) |
| `buoy` / `ndbc-tao-cmems` / `point` | `INSITU_NDBC_TAO_CMEMS_WIND` (NDBC buoys, TAO moorings and the CMEMS in situ buoys in neither) |

The same imagette matched to SWOT is named
`S1D_WV_OCN__2SSV_20260622T213558_20260622T214335_003354_005E47_1F38__IMG010__PODAAC_SWOT_KARIN_L2_WINDWAVE_D0.json`.

The processing version of the reference files is not part of the name. The Item
records it per granule (`reference_version`).

The SAFE name already holds the product type (`OCN__2S`, `SLC__1S`), so the same
convention applies to every collection.

## 4. Reference points per SAR image

One Item per imagette holds every reference observation that passes the matching
rule in section 8: a measured cell, inside the footprint, within the time tolerance. Nothing is dropped for quality or ice flags, and nothing is reduced to a
single best point, except for the altimeter fallback of section 8.

- Points are grouped by reference granule in `sarmatchup:matchups` (section 7), with
  one group and one asset per granule.
- `sarmatchup:n_coloc_points` is the total. `sarmatchup:delta_diff_time_minutes_min`
  and `_max` summarise the signed time differences.
- An imagette without a point has no Item.
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
| `sarmatchup:other_instrument` | Reference source, for example `HY-2B HSCAT` or `SWOT KaRIn`. For the CCI and CMEMS altimeters, the missions of the Item joined by ` + `, for example `Jason-3 altimeter + Sentinel-3 A altimeter`. For buoys, the sources of the Item joined the same way, among `NDBC buoy`, `TAO mooring` and `CMEMS in situ`. |
| `sarmatchup:other_type` | `scatterometer` for ASCAT and HSCAT, `altimeter` for SWOT KaRIn and the CCI and CMEMS altimeters, `in_situ` for buoys. |
| `sarmatchup:other_geometry_type` | `MultiPoint`: the reference observations are points, given by `ref_lon` and `ref_lat`, not a swath outline. |
| `sarmatchup:searching_window_hours` | Time tolerance in hours, applied on each side of the acquisition interval. |
| `sarmatchup:lib_version` | `soba_matchups` version. |
| `sarmatchup:n_coloc_points` | Number of retained associations across all granule groups. |
| `sarmatchup:delta_diff_time_minutes_min`, `_max` | Smallest and largest signed point time difference in this Item. |
| `sarmatchup:imagette_index` | Imagette number from the measurement file name. |
| `sarmatchup:measurement_name` | Name of the imagette NetCDF file. |
| `sarmatchup:polarization` | `VV` or `HH`. |
| `sarmatchup:matchups` | Array of granule groups, section 7. |

`sarmatchup:n_coloc_points` counts associations, not distinct coordinates. Two
observations at one location stay two associations. Repeated identical associations
are discarded by their `record_id`.

## 7. Granule groups

Each object in `sarmatchup:matchups` describes one reference granule.

| Field | Meaning |
| --- | --- |
| `reference_asset` | Key of this granule in `assets`. |
| `reference_file` | Original granule file name. |
| `reference_source` | Validated `source` attribute (KNMI); `SWOT KaRIn` for SWOT; `<mission> altimeter` for CCI and CMEMS, one per reference file; the mission is the file's `platform` attribute, for example `Sentinel-3 A altimeter` (CCI) or `HaiYang-2B altimeter` (CMEMS). Buoys: `NDBC buoy`, `TAO mooring` or `CMEMS in situ`. |
| `reference_version` | KNMI: `software_identification_wind` attribute, as a string. SWOT: processing level and version from the file name, for example `PID0_01`. CCI: dataset release from the file's `references` attribute and the `fv` number of the name, for example `v4.0_fv01`. CMEMS: product release from the file's `product_version` attribute and the generation stamp of the name, for example `v6.0_g20241221T050553`. Buoys: the program that wrote the file (`get_ncei_data 0.1.0`, `get_tao_data 0.1.0`), or for CMEMS in situ the channel and format, for example `013_030 history format 2.0`. |
| `reference_resolution` | KNMI: `pixel_size_on_horizontal` attribute, for example `12.5 km`. SWOT: the recipe's resolution, `2 km`, since the file has no such attribute. CCI and CMEMS: `1 Hz`, the sampling of the points. Buoys: `point`. |
| `reference_platform` | Buoys only: the station, site or platform code, for example `41046`, `T0N140W`, `6200001`. |
| `reference_platform_type` | Buoys only: the CMEMS platform type code, `MO` (moored) for NDBC and TAO, and for CMEMS that of the file name, `MO` or `DB` (drifting). |
| `points` | Matched observations, section 8. |

For buoys a group is one station-year file (NDBC, TAO) or one CMEMS netCDF file, so one platform
can have two groups in an Item: a CMEMS history file and its monthly file, for example.

## 8. Points

### Common fields

| Field | Meaning |
| --- | --- |
| `record_id` | SHA-256 of the imagette id, the reference file checksum and the original flat cell index. |
| `ref_row_0based` | Row index in the reference NetCDF. |
| `ref_cell_0based` | Cross-track cell index in the reference NetCDF. |
| `ref_time_utc` | Observation time, UTC. |
| `ref_lon` | Longitude in degrees east, in [-180, 180). |
| `ref_lat` | Latitude in degrees north. |
| `delta_diff_time_minutes` | Observation time minus the imagette midpoint, in minutes. Positive means the observation is later. |
| `sar_distance_to_coast` | Distance in km from the point to the nearest ocean coast, read from the NASA GSFC grid named by the recipe key `distance_to_coast_file`. Negative over land. Null when the key is not set or the grid cannot be read; the run goes on and the manifest says why. |
| `sar_incidence_angle` | Incidence angle of the SAR acquisition at the point, in degrees from the local vertical. Computed by the `s1-geometry`. |
| `sar_elevation_angle` | Off-nadir angle measured at the satellite, in degrees (the ESA OCN convention), from `s1-geometry`. It is not an angle at the point, and it is not 90 degrees minus the incidence. |
| `sar_ground_heading` | Heading of the SAR track at the point, in degrees clockwise from north, from `s1-geometry`. |
| `sar_ground_heading_uncertainty` | Uncertainty of the heading in degrees: the p95 of the `s1-geometry` WV heading error, 0.4. |
| `sar_wv_subswath` | WV subswath of the imagette the model placed the point on, 1 or 2. It agrees with the `wv1` or `wv2` in the measurement file name; a point where it or the imagette number disagrees gets null angles. |

The SAR angle fields (incidence, elevation, heading, heading uncertainty, subswath) are null for a point outside the product footprint, which the library does not extrapolate to (for example an altimeter point kept by the 30 km fallback of section 8), and for every point of a product the library cannot handle. The run goes on in both cases; the products it could not handle are listed in `manifest.sar_angles.errors`.

### KNMI fields (ASCAT, HSCAT)

| Field | Meaning |
| --- | --- |
| `wind_speed` | m s-1 |
| `wind_direction_to` | Degrees, direction the wind blows toward. |
| `quality_flag` | Original numeric flag, `null` if not finite. |
| `ice_probability` | Original `ice_prob`, `null` if not finite. No unit conversion. |

### SWOT fields

| Field | Meaning |
| --- | --- |
| `swh_karin` | Significant wave height, m. |
| `swh_karin_qual` | Quality flag of the wave height, original integer. |
| `swh_karin_uncert` | Wave height uncertainty, m. |
| `wind_speed_karin`, `wind_speed_karin_2` | The two KaRIn wind speeds, m s-1. |
| `wind_speed_karin_qual`, `wind_speed_karin_2_qual` | Quality flags of the two wind speeds, original integers. |
| `dynamic_ice_flag`, `rain_flag` | Original integer flags. |

### ALTI CCI fields

| Field | Meaning |
| --- | --- |
| `swh` | Significant wave height from the altimeter retracker, without cross-mission bias correction, m. |
| `swh_adjusted` | Significant wave height, bias corrected, m. |
| `swh_denoised` | Significant wave height, bias corrected and denoised (EMD denoising by Quilfen et al.), m. |
| `swh_uncertainty` | Theoretical uncertainty from speckle noise and sampling in the 1 Hz average, m. |
| `swh_quality_level` | Quality of the wave height: 0 undefined, 1 bad, 2 acceptable, 3 good. Original integer. |
| `swh_rejection_flags` | Flags raised when the quality level was lowered, original integer bit mask: 1 too few valid values, 2 validity, 4 sea ice, 8 rms outlier, 16 outlier test. |
| `sea_ice_fraction` | Fraction of sea ice in the water, 1. |

### ALTI CMEMS fields

| Field | Meaning |
| --- | --- |
| `VAVH` | Significant wave height, bias corrected and filtered, calibrated against buoys (Sepulveda et al., 2015), m. |
| `VAVH_UNFILTERED` | Significant wave height, bias corrected, calibrated against buoys (Sepulveda et al., 2015), m. |
| `WIND_SPEED` | Equivalent 10 m wind speed derived from the altimeter, bias corrected relative to a reference mission, m s-1. |

### Buoy fields (NDBC, TAO, CMEMS in situ)

Every buoy point has every field below. A field a source has no equivalent for is `null`. Values
are copied as the source gives them: nothing is converted to 10 m and nothing is filtered.

| Field | Meaning | NDBC (NCEI) | TAO | CMEMS in situ |
| --- | --- | --- | --- | --- |
| `wind_speed` | Wind speed at the anemometer height, m s-1. | `wind_speed`, as NDBC published it | `wind_speed` (delayed mode where it exists) | `WSPD` at the first depth level holding one, else `hypot(WSPE, WSPN)` |
| `wind_direction_from` | Direction the wind blows from, degrees clockwise from north. | `wind_direction` | `wind_direction` | `WDIR`, else `WTODIR` + 180, else from `WSPE`/`WSPN` |
| `anemometer_height` | m above the surface. | `anemometer_height` | `anemometer_height` | `-DEPH` of the level used; `null` for 0 (height lost) or beyond 100 m |
| `wind_speed_qc`, `wind_direction_qc` | Original integer QC flag; the scale is the source's. | QARTOD: 1 pass, 2 not evaluated, 3 suspect, 4 fail, 9 missing | 0 unknown, 1 good, 2 probably good, 3 questionable, 4 bad, 5 adjusted | OceanSITES/CMEMS: 1 good ... 9 missing |
| `wind_speed_qc_detail`, `wind_direction_qc_detail` | NDBC test that fired: `"0"` none, else its letter. | ✓ | `null` | `null` |
| `wind_speed_released`, `wind_direction_released` | `true` when NDBC released the value, `false` when withheld. | ✓ | `null` | `null` |
| `wind_speed_mode`, `wind_direction_mode` | Data mode. | `null` | `D` delayed, `R` real time | the file's data mode: `R`, `D` or `M` |
| `wind_speed_withdrawn`, `wind_direction_withdrawn` | `true` for a real-time value the delayed-mode file no longer holds. | `null` | ✓ | `null` |
| `wind_speed_bias_suspect` | `true` for real-time speeds of a 2020–2025 deployment (NDBC advisory DM 09.26-11). | `null` | ✓ | `null` |
| `position_source` | How the position was given. | `gps`, `nominal` or `nominal_whole_degree` | `gps` or `nominal` | `precise` (the record's own fix), `record` (its own position) or `file` (the file's single position) |
| `position_qc` | Position QC flag. | `gps_qc` | `null` | `POSITION_QC` |
| `position_offset_km` | Distance from the nominal mooring position, km. | ✓ | ✓ | `null` |

Which buoys are read:

- **NDBC:** the station-years of `get_ncei_data` under `<reference_root>/ncei/ncei_concat/wind`.
  Only buoys: NDBC numbers buoys with digits (`41046`). Land stations, named with letters
  (`bura1`), are left out and listed in `manifest.reference_selection`.
- **TAO:** the site-years of `get_tao_data` under `<reference_root>/tao/tao_concat`.
- **CMEMS in situ:** files of `INSITU_GLO_PHYBGCWAV_DISCRETE_MYNRT_013_030` under the recipe's
  `cmems_insitu_root` whose index row lists `WSPD` or both `WSPE` and `WSPN`, chosen from the
  dataset's `index_{history,monthly,latest}.txt`. Only buoys: the `MO` and `DB` types, and of
  those only platforms whose file states `source` = `moored surface buoy` or
  `drifting surface float`. `MO` also holds oil platforms, coastal and land masts, lightships
  and generic `mooring`s (mostly coastal stations), and `DB` ice buoys and `unknown` drifters;
  all of these are left out, as are ships (`FB`, `TS`), saildrones (`SD`) and tide gauges (`TG`). A platform is left out when its code is an NDBC station id (case-insensitive), a
  TAO site code, or a TAO or NDBC WMO number, also in its seven-digit CMEMS form (`5200013` is
  WMO `52013`). A monthly file is read only for records after the platform's history file ends,
  a latest file only after both, so no record is read twice. A platform distributed by two
  regions is read once, from `GL`.

### Matching rule

```
imagette_start - tolerance < observation_time < imagette_end + tolerance
```

For every reference, an observation matches when it is inside this time window and strictly
inside the footprint of the same imagette. The dateline is not a footprint boundary: a cell at
179.9° and a cell at -179.9° both match a footprint that straddles it. Quality and ice flags are
recorded, never used to filter. Where a reference differs:

- **SWOT:** the time test is made on each line's time (shared by the line's pixels) and the
  footprint test on each pixel's centre.
- **ALTI (CCI and CMEMS):** all points inside the footprint are kept. If none is inside, the
  tool keeps one point: among those within 30 km of the imagette's OSW cell centre and inside
  the time window, the one closest in time to the imagette midpoint, over all missions and files.
- **Buoys:** every record of a buoy inside the footprint and the time window is kept, so a buoy
  reporting every 10 minutes gives about six points per imagette at a 30 minute tolerance.
  Choosing the record nearest the imagette midpoint is a downstream step. `start_date` and
  `stop_date` select the days the records fall in, not the days the files start on.

## 9. Assets

| Key | Points to | Role | Type | Checksum |
| --- | --- | --- | --- | --- |
| `sar` | The imagette NetCDF in the SAR archive | `data` | `application/x-netcdf` | none |
| `sar_manifest` | The SAFE `manifest.safe` | `metadata` | `application/xml` | `sarmatchup:sha256` |
| `reference_001`, `reference_002`, ... | Each contributing reference granule | `data` | `application/x-netcdf`, `application/gzip` for `.nc.gz`, or `application/vnd.apache.parquet` for the buoy station-years | `sarmatchup:sha256` |

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
  `distance_to_coast` (status, identity of the grid file, error text if any),
  `sar_angles` (the `s1-geometry` version used and the products it could not handle),
  known limitations, and the checksum of every output file. Reference inputs carry
  the `source`, `version` and `resolution` read from the file, not the requested ones.
  Buoy runs add `reference_selection`: the three source folders, the number of files read from
  each, the NDBC land stations left out, the number of CMEMS platforms of each non-buoy type
  (`cmems_types_left_out`), the `MO`/`DB` platforms that are not buoys with their `source`
  (`cmems_not_buoys_left_out`), the CMEMS platforms already read elsewhere with the reason
  (`NDBC station in NCEI`, `TAO site`, `TAO site T0N140W (WMO number)`, ...), the CMEMS
  platforms read from another region, and the files the CMEMS index lists but the disk lacks.
  Files with no wind record in the requested days are in `reference_files_skipped`.
  `schema_version` is 15: point values may be text or true/false, and granule groups may carry
  fields of their own (`reference_platform`).

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
