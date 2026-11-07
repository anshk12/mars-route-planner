"""Test the idea that the rover's suspension smooths out small bumps in the map.

The idea predicts that recorded tilt should match map slope better when slope is measured over a wider area,
closer to the rover's size (about 3 m long and 2.7 m wide), compared with smaller windows.

This can only support the idea, not prove it. A wider window also hides map noise and small errors in the
rover's recorded position, which would improve the match in the same way.

Every window uses the same stops, so differences aren't caused by comparing different stops. Uncertainty comes
from resampling whole rover sites, because stops at the same site aren't independent.

Run from the project folder: python -m scripts.test_suspension_idea
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from rasterio.windows import Window

from mars_rover.sources import DTM_URL
from mars_rover.terrain.source import TerrainSource

DTM_PATH = Path("data/raw") / DTM_URL.rsplit("/", 1)[-1]
MANIFEST = Path("data/raw/MANIFEST.json")
WINDOWS_M = (3, 5, 7, 9, 11, 15, 21)
BOOTSTRAPS = 2000


def plane_slope_deg(z: np.ndarray, dx_m: float, dy_m: float) -> float:
    """Fit a plane to a square window and return its slope, or NaN if data is missing."""
    if not np.isfinite(z).all():
        return float("nan")
    radius = z.shape[0] // 2
    offsets = np.arange(-radius, radius + 1, dtype=np.float64)
    sum_squares = float(np.sum(offsets**2)) * z.shape[0]
    z = z.astype(np.float64) - float(z.mean())
    east = float(np.sum(z * offsets[None, :])) / sum_squares / dx_m
    north = float(np.sum(z * -offsets[:, None])) / sum_squares / dy_m
    return float(np.degrees(np.arctan(np.hypot(east, north))))


def load_stops(path: str) -> list[dict]:
    stops = []
    for feature in json.loads(Path(path).read_text(encoding="utf-8"))["features"]:
        props = feature["properties"]
        stops.append(
            {
                "site": str(props["site"]),
                "sol": float(props["sol"]),
                "tilt": float(props["tilt"]),
                "lon": float(props["lon"]),
                "lat": float(props["lat"]),
            }
        )
    return stops


def measure(source: TerrainSource, stops: list[dict]) -> tuple[np.ndarray, list[dict]]:
    """Return slopes per stop and window, keeping stops with data in every window."""
    largest = max(WINDOWS_M) // 2
    rows_out, kept = [], []
    for stop in stops:
        row, col = source.lonlat_to_cell(stop["lon"], stop["lat"])
        if not (largest <= row < source.height - largest and largest <= col < source.width - largest):
            continue
        block = source.read(Window(col - largest, row - largest, 2 * largest + 1, 2 * largest + 1))
        dx, dy = source.ground_cell_size(row, 1)
        slopes = []
        for window in WINDOWS_M:
            trim = largest - window // 2
            slopes.append(plane_slope_deg(block[trim : trim + window, trim : trim + window], float(dx[0]), dy))
        if np.isfinite(slopes).all():
            rows_out.append(slopes)
            kept.append(stop)
    return np.array(rows_out), kept


def scores(slopes: np.ndarray, tilt: np.ndarray) -> tuple[float, float, float]:
    """Return mean absolute error, average bias and correlation."""
    gap = slopes - tilt
    return float(np.mean(np.abs(gap))), float(np.mean(gap)), float(np.corrcoef(slopes, tilt)[0, 1])


def print_table(title: str, slopes: np.ndarray, tilt: np.ndarray) -> None:
    print(f"\n{title} ({tilt.size} stops)")
    print("window | average error | map minus tilt | correlation")
    for index, window in enumerate(WINDOWS_M):
        error, bias, corr = scores(slopes[:, index], tilt)
        print(f"{window:4d} m | {error:9.2f} deg | {bias:+10.2f} deg | {corr:10.3f}")


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths = {record["name"]: record["path"] for record in manifest["routes"]}
    with TerrainSource(DTM_PATH) as source:
        slopes, stops = measure(source, load_stops(paths["waypoints"]))

    tilt = np.array([stop["tilt"] for stop in stops])
    sites = np.array([stop["site"] for stop in stops])
    sols = np.array([stop["sol"] for stop in stops])
    unique_sites = np.unique(sites)
    print(f"Stops with data in every window: {tilt.size}, at {unique_sites.size} rover sites")

    print_table("All stops", slopes, tilt)
    print_table("Steeper stops, tilt of at least 10 degrees", slopes[tilt >= 10], tilt[tilt >= 10])
    middle = np.median(sols)
    print_table(f"Earlier half, before sol {middle:.0f}", slopes[sols < middle], tilt[sols < middle])
    print_table(f"Later half, from sol {middle:.0f}", slopes[sols >= middle], tilt[sols >= middle])

    rng = np.random.default_rng(2020)
    members = {site: np.flatnonzero(sites == site) for site in unique_sites}
    errors = np.zeros((BOOTSTRAPS, len(WINDOWS_M)))
    for trial in range(BOOTSTRAPS):
        chosen = rng.choice(unique_sites, size=unique_sites.size, replace=True)
        picks = np.concatenate([members[site] for site in chosen])
        errors[trial] = np.mean(np.abs(slopes[picks] - tilt[picks, None]), axis=0)

    print("\nResampling whole sites, compared with the 3 m window (negative means closer to tilt):")
    print("window | change in average error, 95% range | share of trials this window was best")
    best = np.argmin(errors, axis=1)
    for index, window in enumerate(WINDOWS_M):
        change = errors[:, index] - errors[:, 0]
        low, high = np.percentile(change, [2.5, 97.5])
        print(f"{window:4d} m | {low:+.2f} to {high:+.2f} deg | {100 * np.mean(best == index):.1f}%")


if __name__ == "__main__":
    main()
