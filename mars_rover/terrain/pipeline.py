"""Process a whole elevation map tile by tile, so it fits in memory.

Each tile is read with an extra margin of neighboring cells, measured, and trimmed back
to its core before writing. The margin is what keeps seams out of the result: the test
suite checks that tiled output exactly matches processing the whole grid in one piece.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import asdict
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window
from rasterio.windows import transform as window_transform

from .metrics import compute_metrics, required_margin
from .source import TerrainSource

LAYERS = ("slope_deg", "aspect_deg", "roughness_m", "step_m")


def iter_tiles(region: Window, tile_size: int) -> Iterator[Window]:
    """Split a region into tiles that cover every cell exactly once."""
    if tile_size < 1:
        raise ValueError("tile_size must be positive")
    col_start, row_start = int(region.col_off), int(region.row_off)
    for row in range(row_start, row_start + int(region.height), tile_size):
        height = min(tile_size, row_start + int(region.height) - row)
        for col in range(col_start, col_start + int(region.width), tile_size):
            width = min(tile_size, col_start + int(region.width) - col)
            yield Window(col, row, width, height)


def _padded(tile: Window, margin: int, width: int, height: int) -> Window:
    """Grow a tile by a margin, stopping at the edges of the map."""
    left = max(int(tile.col_off) - margin, 0)
    top = max(int(tile.row_off) - margin, 0)
    right = min(int(tile.col_off + tile.width) + margin, width)
    bottom = min(int(tile.row_off + tile.height) + margin, height)
    return Window(left, top, right - left, bottom - top)


def process_terrain(
    source: TerrainSource,
    out_dir: str | Path,
    region: Window | None = None,
    tile_size: int = 2048,
    roughness_window: int = 11,
    step_window: int = 3,
    plane_window: int = 21,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Path]:
    """Compute terrain layers for a region and write one GeoTIFF per layer.

    Files are written under a temporary name and renamed at the end, so an interrupted
    run can't leave a half-finished layer that looks complete.
    """
    full = Window(0, 0, source.width, source.height)
    region = full if region is None else region
    if (
        region.col_off < 0
        or region.row_off < 0
        or region.col_off + region.width > source.width
        or region.row_off + region.height > source.height
    ):
        raise ValueError("region must be inside the map")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    margin = required_margin(roughness_window, step_window, plane_window)
    # GeoTIFF internal tiles must be a multiple of 16.
    block = max(16, min(512, tile_size // 16 * 16))
    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "count": 1,
        "width": int(region.width),
        "height": int(region.height),
        "crs": source.crs,
        "transform": window_transform(region, source.transform),
        "nodata": float("nan"),
        "tiled": True,
        "blockxsize": block,
        "blockysize": block,
        "compress": "deflate",
        "predictor": 3,
        "BIGTIFF": "IF_SAFER",
    }

    final_paths = {name: out_dir / f"{name}.tif" for name in LAYERS}
    temp_paths = {name: out_dir / f".{name}.tmp.tif" for name in LAYERS}
    outputs = {name: rasterio.open(temp_paths[name], "w", **profile) for name in LAYERS}
    completed = False
    try:
        tiles = list(iter_tiles(region, tile_size))
        for index, tile in enumerate(tiles, start=1):
            padded = _padded(tile, margin, source.width, source.height)
            z = source.read(padded)
            dx, dy = source.ground_cell_size(int(padded.row_off), int(padded.height))
            metrics = compute_metrics(
                z, dx, dy, roughness_window, step_window, plane_window
            )

            top = int(tile.row_off - padded.row_off)
            left = int(tile.col_off - padded.col_off)
            core = (
                slice(top, top + int(tile.height)),
                slice(left, left + int(tile.width)),
            )
            target = Window(
                int(tile.col_off - region.col_off),
                int(tile.row_off - region.row_off),
                int(tile.width),
                int(tile.height),
            )
            for name, layer in asdict(metrics).items():
                outputs[name].write(layer[core].astype(np.float32), 1, window=target)
            if progress is not None:
                progress(index, len(tiles))
        completed = True
    finally:
        for dataset in outputs.values():
            dataset.close()
        if not completed:
            for path in temp_paths.values():
                path.unlink(missing_ok=True)

    for name in LAYERS:
        os.replace(temp_paths[name], final_paths[name])
    return final_paths
