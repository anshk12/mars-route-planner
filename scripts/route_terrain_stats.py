"""Measure the terrain Perseverance actually drove across, compared with nearby ground.

This gives the evidence for choosing Stage 2's hazard limits, in three parts:
1. The terrain directly under the real route, and the worst value in a 3 x 3 m footprint.
2. A random sample of ground within 500 m of the route, which the rover could have reached.
3. The tilt the rover recorded at each stop.

A difference between the route and nearby ground fits a deliberate choice, but doesn't prove
one. Drivers also pick routes for science targets, and the map misses small features.

Run from the project folder: python -m scripts.route_terrain_stats
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window
from scipy.ndimage import binary_dilation

from mars_rover.sources import DTM_URL
from mars_rover.terrain.source import TerrainSource

DERIVED = Path("data/derived")
DTM_PATH = Path("data/raw") / DTM_URL.rsplit("/", 1)[-1]
MANIFEST = Path("data/raw/MANIFEST.json")
COARSE_CELLS = 50  # Cells per grid square when marking ground near the route.
NEARBY_SQUARES = 10  # 10 squares of 50 m gives the 500 m corridor.
NEARBY_SAMPLES = 200_000
CHUNK = 1024

THRESHOLDS = {
    "slope_deg": [10, 15, 20, 25, 30, 35],
    "roughness_m": [0.1, 0.2, 0.3, 0.5],
    "step_m": [0.2, 0.3, 0.4, 0.525],
}


def route_cells(source: TerrainSource, path: str) -> tuple[np.ndarray, np.ndarray, int]:
    """Return unique map cells under the route, plus the number of points off the map."""
    features = json.loads(Path(path).read_text(encoding="utf-8"))["features"]
    rows, cols, outside = [], [], 0
    for feature in features:
        for lon, lat, *_ in feature["geometry"]["coordinates"]:
            row, col = source.lonlat_to_cell(lon, lat)
            if 0 <= row < source.height and 0 <= col < source.width:
                rows.append(row)
                cols.append(col)
            else:
                outside += 1
    unique = np.unique(np.column_stack((rows, cols)), axis=0)
    return unique[:, 0], unique[:, 1], outside


def nearby_cells(
    rows: np.ndarray, cols: np.ndarray, height: int, width: int, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """Sample random cells from squares within about 500 m of the route."""
    grid = np.zeros((height // COARSE_CELLS + 1, width // COARSE_CELLS + 1), dtype=bool)
    grid[rows // COARSE_CELLS, cols // COARSE_CELLS] = True
    grid = binary_dilation(grid, structure=np.ones((3, 3)), iterations=NEARBY_SQUARES)
    square_rows, square_cols = np.nonzero(grid)
    pick = rng.integers(0, square_rows.size, NEARBY_SAMPLES)
    sample_rows = square_rows[pick] * COARSE_CELLS + rng.integers(0, COARSE_CELLS, NEARBY_SAMPLES)
    sample_cols = square_cols[pick] * COARSE_CELLS + rng.integers(0, COARSE_CELLS, NEARBY_SAMPLES)
    keep = (sample_rows < height) & (sample_cols < width)
    return sample_rows[keep], sample_cols[keep]


def read_cells(
    dataset: rasterio.DatasetReader, rows: np.ndarray, cols: np.ndarray, footprint: bool = False
) -> np.ndarray:
    """Read values at many cells, one chunk at a time.

    With ``footprint``, each result is the worst value in the surrounding 3 x 3 cells. A cell is
    NaN if any part of its footprint has no data, so missing data never counts as safe.
    """
    values = np.full(rows.shape, np.nan, dtype=np.float64)
    keys = rows // CHUNK * 100_000 + cols // CHUNK
    for key in np.unique(keys):
        members = np.flatnonzero(keys == key)
        top = max(int(rows[members].min()) - 1, 0)
        left = max(int(cols[members].min()) - 1, 0)
        bottom = min(int(rows[members].max()) + 2, dataset.height)
        right = min(int(cols[members].max()) + 2, dataset.width)
        block = dataset.read(1, window=Window(left, top, right - left, bottom - top))
        r, c = rows[members] - top, cols[members] - left
        if not footprint:
            values[members] = block[r, c]
            continue
        stack = np.full((9, members.size), np.nan)
        for index, (dr, dc) in enumerate(np.ndindex(3, 3)):
            rr, cc = r + dr - 1, c + dc - 1
            inside = (rr >= 0) & (rr < block.shape[0]) & (cc >= 0) & (cc < block.shape[1])
            stack[index, inside] = block[rr[inside], cc[inside]]
        complete = np.isfinite(stack).all(axis=0)
        values[members[complete]] = stack[:, complete].max(axis=0)
    return values


def percentiles(values: np.ndarray) -> str:
    finite = values[np.isfinite(values)]
    p50, p90, p99, p999 = np.percentile(finite, [50, 90, 99, 99.9])
    return f"median {p50:6.2f}  90th {p90:6.2f}  99th {p99:6.2f}  99.9th {p999:6.2f}  max {finite.max():6.2f}"


def share_above(values: np.ndarray, limit: float) -> float:
    finite = values[np.isfinite(values)]
    return 100.0 * float(np.mean(finite > limit))


def main() -> None:
    rng = np.random.default_rng(2020)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths = {record["name"]: record["path"] for record in manifest["routes"]}

    with TerrainSource(DTM_PATH) as source:
        rows, cols, outside = route_cells(source, paths["traverse"])
        near_rows, near_cols = nearby_cells(rows, cols, source.height, source.width, rng)

    print(f"Route: {rows.size:,} unique map cells, {outside:,} route points off the map")
    print(f"Nearby sample: {near_rows.size:,} random cells within about 500 m of the route")

    for layer, limits in THRESHOLDS.items():
        with rasterio.open(DERIVED / f"{layer}.tif") as dataset:
            under = read_cells(dataset, rows, cols)
            worst = read_cells(dataset, rows, cols, footprint=True)
            nearby = read_cells(dataset, near_rows, near_cols)
        print(f"\n=== {layer} ===")
        print(f"  under route     {percentiles(under)}")
        print(f"  worst in 3x3 m  {percentiles(worst)}")
        print(f"  nearby ground   {percentiles(nearby)}")
        print(f"  no data under route: {100 * np.mean(~np.isfinite(under)):.2f}%")
        print("  share above limit:   limit | under route | worst in 3x3 m | nearby ground")
        for limit in limits:
            print(
                f"  {limit:>25} | {share_above(under, limit):10.3f}% | "
                f"{share_above(worst, limit):13.3f}% | {share_above(nearby, limit):12.3f}%"
            )

    stops = json.loads(Path(paths["waypoints"]).read_text(encoding="utf-8"))["features"]
    tilt = np.array([float(stop["properties"]["tilt"]) for stop in stops])
    print(f"\n=== rover tilt at {tilt.size} stops ===")
    print(f"  {percentiles(tilt)}")
    for limit in (10, 15, 20, 25, 30):
        print(f"  above {limit:>2} deg: {int(np.sum(tilt > limit)):3d} stops ({share_above(tilt, limit):.1f}%)")


if __name__ == "__main__":
    main()
