# SAR matchups design document

This document specifies the matchup collections and what `soba_matchups run` writes:
STAC 1.0.0 Items and three run files.


## 1. Product types and collections

The tool reads **Sentinel-1 Level-2 OCN products only (No support for SLC or GRD)**, in one of
four acquisition modes chosen by the recipe key `sar_mode`: `WV` (the default), `IW`, `EW` or `SM`
(StripMap, whose SAFE names carry the beam, `S1_` to `S6_`, instead of the mode).
Discovery rejects any other product type, and any product of another mode.

You can also use the **sar_parquet_root** flag in recipes to specify the path for the daily parquets if you want to use only the SAR metadata for matchups.
IW, EW and SM **require** it: everything the tool knows about such a product comes from its row
in the CDSE daily parquet files (name, `ContentDate`, `GeoFootprint`) and from `s1-geometry`.
The archive, when `sar_archive_root` is also set, is only asked which measurement file the SAFE
holds; no SAR file is opened. Its layout is `sar_archive_layout`: `mpc` (default), the MPC
Sentinel-1 cache, `sentinel-1x/L2/<mode>/<S1x>_<mode>_OCN__2S/<year>/<doy>/<SAFE>`, or
`provider`, the CERSAT provider tree of the ESA L2 products
(`/legacy/project/cersat/public/provider/esa/satellite/l2`), which holds every IW and EW OCN
product: `sentinel-1x/c-band_sar/s1x_<iw|ew>_ocn__2s/<year>/<doy>/<SAFE>` (IW and EW runs only).
Recent IW SAFEs hold one OSW file per subswath (`s1c-iw1-osw-...`, `iw2`, `iw3`) next to the
scene's `s1c-iw-ocn-...` file: the scene's file is the one `-ocn-` file.

One collection per product type and mode:

| Proposed collection id | Product | Matched unit | Status |
| --- | --- | --- | --- |
| `sar-matchups-wv-ocn` | Sentinel-1 WV L2 OCN | imagette | Items implemented; no collection document |
| `sar-matchups-iw-ocn` | Sentinel-1 IW L2 OCN | scene (one SAFE) | Items implemented; no collection document |
| `sar-matchups-ew-ocn` | Sentinel-1 EW L2 OCN | scene (one SAFE) | Items implemented; no collection document |
| `sar-matchups-sm-ocn` | Sentinel-1 SM L2 OCN | scene (one SAFE) | Items implemented; no collection document |
| `sar-matchups-wv-slc` | Sentinel-1 WV L1 SLC | imagette | not implemented |

An IW, EW or SM OCN product holds one measurement file for the whole scene, so the scene plays
the part of the imagette: one Item per matched SAFE, with no `imagette_index`. A run reads one
mode, so a run or a collection never mixes WV imagettes with scenes, or scenes of two modes.

Every mode reads the SAR metadata from `daily_parquet_files_v2`. That folder **does not list SM
products yet**: its producer keeps the modes `S1` to `S6` but tests the `operationalMode`
attribute, which says `SM`, so every SM product is dropped. Until the producer is fixed, an SM
run finds no product; it says so in `discovery.warning` and in the log, so that an empty result
is not taken for a period without SM acquisitions. The SM path was tested on the older
`daily_parquet_files` folder, which lists SM products in the same row layout.


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

The tool writes only `output_dir`; where that folder sits is the caller's choice. The production
runs (Airflow DAGs) use one tree for every reference, one run folder per reference collection,
day, SAR product type and SAR mode:

```
<root>/<reference>/<collection>/<YYYY-MM-DD>/<product_type>/<mode>/   = output_dir
    swot/d0/2025-02-03/ocn/wv/      SWOT KaRIn release D0 (PGD0 + PID0), WV L2 OCN
    swot/d2/2026-11-20/ocn/iw/      SWOT KaRIn release D2 (PGD2 + PID2), IW L2 OCN
    scat/hscat/2025-02-03/grd/iw/   HSCAT, IW L1 GRD
    alti/cci/2025-02-03/ocn/wv/     CCI altimeters, WV L2 OCN
```

`<collection>` is the SWOT release (`d0`, `d2`, recipe `swot_release` in lower case), the
scatterometer family (`hscat`, `ascat`) or the altimeter product (`cci`, `cmems`).
`<product_type>` and `<mode>` are the recipe `product_type` and `sar_mode` in lower case.

Outside Airflow, a per-day recipe writes the same tree: its `output_dir` holds `{day}`
(e.g. `<root>/swot/d2/{day}/ocn/iw`), `soba_matchups listing` writes one `run-day` line per day
whose folder does not exist yet, and each line runs one day (a Slurm job-array task, see
`scripts/slurm/`). A day without any reference file writes nothing and stays in the next listing.

## 3. File naming

```
<SAFE name without .SAFE>__IMG<NNN>__<PRODUCT>.json     WV: one Item per imagette
<SAFE name without .SAFE>__<PRODUCT>.json               IW, EW, SM: one Item per scene
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
| `karin` / `swot` / `2km` | `PODAAC_SWOT_KARIN_L2_WINDWAVE_<release>`, for example `PODAAC_SWOT_KARIN_L2_WINDWAVE_D2`: the release the run read (recipe key `swot_release`); the CRID (`PGD2`, `PID2`) and file version of each granule are in `reference_version` |
| `alti` / `cci` / `l2p` | `ESACCI_SEASTATE_L2P_V<release>`, for example `ESACCI_SEASTATE_L2P_V5` (all CCI altimeter missions of the root) |
| `alti` / `cmems` / `l3` | `CMEMS_WAV_L3_ALTI_VAVH` (all CMEMS altimeter missions of the root; no release in the label) |
| `buoy` / `ndbc-tao-cmems` / `point` | `INSITU_NDBC_TAO_CMEMS_WIND` (NDBC buoys, TAO moorings and the CMEMS in situ buoys in neither) |

An IW scene matched to HY-2B HSCAT is named
`S1A_IW_OCN__2SDH_20250415T070725_20250415T070754_058764_0747A0_F658__KNMI_HSCAT_HY2B_25KM.json`.

The same WV imagette matched to SWOT is named
`S1D_WV_OCN__2SSV_20260622T213558_20260622T214335_003354_005E47_1F38__IMG010__PODAAC_SWOT_KARIN_L2_WINDWAVE_PID0.json`.

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
| `id` | `local_` plus a SHA-256 of the SAFE name and the measurement file's path inside the SAFE. Deterministic and unique per imagette. Not a CDSE UUID, and not equal to the file name. For an IW, EW or SM scene, a SHA-256 of the SAFE name and `scene`, so the id does not depend on whether the archive was given. |
| `geometry` | Imagette footprint in WGS84, `Polygon` or `MultiPolygon`. Positions are `[longitude, latitude]`, the GeoJSON order that STAC requires. |
| `bbox` | `[west, south, east, north]`, the bounds of `geometry`. |
| `properties.datetime` | `null`. The interval is in `start_datetime` and `end_datetime`. |
| `properties.start_datetime`, `end_datetime` | Imagette acquisition interval, UTC, from the `firstMeasurementTime` and `lastMeasurementTime` attributes of the OCN file (in parquet mode, the imagette's `ContentDate` in `WVImagettes`). For an IW, EW or SM scene, the product's `ContentDate` in the daily parquet file. |
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
| `sarmatchup:sar_instrument_mode` | `WV`, `IW`, `EW` or `SM`, from the SAFE name (an `S1_` to `S6_` beam name is `SM`). |
| `sarmatchup:sar_product_type` | `OCN` |
| `sarmatchup:sar_safe` | Full parent SAFE name. |
| `sarmatchup:other_instrument` | Reference source, for example `HY-2B HSCAT` or `SWOT KaRIn`. For the CCI and CMEMS altimeters, the missions of the Item joined by ` + `, for example `Jason-3 altimeter + Sentinel-3 A altimeter`. For buoys, the sources of the Item joined the same way, among `NDBC buoy`, `TAO mooring` and `CMEMS in situ`. |
| `sarmatchup:other_type` | `scatterometer` for ASCAT and HSCAT, `altimeter` for SWOT KaRIn and the CCI and CMEMS altimeters, `in_situ` for buoys. |
| `sarmatchup:other_geometry_type` | `MultiPoint`: the reference observations are points, given by `ref_lon` and `ref_lat`, not a swath outline. |
| `sarmatchup:searching_window_hours` | Time tolerance in hours, applied on each side of the acquisition interval. |
| `sarmatchup:lib_version` | `soba_matchups` version. |
| `sarmatchup:n_coloc_points` | Number of retained associations across all granule groups. |
| `sarmatchup:delta_diff_time_minutes_min`, `_max` | Smallest and largest signed point time difference in this Item. |
| `sarmatchup:imagette_index` | Imagette number from the measurement file name. WV only: absent from an IW, EW or SM Item (a scene). |
| `sarmatchup:measurement_name` | Name of the imagette NetCDF file. For an IW, EW or SM scene, the `-ocn-` NetCDF file of the archive SAFE, or `null` when the archive was not given or does not hold it. |
| `sarmatchup:polarization` | The product's polarizations, from the SAFE name: `VV` or `HH` (single, `SV` / `SH`; every WV product), `VV+VH` or `HH+HV` (dual, `DV` / `DH`; most IW and EW products). An OCN product is processed on the co-polarization (the first one). |
| `sarmatchup:sar_orbit_direction` | IW, EW and SM only: the pass direction the SAR angles rest on, `ascending` or `descending` (`null` when there is none). Absent from WV Items. |
| `sarmatchup:sar_orbit_direction_source` | IW, EW and SM only: `slice motion` (checked by the datatake's other slices) or `catalogue` (the `orbitDirection` attribute alone, unchecked; see section 8). Absent from WV Items. |
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
| `sar_ground_heading_uncertainty` | Uncertainty of the heading in degrees. WV: the p95 of the `s1-geometry` WV heading error, 0.4. IW, EW, SM: the library's latitude- and hemisphere-dependent uncertainty of its IW heading model at the point. |
| `sar_wv_subswath` | WV subswath of the imagette the model placed the point on, 1 or 2. It agrees with the `wv1` or `wv2` in the measurement file name; a point where it or the imagette number disagrees gets null angles. WV only: absent from IW, EW and SM points, which the incidence and elevation angles place across the swath. |
| `sar_distance_to_along_track_edge` | IW, EW, SM only. Distance in m from the point to the nearer along-track edge of the scene footprint (the near or far range line), great circle on a sphere of radius 6371008.8 m, the footprint's edges straight between its four corners. `null` when the point has no SAR angles' geometry (outside the footprint). |
| `sar_distance_to_cross_track_edge` | IW, EW, SM only. Distance in m from the point to the nearer cross-track edge (the first or last line), computed the same way. |

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

### SAR angles of an IW, EW or SM scene

Everything comes from the daily parquet rows (`Name`, `GeoFootprint`, `Attributes`) and from
`s1-geometry`.

- **Pass direction (ascending or descending):** consecutive slices of one datatake (same
  mission, mode and datatake id in the SAFE name, ordered by start time) follow the flight, so
  the footprint moves north on an ascending pass and south on a descending one. Steps of
  0.05° of latitude or less are ignored. A product takes the direction of its remaining steps
  to the slices before and after it when they agree; one step is enough. A lone slice takes
  the catalogue's `orbitDirection`. A slice with neighbours whose steps disagree or are all
  below 0.05° is at the **turning latitude** and is rejected (see below).
- **Rejected scenes:** a scene with candidate cells whose along-track and cross-track edges
  cannot be told apart gives no Item, and is listed with its reason in the manifest's
  `scenes_rejected_geometry`: at the turning latitude, a footprint that is not four corners
  (for example clipped by the catalogue at 85.05° N), no pass direction (a lone slice with no
  catalogue direction), a lone slice whose footprint tilt contradicts the catalogue direction or
  is ambiguous, or a footprint `s1-geometry` reads in another direction than the pass once its
  corners are in the library's order (some SM scenes).
- **Footprint tilt (lone slices):** the range lines follow the ground track, whose bearing
  (`heading_from_lat` at the footprint's mean latitude) leans one way on an ascending pass and
  the other way on a descending one, about 24° apart at low latitude. The direction is the one
  whose bearing (modulo 180°) an edge follows best; when the two fits differ by less than 5° the
  tilt is ambiguous. Against the slice motion (S1A/S1C/S1D, 2025-04-10 → 17 and 2026-11-01 →
  08) it agreed on 99.6% of the unambiguous IW products (2% ambiguous) and 95% of EW ones (7%
  ambiguous). On the lone slices of those weeks it agreed with the catalogue on 264 of 264
  unambiguous IW ones, and disagreed on 17 of 123 EW ones: such checks reject about 0.1% of
  IW products and 1 – 2% of EW ones.
- **How much is rejected:** on those two weeks, every reason together rejected 0.7% of IW
  products (21 of 3,171 and 22 of 3,053) and 5.9% of EW ones (69 of 1,182, 62 of 1,048), nearly
  all poleward of 70°: mostly footprints `s1-geometry` reads in another direction (IW 0.5%,
  EW 3%), then lone slices (IW 0.1%, EW 1.6 – 2%) and the turning latitude (IW < 0.1%,
  EW 0.8 – 1.1%). Only scenes with candidate cells are tested in a run.
- **Slices published twice:** ESA publishes some slices twice (same start, two checksums). They
  count as one slice for the motion, at their mean latitude; otherwise a twin at the same
  latitude would leave the first or last slice of a datatake without a direction.
  - The manifest counts the first case (`discovery.orbit_direction_from_slices`), lists every
    product whose catalogue direction disagreed (`discovery.orbit_direction_corrected`) and every
    product whose direction is the catalogue's alone (`discovery.orbit_direction_from_catalogue`).
  - Each Item gives `sarmatchup:sar_orbit_direction` and `sarmatchup:sar_orbit_direction_source`;
    `sar_metadata.json` gives each product's `OrbitDirection` and `OrbitDirectionSource`.
  - A direction from the catalogue alone is checked against the footprint tilt (below): if it
    were wrong, near and far range would swap, incidence would be wrong by several degrees and
    the heading by about 160°, and the library would not notice, since the corners are ordered
    for that direction and it reads it back. The catalogue is least reliable on S1C EW.
  - The last slice of a datatake just past the turning latitude has only its previous step,
    which can be on the other side of the turn, so it can take the wrong direction.
  - Checked against the OCN files' `owiHeading`. On S1B IW/EW, January 2021, the slice motion
    was right on all 2,569 products that had one, and the catalogue was wrong on 14. On S1A/S1C,
    April 2025, the slice motion was wrong on 4 of 1,188, and the catalogue on 82: 73 of them S1C
    EW, about one in five.
- **Heading:** `heading_from_lat(lat, direction)` at each point, with its uncertainty
  `sar_ground_heading_uncertainty(lat)`. The library's heading depends only on latitude and pass
  direction.
- **Incidence and elevation:** `estimate_incidence_elevation(corners, lon, lat, mode)`, with for
  SM the beam of the SAFE name (`beam="S1"` to `"S6"`). The library reads the pass direction, and
  so which edges are the range lines, from the vertex order. So the corners are put in its
  standard order, built from their positions for the pass direction: last-near, first-near,
  first-far, last-far. The range lines are the pair of opposite edges most parallel to the
  flight. The flight's bearing (the library's heading at the scene's mean latitude) only has to
  be within 45°, so the roles hold near the turning latitude, where that heading is
  extrapolated. The radar looks right of the flight, so near range is the range line on the
  left.
  - The catalogue's own order cannot be used. On S1A/S1B it is one corner off for ascending
    passes, which the library misreads on about a quarter of ascending EW footprints, and S1C
    uses yet another order.
  - With the standard order the library read the right direction on every S1B IW and EW scene of
    January 2021, and on 72 of 80 SM scenes.
  - When it still reads another direction (some SM scenes), the scene is rejected.
  - The same happens when the swath width the library measured differs by more than 10% from the
    scene's own across-track width (near-range line to far-range line): it then measured across
    the wrong pair of edges. On the runs below this flagged 6 of about 1,870 products, 5 of
    which the direction rule already left without incidence.
- **Footprint:** the catalogue cuts footprints at the dateline (sometimes at 179.99999°) and adds
  two vertices where a scene crosses longitude 0. The pieces are joined in cyclic order and the
  added vertices dropped, so the library gets the four corners. In the Item geometry a cut at
  179.99999° is moved onto 180° exactly, so the matching joins the two pieces across the
  dateline and a point on the cut is not lost in the gap between them. A footprint that is not four
  corners is rejected.
- **Distances to the edges:** with the corners in that order, each point inside the footprint
  gets its great-circle distance in metres to the nearer range line
  (`sar_distance_to_along_track_edge`) and to the nearer of the first and last lines
  (`sar_distance_to_cross_track_edge`).
- All these cases are listed in `manifest.sar_angles.errors`.

Checked against the `owiIncidenceAngle` and `owiHeading` grids of the OCN files the archive
holds, at the cell nearest each point (within 2 km). This was a check only: the Items use the
model, never the files.
- **Runs:** IW and EW on 2025-04-15 (S1A + S1C) and 2021-01-01 to 07 (S1B); SM over January
  2021 (S1A + S1B, tested on the older `daily_parquet_files`, since `daily_parquet_files_v2` lists
  no SM product yet). All against HY-2B HSCAT.

| Mode | Where | Points | Incidence error, median / p95 / max | Heading error, median / p95 / max |
| --- | --- | --- | --- | --- |
| IW | latitude below 50° | 9,941–10,546 | 0.57–0.72 / 1.29–1.90 / 2.15° | 0.32–0.40 / 0.89–1.05 / 2.16° |
| IW | 50° and above | 6,725 | 0.54–0.65 / 1.51–1.67 / 4.07° | 1.00–1.23 / 2.20–3.14 / 6.69° |
| EW | latitude below 50° | 3,433 | 1.50 / 2.46 / 2.77° | 0.52 / 1.71 / 2.53° |
| EW | 50° and above | 19,740 | 2.00–2.12 / 4.69–5.20 / 9.27° | 2.38–2.95 / 6.03–6.63 / 21.41° |
| EW | across the dateline | 743 | 3.38 / 7.12 / 8.92° | 2.21 / 6.45 / 7.08° |
| SM | latitude below 50° | 735 | 0.74 / 0.87 / 0.98° | 0.26 / 1.05 / 14.27° |
| SM | 50° and above | 71 | 0.71 / 1.23 / 1.39° | 2.81 / 4.01 / 4.15° |

Ranges are over the two IW or EW runs.
- **Heading outliers:** the largest EW heading errors are near the pole, where the library's EW
  heading (a documented extrapolation of its IW model) is weakest. The SM maximum comes from cells
  of one OCN file whose `owiHeading` reads 0–10° while the rest of the scene reads 191°.

Coverage:

| Run | Points | With incidence | With heading | Directions from slices / corrected |
| --- | --- | --- | --- | --- |
| IW, 2025-04-15 | 8,632 | 8,597 (99.6%) | all | 395 of 418 products / 9 |
| IW, 2021-01-01 to 07 | 15,572 | 14,965 (96.1%) | all | 978 of 1,008 / 0 |
| EW, 2025-04-15 | 7,668 | 7,643 (99.7%) | all | 115 of 126 / 6 |
| EW, 2021-01-01 to 07 | 22,399 | 22,207 (99.1%) | all | 212 of 238 / 6 |
| SM, January 2021 | 901 | 846 (93.9%) | all | 12 of 135 / 0 |

The incidence is missing where the library could not invert the footprint (IW 2021: 17
products) or, for SM, still read another direction (5 products). The angles are recorded as the
model gives them, never clipped.

### Matching rule

```
imagette_start - tolerance < observation_time < imagette_end + tolerance
```

For every reference, an observation matches when it is inside this time window and strictly
inside the footprint of the same imagette. For IW, EW and SM the imagette is the scene: its footprint
and its `ContentDate` interval. The dateline is not a footprint boundary: a cell at
179.9° and a cell at -179.9° both match a footprint that straddles it. Quality and ice flags are
recorded, never used to filter. Where a reference differs:

- **SWOT:** the time test is made on each line's time (shared by the line's pixels) and the
  footprint test on each pixel's centre. A run reads one release, the recipe key
  `swot_release` (`D0` or `D2`), from the folders `PG<release>` (ground reprocessing) and
  `PI<release>` (forward processing) under `reference_root`, either of which may be missing:
  one file per cycle and pass, from `PG` when the pass is in both (same processor), then the
  highest version number. Releases are never mixed: AVISO moved the forward processing from
  PID0 to PID2 at cycle 059 pass 130 (2026-11-17), so a pass can be in D0 and in D2, and each
  release is matched by its own run into its own folder (section 2). The CRID and version of
  each granule are in `reference_version`, for example `PGD2_01` or `PID2_01`.
- **ALTI (CCI and CMEMS):** all points inside the footprint are kept. If none is inside, the
  tool keeps one point: among those within 30 km of the imagette's OSW cell centre (for an IW or
  EW scene, the centroid of its footprint) and inside
  the time window, the one closest in time to the imagette midpoint, over all missions and files.
- **Buoys:** every record of a buoy inside the footprint and the time window is kept, so a buoy
  reporting every 10 minutes gives about six points per imagette at a 30 minute tolerance.
  Choosing the record nearest the imagette midpoint is a downstream step. `start_date` and
  `stop_date` select the days the records fall in, not the days the files start on.

## 9. Assets

| Key | Points to | Role | Type | Checksum |
| --- | --- | --- | --- | --- |
| `sar` | The imagette NetCDF in the SAR archive. For an IW, EW or SM scene, its NetCDF when the archive holds it; otherwise the SAFE folder (no `type`), or the bare SAFE name when no archive was given | `data` | `application/x-netcdf` | none |
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
  `schema_version` is 19: IW, EW and SM Items no longer carry `sarmatchup:imagette_index` nor,
  on their points, `sar_wv_subswath` (WV only); their points give
  `sar_distance_to_along_track_edge` and `sar_distance_to_cross_track_edge` (m);
  `sarmatchup:polarization` is the product's own (`VV+VH` for an `SDV` product, was `VV`);
  scenes whose along-track and cross-track edges cannot be told apart (turning latitude, not
  four corners, no pass direction, footprint read in another direction) are rejected and listed
  in `scenes_rejected_geometry` with their reason; `sar_archive_layout` selects the provider
  tree, recorded in `discovery.sar_archive_layout`. Since 18, SWOT runs read one release (`recipe.swot_release`, D0 or D2, ground
  processing preferred over forward), which ends the SWOT label
  (`PODAAC_SWOT_KARIN_L2_WINDWAVE_D2`); schema 17 read one CRID (`swot_crid`, label `..._PID2`).
  Since 16, the manifest
  gives `sar_mode`, and IW, EW and SM runs write one Item per scene. Since 15, point values may be text or true/false, and granule groups may carry fields
  of their own (`reference_platform`). In IW, EW and SM runs, `discovery.imagettes_unavailable`
  lists scenes (one entry per SAFE whose measurement file the archive lacks) and
  `discovery.safes_skipped` the products whose footprint pieces do not form one polygon.
  They also give `discovery.orbit_direction_from_slices`, `discovery.orbit_direction_corrected`
  and `discovery.orbit_direction_from_catalogue` (the pass direction, section 8).

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
