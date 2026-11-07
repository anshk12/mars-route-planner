"""Close Stage 2's open questions with four checks.

1. Sol 999: is its 36.9 degree reading real, or a flaw in the map?
2. Off-limits patches: what do a random sample look like in orbital photos?
3. Energy: how much do the assumed physics values change the results?
4. Hazard settings: how much do the ledge and roughness limits change the map?

Run from the project folder: python -m scripts.stage2_followups
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window
from scipy import ndimage, stats

from mars_rover.rover.energy import RoverPhysics, energy_per_meter
from mars_rover.rover.hazards import (
    HazardClass,
    HazardLimits,
    classify_from_worst,
    worst_in_footprints,
)
from mars_rover.sources import DTM_URL, ORTHO_URL
from mars_rover.terrain.source import TerrainSource
from mars_rover.terrain.visualize import MAGNITUDE, SURFACE, TEXT_PRIMARY, TEXT_SECONDARY
from scripts.check_hazards_against_route import load_drives
from scripts.investigate_steep_drives import load_stops
from scripts.route_terrain_stats import nearby_cells, read_cells
from scripts.test_suspension_idea import plane_slope_deg

DERIVED = Path("data/derived")
DTM_PATH = Path("data/raw") / DTM_URL.rsplit("/", 1)[-1]
MANIFEST = Path("data/raw/MANIFEST.json")
SOL_999_FIGURE = Path("figures/sol_999_steepest_point.png")
PATCHES_FIGURE = Path("figures/off_limits_patch_sample.png")
ROUTE_COLOR = "#eb6834"
PATCH_SAMPLE = 12
PATCH_VIEW_M = 60
CROP_MARGIN = 1000
TOP_SPEED_M_S = 0.042
PEAK_POWER_W = 900.0


def read_ortho(top: int, left: int, height: int, width: int, max_pixels: int = 500) -> np.ndarray:
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"), rasterio.open(f"/vsicurl/{ORTHO_URL}") as ortho:
        return ortho.read(
            1,
            window=Window(left * 4, top * 4, width * 4, height * 4),
            out_shape=(min(height * 4, max_pixels), min(width * 4, max_pixels)),
            resampling=Resampling.bilinear,
        )


def stretched(ax, image: np.ndarray, extent: tuple[float, float, float, float]) -> None:
    valid = image[image > 0]
    low, high = np.percentile(valid, [1, 99]) if valid.size else (0, 255)
    ax.imshow(image, cmap="gray", vmin=low, vmax=high, extent=extent, interpolation="bilinear")


def check_sol_999(source, drives, stops, slope, hazard) -> None:
    print("=== 1. Sol 999 ===")
    drive = next(d for d in drives if str(d["sol"]) == "999")
    classes = read_cells(hazard, drive["rows"], drive["cols"]).astype(np.uint8)
    off = np.flatnonzero(classes == HazardClass.OFF_LIMITS)
    worst = read_cells(slope, drive["rows"][off], drive["cols"][off], footprint=True)
    point = off[int(np.nanargmax(worst))]
    row, col = int(drive["rows"][point]), int(drive["cols"][point])

    pad = 10
    block = slope.read(1, window=Window(col - pad, row - pad, 2 * pad + 1, 2 * pad + 1))
    around = block[pad - 1 : pad + 2, pad - 1 : pad + 2]
    peak_r, peak_c = np.unravel_index(int(np.nanargmax(around)), around.shape)
    print(f"Steepest point on the drive: row {row}, col {col}")
    print(f"  slope under the path {block[pad, pad]:.1f} deg, steepest cell in footprint {np.nanmax(around):.1f} deg")
    print(f"  that steepest cell is at offset (row {peak_r - 1:+d}, col {peak_c - 1:+d}) from the path")

    size = 21
    half = size // 2
    z = source.read(Window(col - half, row - half, size, size))
    dx, dy = source.ground_cell_size(row, 1)
    print("  slope over wider windows at this point:")
    for window in (3, 5, 7, 11, 15, 21):
        trim = half - window // 2
        value = plane_slope_deg(z[trim : trim + window, trim : trim + window], float(dx[0]), dy)
        print(f"    {window:2d} m: {value:5.1f} deg")

    band = 100
    zone = source.read(Window(max(col - band, 0), row - 40, 2 * band + 1, 81))
    jump = np.nanmean(np.abs(np.diff(zone, axis=0)), axis=1)
    local = ndimage.median_filter(jump, size=21, mode="nearest")
    excess = jump - local
    spread = 1.4826 * np.median(np.abs(excess - np.median(excess)))
    near = excess[37:43]
    print(f"  row-flaw check: largest local row jump within 3 rows is {near.max() / spread:.1f} spreads above nearby rows")

    start = stops.get(drive["from_rmc"], {}).get("tilt")
    end = stops.get(drive["to_rmc"], {}).get("tilt")
    print(f"  tilt at drive start {start}, end {end} deg")
    nearest = sorted(
        (float(np.hypot(s["row"] - row, s["col"] - col)), s["tilt"], s["sol"])
        for s in stops.values()
        if s["inside"]
    )[:4]
    for distance, tilt, sol in nearest:
        print(f"    stop {distance:6.1f} m away, sol {sol}: tilt {tilt:.1f} deg")

    view = 40
    top, left = row - view, col - view
    size_m = 2 * view
    image = read_ortho(top, left, size_m, size_m, max_pixels=1000)
    local_slope = slope.read(1, window=Window(left, top, size_m, size_m))
    local_class = hazard.read(1, window=Window(left, top, size_m, size_m))
    extent = (0, size_m, 0, size_m)

    fig, axes = plt.subplots(1, 2, figsize=(14, 7), facecolor=SURFACE, layout="constrained")
    stretched(axes[0], image, extent)
    shown = axes[1].imshow(
        np.ma.masked_invalid(local_slope), cmap=MAGNITUDE, vmin=0, vmax=35, extent=extent, interpolation="nearest"
    )
    bar = fig.colorbar(shown, ax=axes[1], fraction=0.04, pad=0.02, extend="max")
    bar.outline.set_visible(False)
    bar.ax.tick_params(colors=TEXT_SECONDARY, labelsize=8, length=0)
    bar.set_label("slope in degrees", color=TEXT_SECONDARY, fontsize=9)
    axes[0].set_title("Orbital photo, 25 cm per pixel", loc="left", color=TEXT_PRIMARY, fontsize=12)
    axes[1].set_title("Map slope, off-limits areas outlined", loc="left", color=TEXT_PRIMARY, fontsize=12)
    mask = (local_class == HazardClass.OFF_LIMITS).astype(float)
    for ax in axes:
        ax.contour(mask, levels=[0.5], colors=[SURFACE], linewidths=1.5, extent=extent, origin="upper")
        x = drive["cols"] - left + 0.5
        y = size_m - (drive["rows"] - top + 0.5)
        ax.plot(x, y, color=SURFACE, linewidth=3.2)
        ax.plot(x, y, color=ROUTE_COLOR, linewidth=1.8)
        ax.set_xlim(0, size_m)
        ax.set_ylim(0, size_m)
        ax.set_xlabel("meters east", color=TEXT_SECONDARY, fontsize=9)
        ax.tick_params(colors=TEXT_SECONDARY, labelsize=8, length=2)
        for spine in ax.spines.values():
            spine.set_visible(False)
    axes[0].set_ylabel("meters north", color=TEXT_SECONDARY, fontsize=9)
    fig.suptitle("Sol 999's steepest point, centered, with the rover's path in orange", x=0.01, ha="left",
                 fontsize=13, color=TEXT_PRIMARY)
    SOL_999_FIGURE.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(SOL_999_FIGURE, dpi=120, facecolor=SURFACE)
    plt.close(fig)
    print(f"  saved {SOL_999_FIGURE}")


def inspect_patches(worst, top, left, slope, rng) -> None:
    print("\n=== 2. Off-limits patches ===")
    classes = classify_from_worst(worst, HazardLimits())
    labels, count = ndimage.label(classes == HazardClass.OFF_LIMITS, structure=np.ones((3, 3), dtype=bool))
    sizes = np.bincount(labels.ravel())[1:]
    boxes = ndimage.find_objects(labels)
    heights = np.array([box[0].stop - box[0].start for box in boxes])
    widths = np.array([box[1].stop - box[1].start for box in boxes])

    print(f"Patches in the {classes.shape[1]} x {classes.shape[0]} m area around the route: {count:,}")
    print(f"  size in cells: median {np.median(sizes):.0f}, 90th {np.percentile(sizes, 90):.0f}, max {sizes.max():,}")
    bins = [(0, 25), (25, 100), (100, 1000), (1000, 10**9)]
    for low, high in bins:
        members = (sizes >= low) & (sizes < high)
        print(f"  {low:>5} to {high:<10}: {int(members.sum()):7,} patches, {100 * sizes[members].sum() / sizes.sum():5.1f}% of off-limits area")
    thin = (heights <= 5) & (widths >= 25)
    print(f"  thin east-west streaks that could reflect the row flaw (at most 5 m tall, at least 25 m wide): "
          f"{int(thin.sum()):,} patches, {100 * sizes[thin].sum() / sizes.sum():.2f}% of off-limits area")

    picks = rng.choice(count, size=min(PATCH_SAMPLE, count), replace=False)
    fig, axes = plt.subplots(3, 4, figsize=(14, 11), facecolor=SURFACE, layout="constrained")
    half = PATCH_VIEW_M // 2
    print("  sampled patches (size in cells, height x width in m):")
    for number, (ax, pick) in enumerate(zip(axes.flat, picks), start=1):
        box = boxes[pick]
        center_row = (box[0].start + box[0].stop) // 2 + top
        center_col = (box[1].start + box[1].stop) // 2 + left
        view_top, view_left = center_row - half, center_col - half
        image = read_ortho(view_top, view_left, PATCH_VIEW_M, PATCH_VIEW_M)
        extent = (0, PATCH_VIEW_M, 0, PATCH_VIEW_M)
        stretched(ax, image, extent)
        row_start = max(view_top - top, 0)
        col_start = max(view_left - left, 0)
        crop = classes[row_start : row_start + PATCH_VIEW_M, col_start : col_start + PATCH_VIEW_M]
        if crop.shape == (PATCH_VIEW_M, PATCH_VIEW_M):
            ax.contour((crop == HazardClass.OFF_LIMITS).astype(float), levels=[0.5], colors=[ROUTE_COLOR],
                       linewidths=1.4, extent=extent, origin="upper")
        ax.set_title(f"{number}: {sizes[pick]:,} cells", loc="left", fontsize=10, color=TEXT_PRIMARY)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        print(f"    {number:2d}: {sizes[pick]:>6,} cells, {heights[pick]} x {widths[pick]} m")
    fig.suptitle(f"{PATCH_SAMPLE} randomly chosen off-limits patches, outlined in orange. Each view is {PATCH_VIEW_M} m wide",
                 x=0.01, ha="left", fontsize=13, color=TEXT_PRIMARY)
    fig.savefig(PATCHES_FIGURE, dpi=110, facecolor=SURFACE)
    plt.close(fig)
    print(f"  saved {PATCHES_FIGURE}")


def bound_energy(source, drives) -> None:
    print("\n=== 3. Energy assumptions ===")
    segments = []
    with rasterio.open(DERIVED / "slope_deg.tif") as slope_layer, rasterio.open(DERIVED / "aspect_deg.tif") as aspect_layer:
        for drive in drives:
            if drive["rows"].size < 2:
                continue
            lat = np.radians(drive["lats"])
            lon = np.radians(drive["lons"])
            north = source.radius_m * np.diff(lat)
            east = source.radius_m * np.diff(lon) * np.cos((lat[:-1] + lat[1:]) / 2)
            segments.append(
                (
                    np.hypot(east, north),
                    np.degrees(np.arctan2(east, north)) % 360.0,
                    read_cells(slope_layer, drive["rows"][:-1], drive["cols"][:-1]),
                    read_cells(aspect_layer, drive["rows"][:-1], drive["cols"][:-1]),
                )
            )

    def per_drive(physics: RoverPhysics) -> tuple[float, float, np.ndarray]:
        total, distance, rates = 0.0, 0.0, []
        for step, heading, slope, aspect in segments:
            energy = energy_per_meter(slope, aspect, heading, physics)
            known = np.isfinite(energy)
            if known.any() and step[known].sum() > 0:
                joules = float(np.sum(energy[known] * step[known]))
                meters = float(np.sum(step[known]))
                total += joules
                distance += meters
                rates.append(joules / meters)
            else:
                rates.append(np.nan)
        return total, distance, np.array(rates)

    _, _, baseline = per_drive(RoverPhysics())
    print("rolling resistance | efficiency | route kWh | J per meter | flat J/m | flat W at top speed | rank match with default")
    for resistance in (0.05, 0.10, 0.15, 0.20, 0.30):
        for efficiency in (0.3, 0.5, 0.7):
            physics = RoverPhysics(rolling_resistance=resistance, drivetrain_efficiency=efficiency)
            total, distance, rates = per_drive(physics)
            flat = float(energy_per_meter(0.0, np.nan, 0.0, physics))
            known = np.isfinite(rates) & np.isfinite(baseline)
            rank = stats.spearmanr(rates[known], baseline[known]).statistic
            print(
                f"{resistance:18.2f} | {efficiency:10.1f} | {total / 3.6e6:9.1f} | {total / distance:11.0f} | "
                f"{flat:8.0f} | {flat * TOP_SPEED_M_S:19.1f} | {rank:10.3f}"
            )
    print(f"Peak power demand published by NASA: {PEAK_POWER_W:.0f} W")


def settings_sensitivity(worst, rows, cols, near_rows, near_cols, top, left) -> None:
    print("\n=== 4. Hazard settings ===")
    print("Off-limits ground doesn't depend on these settings, so only the risky share changes.")
    print("step limit | roughness limit | route risky | nearby risky | whole area risky")
    for step, roughness in [
        (0.525, 0.3), (0.525, 0.2), (0.525, 0.4), (0.525, 0.5), (0.3, 0.3), (0.4, 0.3), (0.7, 0.3), (1.0, 0.3),
    ]:
        classes = classify_from_worst(worst, HazardLimits(step_risky_m=step, roughness_risky_m=roughness))
        route = classes[rows - top, cols - left]
        near = classes[near_rows - top, near_cols - left]
        print(
            f"{step:10.3f} | {roughness:15.2f} | {100 * np.mean(route == HazardClass.RISKY):10.3f}% | "
            f"{100 * np.mean(near == HazardClass.RISKY):11.3f}% | {100 * np.mean(classes == HazardClass.RISKY):15.3f}%"
        )


def main() -> None:
    rng = np.random.default_rng(2020)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths = {record["name"]: record["path"] for record in manifest["routes"]}

    with TerrainSource(DTM_PATH) as source:
        drives = load_drives(source, paths["traverse"])
        stops = load_stops(source, paths["waypoints"])
        with rasterio.open(DERIVED / "slope_deg.tif") as slope, rasterio.open(DERIVED / "hazard_class.tif") as hazard:
            check_sol_999(source, drives, stops, slope, hazard)

        all_rows = np.concatenate([d["rows"] for d in drives])
        all_cols = np.concatenate([d["cols"] for d in drives])
        unique = np.unique(np.column_stack((all_rows, all_cols)), axis=0)
        rows, cols = unique[:, 0], unique[:, 1]
        near_rows, near_cols = nearby_cells(rows, cols, source.height, source.width, rng)
        top = max(int(rows.min()) - CROP_MARGIN, 0)
        left = max(int(cols.min()) - CROP_MARGIN, 0)
        bottom = min(int(rows.max()) + CROP_MARGIN, source.height)
        right = min(int(cols.max()) + CROP_MARGIN, source.width)
        window = Window(left, top, right - left, bottom - top)
        layers = {}
        for name in ("slope_deg", "step_m", "roughness_m"):
            with rasterio.open(DERIVED / f"{name}.tif") as dataset:
                layers[name] = dataset.read(1, window=window)
        worst = worst_in_footprints(
            layers["slope_deg"], layers["step_m"], layers["roughness_m"], HazardLimits().footprint_cells
        )
        del layers

        with rasterio.open(DERIVED / "slope_deg.tif") as slope:
            inspect_patches(worst, top, left, slope, rng)
        bound_energy(source, drives)
        settings_sensitivity(worst, rows, cols, near_rows, near_cols, top, left)


if __name__ == "__main__":
    main()
