"""Test the hazard map against Perseverance's real drives.

The rover really drove these routes, so ground it crossed that the model calls off-limits needs explaining.
It could mean the limit is too strict, the map is wrong there, the recorded position is slightly off, or the
rover really did take on steeper ground.

This also tests whether the off-limits areas cut the route apart, which would leave a planner unable to
reproduce the rover's journey no matter how it searched. It reports rough energy estimates too, using the
assumed values in docs/decisions.md.

Run from the project folder: python -m scripts.check_hazards_against_route
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window
from scipy import ndimage

from mars_rover.rover.energy import energy_per_meter
from mars_rover.rover.hazards import HazardClass
from mars_rover.sources import DTM_URL
from mars_rover.terrain.source import TerrainSource
from scripts.route_terrain_stats import nearby_cells, read_cells

DERIVED = Path("data/derived")
DTM_PATH = Path("data/raw") / DTM_URL.rsplit("/", 1)[-1]
MANIFEST = Path("data/raw/MANIFEST.json")
CROP_MARGIN = 1000
NAMES = {cls: cls.name.replace("_", "-").lower() for cls in HazardClass}


def load_drives(source: TerrainSource, path: str) -> list[dict]:
    """Return each drive with its map cells, keeping points that fall inside the map."""
    drives = []
    for feature in json.loads(Path(path).read_text(encoding="utf-8"))["features"]:
        rows, cols, lons, lats = [], [], [], []
        for lon, lat, *_ in feature["geometry"]["coordinates"]:
            row, col = source.lonlat_to_cell(lon, lat)
            if 0 <= row < source.height and 0 <= col < source.width:
                rows.append(row)
                cols.append(col)
                lons.append(lon)
                lats.append(lat)
        drives.append(
            {
                "sol": feature["properties"]["sol"],
                "length": float(feature["properties"]["length"]),
                "from_rmc": feature["properties"].get("fromRMC"),
                "to_rmc": feature["properties"].get("toRMC"),
                "rows": np.array(rows, dtype=np.int64),
                "cols": np.array(cols, dtype=np.int64),
                "lons": np.array(lons),
                "lats": np.array(lats),
            }
        )
    return drives


def class_shares(classes: np.ndarray) -> str:
    return "  ".join(
        f"{NAMES[cls]} {100 * np.mean(classes == cls):6.3f}%" for cls in HazardClass
    )


def check_connectivity(hazard: rasterio.DatasetReader, rows: np.ndarray, cols: np.ndarray) -> None:
    """Report whether the route's drivable cells form one connected region."""
    top = max(int(rows.min()) - CROP_MARGIN, 0)
    left = max(int(cols.min()) - CROP_MARGIN, 0)
    bottom = min(int(rows.max()) + CROP_MARGIN, hazard.height)
    right = min(int(cols.max()) + CROP_MARGIN, hazard.width)
    classes = hazard.read(1, window=Window(left, top, right - left, bottom - top))
    drivable = (classes == HazardClass.SAFE) | (classes == HazardClass.RISKY)
    labels, _ = ndimage.label(drivable, structure=np.ones((3, 3), dtype=bool))

    route_labels = labels[rows - top, cols - left]
    on_drivable = route_labels[route_labels > 0]
    print(f"\nConnectivity, checked in a {right - left} x {bottom - top} cell area around the route:")
    print(f"  drivable cells in the area: {100 * drivable.mean():.1f}%")
    if on_drivable.size == 0:
        print("  no route cells are drivable")
        return
    ids, counts = np.unique(on_drivable, return_counts=True)
    order = np.argsort(counts)[::-1]
    print(f"  the route's drivable cells fall in {ids.size} separate regions")
    print(f"  largest region holds {100 * counts[order[0]] / on_drivable.size:.2f}% of them")
    sizes = np.bincount(labels.ravel())
    for rank in order[:4]:
        print(
            f"    region of {sizes[ids[rank]] / 1e6:8.2f} km^2 "
            f"holds {counts[rank]:6,} route cells"
        )


def check_energy(source: TerrainSource, drives: list[dict]) -> None:
    """Estimate energy along the real route and check its length against NASA's figures."""
    with rasterio.open(DERIVED / "slope_deg.tif") as slope_layer, rasterio.open(
        DERIVED / "aspect_deg.tif"
    ) as aspect_layer:
        total_joules, measured_m, distance_m, reported_m = 0.0, 0.0, 0.0, 0.0
        radius = source.radius_m
        for drive in drives:
            if drive["rows"].size < 2:
                continue
            lat = np.radians(drive["lats"])
            lon = np.radians(drive["lons"])
            north = radius * np.diff(lat)
            east = radius * np.diff(lon) * np.cos((lat[:-1] + lat[1:]) / 2)
            step = np.hypot(east, north)
            heading = np.degrees(np.arctan2(east, north)) % 360.0
            slope = read_cells(slope_layer, drive["rows"][:-1], drive["cols"][:-1])
            aspect = read_cells(aspect_layer, drive["rows"][:-1], drive["cols"][:-1])
            energy = energy_per_meter(slope, aspect, heading)
            known = np.isfinite(energy)
            total_joules += float(np.sum(energy[known] * step[known]))
            measured_m += float(np.sum(step[known]))
            distance_m += float(np.sum(step))
            reported_m += drive["length"]

    print("\nEnergy, using assumed physics values:")
    print(f"  route length from coordinates, within the map: {distance_m / 1000:.2f} km")
    print(f"  NASA's segment lengths for those drives: {reported_m / 1000:.2f} km")
    print(f"  distance with terrain data: {measured_m / 1000:.2f} km")
    if measured_m:
        print(f"  estimated drive energy: {total_joules / 3.6e6:.1f} kWh, {total_joules / measured_m:.0f} J per meter")
    print(f"  on flat ground the model gives {float(energy_per_meter(0.0, np.nan, 0.0)):.0f} J per meter")


def main() -> None:
    rng = np.random.default_rng(2020)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths = {record["name"]: record["path"] for record in manifest["routes"]}

    with TerrainSource(DTM_PATH) as source:
        drives = load_drives(source, paths["traverse"])
        all_rows = np.concatenate([d["rows"] for d in drives])
        all_cols = np.concatenate([d["cols"] for d in drives])
        unique = np.unique(np.column_stack((all_rows, all_cols)), axis=0)
        rows, cols = unique[:, 0], unique[:, 1]
        near_rows, near_cols = nearby_cells(rows, cols, source.height, source.width, rng)

        with rasterio.open(DERIVED / "hazard_class.tif") as hazard:
            route_classes = read_cells(hazard, rows, cols).astype(np.uint8)
            near_classes = read_cells(hazard, near_rows, near_cols).astype(np.uint8)

            print(f"Under the route ({rows.size:,} cells):  {class_shares(route_classes)}")
            print(f"Nearby ground ({near_rows.size:,} cells):  {class_shares(near_classes)}")

            print(f"\nDrives ({len(drives)} total):")
            flagged = []
            with_data = 0
            for drive in drives:
                if drive["rows"].size == 0:
                    continue
                with_data += 1
                classes = read_cells(hazard, drive["rows"], drive["cols"]).astype(np.uint8)
                bad = int(np.sum(classes == HazardClass.OFF_LIMITS))
                if bad:
                    flagged.append((bad, drive["sol"], drive["length"], classes.size))
            print(f"  drives with points on the map: {with_data}")
            print(f"  drives touching off-limits ground: {len(flagged)}")
            for bad, sol, length, count in sorted(flagged, key=lambda item: -item[0])[:10]:
                print(f"    sol {sol}: {bad} of {count} points off-limits, drive length {length:.1f} m")

            check_connectivity(hazard, rows, cols)

        check_energy(source, drives)


if __name__ == "__main__":
    main()
