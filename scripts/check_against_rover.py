"""Check the terrain engine against real rover measurements, and look for map artifacts.

1. Elevation: does the orbital map agree with the height the rover recorded at each stop?
2. Tilt: does orbital slope track the tilt the rover measured? Tilt isn't the same as
   ground slope, since a rover's body also rocks on nearby rocks and ripples, so this
   looks for a relationship and not an exact match.
3. Stripes: do some map rows jump abruptly compared with their neighbors? Real terrain has
   no preferred direction, so a pattern tied to rows suggests a flaw in the map itself.

Run from the project folder: python -m scripts.check_against_rover [--size 2048]
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import requests
from rasterio.windows import Window
from scipy.ndimage import median_filter

from mars_rover.sources import DTM_URL, LANDING_SITE_LON_LAT, ROUTE_URLS
from mars_rover.terrain.metrics import gradient_horn, slope_aspect
from mars_rover.terrain.source import TerrainSource


def load_waypoints() -> list[dict]:
    response = requests.get(ROUTE_URLS["waypoints"], timeout=120)
    response.raise_for_status()
    return [feature["properties"] for feature in json.loads(response.text)["features"]]


def outlier_lines(mean_jump: np.ndarray, threshold: float = 5.0) -> np.ndarray:
    """Return single lines that jump far more than the lines right next to them.

    Comparing against nearby lines ignores broad rough areas, which raise a whole block
    of lines together, and keeps only sharp, line-shaped features.
    """
    local = median_filter(mean_jump, size=21, mode="nearest")
    excess = mean_jump - local
    spread = 1.4826 * np.median(np.abs(excess - np.median(excess)))
    return np.flatnonzero(excess > threshold * spread)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=2048)
    args = parser.parse_args()

    with TerrainSource(DTM_URL) as source:
        window: Window = source.window_around(*LANDING_SITE_LON_LAT, args.size)
        z = source.read(window)
        dx, dy = source.ground_cell_size(int(window.row_off), int(window.height))
        slope, _ = slope_aspect(*gradient_horn(z, dx, dy))

        rows = []
        for stop in load_waypoints():
            row, col = source.lonlat_to_cell(float(stop["lon"]), float(stop["lat"]))
            r, c = row - int(window.row_off), col - int(window.col_off)
            if 2 <= r < z.shape[0] - 2 and 2 <= c < z.shape[1] - 2:
                rows.append(
                    (
                        float(stop["elev_geoid"]),
                        float(np.nanmean(z[r - 2 : r + 3, c - 2 : c + 3])),
                        float(stop["tilt"]),
                        float(np.nanmean(slope[r - 2 : r + 3, c - 2 : c + 3])),
                    )
                )

    print(f"Section: {args.size} x {args.size} cells around the landing site")
    print(f"Rover stops inside it: {len(rows)}")
    if len(rows) >= 3:
        rover_elev, map_elev, tilt, map_slope = np.array(rows).T
        difference = map_elev - rover_elev
        print("\nElevation, map minus rover:")
        print(f"  mean {difference.mean():+.2f} m, spread (SD) {difference.std():.2f} m")
        print(f"  largest gap {np.abs(difference).max():.2f} m")
        print(f"  correlation {np.corrcoef(rover_elev, map_elev)[0, 1]:.4f}")
        print("\nRover tilt compared with orbital slope (not expected to match exactly):")
        print(f"  median tilt {np.median(tilt):.2f} deg, median slope {np.median(map_slope):.2f} deg")
        print(f"  correlation {np.corrcoef(tilt, map_slope)[0, 1]:.3f}")

    print("\nStripes (average height jump between neighboring lines):")
    row_jump = np.nanmean(np.abs(np.diff(z, axis=0)), axis=1)
    col_jump = np.nanmean(np.abs(np.diff(z, axis=1)), axis=0)
    bad_rows = outlier_lines(row_jump)
    bad_cols = outlier_lines(col_jump)
    print(f"  rows:    median {np.median(row_jump):.4f} m, worst {row_jump.max():.4f} m, "
          f"{bad_rows.size} of {row_jump.size} are outliers")
    print(f"  columns: median {np.median(col_jump):.4f} m, worst {col_jump.max():.4f} m, "
          f"{bad_cols.size} of {col_jump.size} are outliers")
    worst = bad_rows[np.argsort(row_jump[bad_rows])[::-1][:8]]
    print("  sharpest rows (row: jump vs. median of nearby rows):")
    nearby = median_filter(row_jump, size=21, mode="nearest")
    for row in worst:
        print(f"    {int(row)}: {row_jump[row]:.4f} m vs. {nearby[row]:.4f} m")


if __name__ == "__main__":
    main()
