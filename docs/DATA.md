# Data sources

Large data files stay out of Git. They belong in `data/`, which `.gitignore` excludes.

## Elevation map (required)

| | |
|---|---|
| **Name** | Mars 2020 Terrain Relative Navigation HiRISE DTM Mosaic |
| **Publisher** | U.S. Geological Survey, Astrogeology Science Center |
| **DOI** | [10.5066/P9REJ9JN](https://doi.org/10.5066/P9REJ9JN) |
| **File** | [JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif](https://planetarymaps.usgs.gov/mosaic/mars2020_trn/HiRISE/JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif) |
| **Size** | 1.84 GB, 21400 x 21488 cells, 32-bit float |
| **Resolution** | 1 m per map cell, about 0.95 m wide on the ground at Jezero |
| **Coverage** | Longitude 77.2229 to 77.5840 E, latitude 18.3068 to 18.6693 N |
| **Projection** | Equirectangular on a 3,396,190 m sphere, true scale at the equator |
| **Missing data** | -3.4028e+38 |

The USGS metadata lists use constraints as "None".

## Image map (needed for the vision stage)

| | |
|---|---|
| **Name** | Mars 2020 Terrain Relative Navigation HiRISE Orthorectified Image Mosaic |
| **Authors** | Fergason, R.L., Galuszka, D.M., Hare, T.M., Mayer, D.P., Redding, B.L. (2020) |
| **DOI** | [10.5066/P9QJDP48](https://doi.org/10.5066/P9QJDP48) |
| **File** | [JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.tif](https://planetarymaps.usgs.gov/mosaic/mars2020_trn/HiRISE/JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.tif) |
| **Size** | 7.36 GB, 85600 x 85952 cells, 8-bit |
| **License** | Marked CC0 1.0 on the [USGS release page](https://www.usgs.gov/data/mars-2020-terrain-relative-navigation-hirise-orthorectified-image-mosaic) |

## Perseverance's route

| Layer | URL | Contents when checked on 2026-11-06 |
|---|---|---|
| Stops | [M20_waypoints.json](https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json) | 708 points through sol 2022, with position, elevation and rover roll, pitch, yaw and tilt |
| Drives | [M20_traverse.json](https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_traverse.json) | 559 drive segments, with sol, start and end, and length |

These are the layers behind NASA's public location map. I found them through community posts, not
NASA documentation, so they could change without notice and have no formal citation. Keep a dated
copy for reproducibility.

93.6% of the route's points fall inside the elevation map. The rest, at the western end, are
outside it. Stage 4 needs to handle that.

`scripts/download_data.py` saves a dated copy of these files and records their checksums in
`data/raw/MANIFEST.json`.

## Known problems

- **Partial coverage:** 66% of the map's cells have data. The rest of the rectangle lies outside the
  mosaic's image strips, and those gaps are treated as unknown ground.
- **Uneven quality:** in the full slope map, some strips look visibly noisier than others, for example
  south of the delta. This is based on looking at the figure and hasn't been measured. A noisy strip
  could make ground look rougher than it is.

- **Row artifacts:** in the 2 km section checked, 19 of 2,047 rows have abrupt jumps compared with
  nearby rows, and no columns do. They show up as faint horizontal lines in the step-height layer.
  Their effect on routes hasn't been measured.
- **Possibly circular elevations:** NASA's stop elevations match this map to about 4 cm, so they may
  come from it. Don't treat them as independent evidence without confirming their source.
- **Resolution:** a 1 m grid can't show rocks or ledges smaller than a few cells.
