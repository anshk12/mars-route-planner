"""Check file handling using small synthetic maps with the real Jezero projection."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window
from rasterio.windows import transform as window_transform

from mars_rover.terrain.metrics import compute_metrics, required_margin
from mars_rover.terrain.pipeline import LAYERS, iter_tiles, process_terrain
from mars_rover.terrain.source import TerrainSource

MARS_EQC = "+proj=eqc +lat_ts=0 +lat_0=0 +lon_0=0 +R=3396190 +units=m +no_defs"
NODATA = -3.4028226550889045e38
LEFT, TOP = 4577366.0, 1106618.0  # The real map's top-left corner.


def write_map(path, z, nodata=NODATA):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=z.shape[0],
        width=z.shape[1],
        count=1,
        dtype="float32",
        crs=MARS_EQC,
        transform=from_origin(LEFT, TOP, 1.0, 1.0),
        nodata=nodata,
    ) as dst:
        dst.write(z.astype("float32"), 1)
    return path


@pytest.fixture
def bumpy_map(tmp_path):
    rng = np.random.default_rng(7)
    rows, cols = np.indices((230, 317), dtype=np.float64)
    z = (
        -2500
        + 0.05 * cols
        - 0.03 * rows
        + 3 * np.sin(cols / 17)
        + rng.normal(0, 0.2, rows.shape)
    )
    z[100:104, 150:156] = NODATA
    return write_map(tmp_path / "map.tif", z), z


def test_missing_data_marker_becomes_nan(bumpy_map):
    path, _ = bumpy_map
    with TerrainSource(path) as source:
        z = source.read(Window(0, 0, source.width, source.height))
    assert np.isnan(z[100:104, 150:156]).all()
    assert np.isfinite(z[0, 0])
    assert np.isnan(z).sum() == 4 * 6


def test_marker_is_caught_even_if_file_does_not_declare_it(tmp_path):
    z = np.full((10, 10), -2500.0)
    z[5, 5] = NODATA
    path = write_map(tmp_path / "undeclared.tif", z, nodata=None)
    with TerrainSource(path) as source:
        assert np.isnan(source.read(Window(0, 0, 10, 10))[5, 5])


def test_ground_width_matches_cosine_of_latitude(bumpy_map):
    path, _ = bumpy_map
    with TerrainSource(path) as source:
        dx, dy = source.ground_cell_size(0, 5)
        _, lat = source.cell_to_lonlat(0, 0)
    assert 18.0 < lat < 19.0
    assert dx[0] == pytest.approx(np.cos(np.radians(lat)), rel=1e-6)
    assert dy == pytest.approx(1.0, rel=1e-6)


def test_location_round_trip(bumpy_map):
    path, _ = bumpy_map
    with TerrainSource(path) as source:
        lon, lat = source.cell_to_lonlat(40, 60)
        assert source.lonlat_to_cell(lon, lat) == (40, 60)


def test_window_stays_inside_map(bumpy_map):
    path, _ = bumpy_map
    with TerrainSource(path) as source:
        lon, lat = source.cell_to_lonlat(1, 1)
        window = source.window_around(lon, lat, 50)
        assert (window.col_off, window.row_off) == (0, 0)
        with pytest.raises(ValueError):
            source.window_around(0.0, 0.0, 50)
        with pytest.raises(ValueError):
            source.window_around(lon, lat, 10_000)


def test_tiles_cover_every_cell_exactly_once():
    region = Window(5, 9, 103, 77)
    counts = np.zeros((200, 200), dtype=int)
    for tile in iter_tiles(region, 32):
        counts[
            int(tile.row_off) : int(tile.row_off + tile.height),
            int(tile.col_off) : int(tile.col_off + tile.width),
        ] += 1
    assert (counts[9:86, 5:108] == 1).all()
    assert counts.sum() == 103 * 77


@pytest.mark.parametrize("tile_size", [16, 64, 97])
def test_tiled_result_has_no_seams(bumpy_map, tmp_path, tile_size):
    """Tile by tile must exactly match processing the whole map in one piece."""
    path, _ = bumpy_map
    out = tmp_path / f"tiles_{tile_size}"
    with TerrainSource(path) as source:
        paths = process_terrain(source, out, tile_size=tile_size)
        z = source.read(Window(0, 0, source.width, source.height))
        dx, dy = source.ground_cell_size(0, source.height)
    whole = compute_metrics(z, dx, dy)

    for name in LAYERS:
        with rasterio.open(paths[name]) as dataset:
            tiled = dataset.read(1)
        expected = getattr(whole, name).astype(np.float32)
        np.testing.assert_array_equal(np.isnan(tiled), np.isnan(expected))
        np.testing.assert_allclose(tiled, expected, atol=1e-3, rtol=0, equal_nan=True)


def test_region_output_is_georeferenced(bumpy_map, tmp_path):
    path, _ = bumpy_map
    region = Window(40, 30, 100, 80)
    with TerrainSource(path) as source:
        paths = process_terrain(source, tmp_path / "region", region=region, tile_size=32)
        expected_transform = window_transform(region, source.transform)
    with rasterio.open(paths["slope_deg"]) as dataset:
        assert (dataset.width, dataset.height) == (100, 80)
        assert dataset.transform.almost_equals(expected_transform)
        assert dataset.crs is not None


def test_region_edges_use_real_neighbors(bumpy_map, tmp_path):
    """A region's edge cells should use neighbors outside it, instead of turning NaN."""
    path, _ = bumpy_map
    margin = required_margin()
    region = Window(60, 20, 60, 40)
    assert region.row_off > margin and region.col_off > margin
    with TerrainSource(path) as source:
        paths = process_terrain(source, tmp_path / "edges", region=region, tile_size=32)
    with rasterio.open(paths["roughness_m"]) as dataset:
        assert np.isfinite(dataset.read(1)).all()


def test_region_outside_map_is_rejected(bumpy_map, tmp_path):
    path, _ = bumpy_map
    with TerrainSource(path) as source:
        with pytest.raises(ValueError):
            process_terrain(source, tmp_path / "bad", region=Window(300, 0, 100, 100))


def test_failed_run_leaves_no_output(bumpy_map, tmp_path):
    path, _ = bumpy_map
    out = tmp_path / "interrupted"

    def fail_partway(done, total):
        raise RuntimeError("simulated crash")

    with TerrainSource(path) as source:
        with pytest.raises(RuntimeError):
            process_terrain(source, out, tile_size=32, progress=fail_partway)
    assert list(out.iterdir()) == []
