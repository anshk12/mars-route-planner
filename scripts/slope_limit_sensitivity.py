"""See how much the results change as the slope limit changes.

This doesn't choose a limit. All of these numbers come from the same route, and a limit chosen to make them
look good would just be fitted to this data. It only shows how much the choice matters.

Every limit uses the same area around the route, reaching 1 km past it in each direction. The 25 degree row
should match scripts/check_hazards_against_route.py, which works as a check on this script.

Run from the project folder: python -m scripts.slope_limit_sensitivity
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window
from scipy import ndimage

from mars_rover.rover.hazards import (
    HazardClass,
    HazardLimits,
    classify_from_worst,
    worst_in_footprints,
)
from mars_rover.sources import DTM_URL
from mars_rover.terrain.source import TerrainSource
from scripts.check_hazards_against_route import load_drives
from scripts.route_terrain_stats import nearby_cells

DERIVED = Path("data/derived")
DTM_PATH = Path("data/raw") / DTM_URL.rsplit("/", 1)[-1]
MANIFEST = Path("data/raw/MANIFEST.json")
LIMITS_DEG = (20.0, 22.0, 25.0, 27.0, 30.0)
CROP_MARGIN = 1000


def main() -> None:
    rng = np.random.default_rng(2020)  # Same seed as the other checks, so samples match.
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths = {record["name"]: record["path"] for record in manifest["routes"]}

    with TerrainSource(DTM_PATH) as source:
        drives = load_drives(source, paths["traverse"])
        height, width = source.height, source.width

    all_rows = np.concatenate([d["rows"] for d in drives])
    all_cols = np.concatenate([d["cols"] for d in drives])
    unique = np.unique(np.column_stack((all_rows, all_cols)), axis=0)
    rows, cols = unique[:, 0], unique[:, 1]
    near_rows, near_cols = nearby_cells(rows, cols, height, width, rng)

    top = max(int(rows.min()) - CROP_MARGIN, 0)
    left = max(int(cols.min()) - CROP_MARGIN, 0)
    bottom = min(int(rows.max()) + CROP_MARGIN, height)
    right = min(int(cols.max()) + CROP_MARGIN, width)
    window = Window(left, top, right - left, bottom - top)

    # Anything within 3 m of the crop's edge is far outside the 500 m nearby corridor,
    # so the crop can't change the sampled results.
    layers = {}
    for name in ("slope_deg", "step_m", "roughness_m"):
        with rasterio.open(DERIVED / f"{name}.tif") as dataset:
            layers[name] = dataset.read(1, window=window)
    worst = worst_in_footprints(
        layers["slope_deg"], layers["step_m"], layers["roughness_m"], HazardLimits().footprint_cells
    )
    del layers

    print(f"Area checked: {right - left} x {bottom - top} cells")
    print(f"Route: {rows.size:,} cells. Nearby sample: {near_rows.size:,} cells.\n")
    header = (
        "limit | route off-limits | nearby off-limits | area off-limits | drives touching | "
        "points off-limits | route regions | largest holds"
    )
    print(header)
    print("-" * len(header))

    results = []
    for limit in LIMITS_DEG:
        classes = classify_from_worst(worst, HazardLimits(slope_off_limits_deg=limit))
        route = classes[rows - top, cols - left]
        near = classes[near_rows - top, near_cols - left]

        drives_touching = 0
        points_off_limits = 0
        for drive in drives:
            if drive["rows"].size == 0:
                continue
            bad = int(np.sum(classes[drive["rows"] - top, drive["cols"] - left] == HazardClass.OFF_LIMITS))
            points_off_limits += bad
            drives_touching += bad > 0

        drivable = (classes == HazardClass.SAFE) | (classes == HazardClass.RISKY)
        labels, _ = ndimage.label(drivable, structure=np.ones((3, 3), dtype=bool))
        on_route = labels[rows - top, cols - left]
        on_route = on_route[on_route > 0]
        ids, counts = np.unique(on_route, return_counts=True)
        largest = 100.0 * counts.max() / on_route.size
        del labels, drivable

        row = {
            "limit_deg": limit,
            "route_off_limits_pct": 100.0 * float(np.mean(route == HazardClass.OFF_LIMITS)),
            "nearby_off_limits_pct": 100.0 * float(np.mean(near == HazardClass.OFF_LIMITS)),
            "area_off_limits_pct": 100.0 * float(np.mean(classes == HazardClass.OFF_LIMITS)),
            "drives_touching": drives_touching,
            "points_off_limits": points_off_limits,
            "route_regions": int(ids.size),
            "largest_region_pct": largest,
        }
        results.append(row)
        print(
            f"{limit:5.0f} | {row['route_off_limits_pct']:15.3f}% | {row['nearby_off_limits_pct']:16.3f}% | "
            f"{row['area_off_limits_pct']:14.3f}% | {drives_touching:15d} | {points_off_limits:17d} | "
            f"{ids.size:13d} | {largest:12.3f}%",
            flush=True,
        )

    out = DERIVED / "slope_limit_sensitivity.json"
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
