"""Where the project's public data lives. Details and citations are in docs/DATA.md."""

USGS_HIRISE_BASE = "https://planetarymaps.usgs.gov/mosaic/mars2020_trn/HiRISE/"

# 1 m elevation mosaic of Jezero Crater (1.84 GB, 21400 x 21488 cells).
DTM_URL = (
    USGS_HIRISE_BASE
    + "JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif"
)

# 25 cm image mosaic of the same area (7.36 GB, 85600 x 85952 cells).
ORTHO_URL = (
    USGS_HIRISE_BASE + "JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.tif"
)

# NASA's public location-map layers. Found through community posts, not NASA
# documentation, so they may change. Always keep a dated copy.
ROUTE_URLS = {
    "waypoints": "https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json",
    "traverse": "https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_traverse.json",
}

# Approximate Perseverance landing site, used only as a convenient test area.
LANDING_SITE_LON_LAT = (77.4508, 18.4447)
