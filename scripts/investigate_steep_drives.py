"""Investigate the real drives that cross ground the hazard model calls off-limits.

The rover really drove these paths, so there are several possible explanations, and each predicts something:

* Steep ground, really driven: high recorded tilt at nearby stops.
* Map overstates the slope: low recorded tilt, even though the map shows steep ground.
* Position error: drivable ground only a few meters from the recorded path.
* Limit too strict: nothing wrong with the data, and the rover simply handled this slope.

Tilt is only recorded when the rover stops, so a low reading can't rule out high tilt during a drive.

Run from the project folder: python -m scripts.investigate_steep_drives
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import ListedColormap
from rasterio.enums import Resampling
from rasterio.windows import Window
from scipy import ndimage

from mars_rover.rover.hazards import HazardClass
from mars_rover.sources import DTM_URL, ORTHO_URL
from mars_rover.terrain.source import TerrainSource
from mars_rover.terrain.visualize import MAGNITUDE, SURFACE, TEXT_PRIMARY, TEXT_SECONDARY
from scripts.check_hazards_against_route import load_drives
from scripts.route_terrain_stats import read_cells

DERIVED = Path("data/derived")
DTM_PATH = Path("data/raw") / DTM_URL.rsplit("/", 1)[-1]
MANIFEST = Path("data/raw/MANIFEST.json")
FIGURE = Path("figures/steep_drives_sols_474_501.png")
FOCUS_SOLS = {"474", "477", "501"}
ROUTE_COLOR = "#eb6834"
OFF_LIMITS_COLOR = "#0d366b"
DISPLAY_PAD_M = 30
LABEL_OFFSETS_M = {"474": (-24, 12), "477": (12, 10), "501": (16, -12)}


def distance_to_drivable(hazard, rows: np.ndarray, cols: np.ndarray, pad: int = 200) -> np.ndarray:
    """Return how far each cell is from the nearest cell that isn't off-limits, in cells."""
    top = max(int(rows.min()) - pad, 0)
    left = max(int(cols.min()) - pad, 0)
    bottom = min(int(rows.max()) + pad + 1, hazard.height)
    right = min(int(cols.max()) + pad + 1, hazard.width)
    block = hazard.read(1, window=Window(left, top, right - left, bottom - top))
    distance = ndimage.distance_transform_edt(block == HazardClass.OFF_LIMITS)
    return distance[rows - top, cols - left]


def load_stops(source: TerrainSource, path: str) -> dict[str, dict]:
    stops = {}
    for feature in json.loads(Path(path).read_text(encoding="utf-8"))["features"]:
        props = feature["properties"]
        row, col = source.lonlat_to_cell(float(props["lon"]), float(props["lat"]))
        stops[props["RMC"]] = {
            "sol": props["sol"],
            "tilt": float(props["tilt"]),
            "row": row,
            "col": col,
            "inside": 0 <= row < source.height and 0 <= col < source.width,
        }
    return stops


def report_flagged_drives(source, drives, stops, slope, hazard) -> list[dict]:
    print("Drives touching off-limits ground:\n")
    print("sol   | off-limits points | steepest cell | steepest in 3x3 | farthest from drivable | tilt at start, end")
    flagged = []
    for drive in drives:
        if drive["rows"].size == 0:
            continue
        classes = read_cells(hazard, drive["rows"], drive["cols"]).astype(np.uint8)
        off = classes == HazardClass.OFF_LIMITS
        if not off.any():
            continue
        rows, cols = drive["rows"][off], drive["cols"][off]
        cell = read_cells(slope, rows, cols)
        worst = read_cells(slope, rows, cols, footprint=True)
        reach = distance_to_drivable(hazard, rows, cols)
        start = stops.get(drive["from_rmc"], {}).get("tilt", float("nan"))
        end = stops.get(drive["to_rmc"], {}).get("tilt", float("nan"))
        print(
            f"{drive['sol']:<5} | {int(off.sum()):3d} of {classes.size:3d}        | "
            f"{np.nanmax(cell):10.1f} deg | {np.nanmax(worst):12.1f} deg | {reach.max():18.1f} m   | "
            f"{start:5.1f}, {end:5.1f} deg"
        )
        flagged.append(drive)
    return flagged


def report_tilt_vs_slope(stops, slope, hazard) -> None:
    """Compare map slope and recorded tilt at every stop on the map."""
    inside = [stop for stop in stops.values() if stop["inside"]]
    rows = np.array([stop["row"] for stop in inside])
    cols = np.array([stop["col"] for stop in inside])
    tilt = np.array([stop["tilt"] for stop in inside])
    center = read_cells(slope, rows, cols)
    worst = read_cells(slope, rows, cols, footprint=True)
    known = np.isfinite(center) & np.isfinite(worst)
    tilt, center, worst = tilt[known], center[known], worst[known]

    print(f"\nRecorded tilt vs. map slope at {tilt.size} stops with terrain data:")
    print(f"  correlation with the slope under the stop: {np.corrcoef(tilt, center)[0, 1]:.3f}")
    print(f"  correlation with the steepest in 3x3 m:    {np.corrcoef(tilt, worst)[0, 1]:.3f}")
    print("  steepest map slope in 3x3 m | stops | mean tilt | highest tilt | mean of (map - tilt)")
    edges = [0, 10, 15, 20, 25, 90]
    for low, high in zip(edges[:-1], edges[1:]):
        members = (worst >= low) & (worst < high)
        if members.any():
            print(
                f"  {low:>3} to {high:<3} deg              | {int(members.sum()):5d} | "
                f"{tilt[members].mean():7.1f}   | {tilt[members].max():10.1f}   | "
                f"{(worst[members] - tilt[members]).mean():+8.1f} deg"
            )
    classes = read_cells(hazard, rows[known], cols[known]).astype(np.uint8)
    print(f"  stops the model marks off-limits: {int(np.sum(classes == HazardClass.OFF_LIMITS))}")


def draw_figure(source, flagged, stops, slope) -> None:
    focus = [drive for drive in flagged if str(drive["sol"]) in FOCUS_SOLS]
    rows = np.concatenate([d["rows"] for d in focus])
    cols = np.concatenate([d["cols"] for d in focus])
    top = max(int(rows.min()) - DISPLAY_PAD_M, 0)
    left = max(int(cols.min()) - DISPLAY_PAD_M, 0)
    height = int(rows.max()) + DISPLAY_PAD_M - top
    width = int(cols.max()) + DISPLAY_PAD_M - left
    print(f"\nFigure area: {width} x {height} m")
    extent = (0, width, 0, height)

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"), rasterio.open(f"/vsicurl/{ORTHO_URL}") as ortho:
        scale = ortho.width / source.width
        image = ortho.read(
            1,
            window=Window(left * scale, top * scale, width * scale, height * scale),
            out_shape=(min(int(height * scale), 1400), min(int(width * scale), 1400)),
            resampling=Resampling.bilinear,
        )
    local_slope = slope.read(1, window=Window(left, top, width, height))
    with rasterio.open(DERIVED / "hazard_class.tif") as hazard:
        local_class = hazard.read(1, window=Window(left, top, width, height))

    fig, axes = plt.subplots(1, 2, figsize=(14, 7), facecolor=SURFACE, layout="constrained")
    low, high = np.percentile(image[image > 0], [1, 99]) if (image > 0).any() else (0, 255)
    axes[0].imshow(image, cmap="gray", vmin=low, vmax=high, extent=extent, interpolation="bilinear")
    axes[0].set_title("Orbital photo, 25 cm per pixel", loc="left", color=TEXT_PRIMARY, fontsize=12)
    shown = axes[1].imshow(
        np.ma.masked_invalid(local_slope), cmap=MAGNITUDE, vmin=0, vmax=35, extent=extent, interpolation="nearest"
    )
    overlay = np.ma.masked_where(local_class != HazardClass.OFF_LIMITS, local_class)
    axes[1].imshow(
        overlay, cmap=ListedColormap([OFF_LIMITS_COLOR]), extent=extent, interpolation="nearest", alpha=0.55
    )
    axes[1].set_title("Map slope, darkest areas are off-limits", loc="left", color=TEXT_PRIMARY, fontsize=12)
    bar = fig.colorbar(shown, ax=axes[1], fraction=0.04, pad=0.02, extend="max")
    bar.outline.set_visible(False)
    bar.ax.tick_params(colors=TEXT_SECONDARY, labelsize=8, length=0)
    bar.set_label("slope in degrees", color=TEXT_SECONDARY, fontsize=9)

    for ax in axes:
        for drive in focus:
            x = drive["cols"] - left + 0.5
            y = height - (drive["rows"] - top + 0.5)
            ax.plot(x, y, color=SURFACE, linewidth=3.2)
            ax.plot(x, y, color=ROUTE_COLOR, linewidth=1.8)
            middle = (x[len(x) // 2], y[len(y) // 2])
            offset = LABEL_OFFSETS_M[str(drive["sol"])]
            ax.annotate(
                f"sol {drive['sol']}",
                xy=middle,
                xytext=(middle[0] + offset[0], middle[1] + offset[1]),
                fontsize=9,
                color=TEXT_PRIMARY,
                bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1.5},
                arrowprops={"arrowstyle": "-", "color": SURFACE, "linewidth": 1},
            )
        for stop in stops.values():
            if stop["inside"] and left <= stop["col"] < left + width and top <= stop["row"] < top + height:
                x, y = stop["col"] - left + 0.5, height - (stop["row"] - top + 0.5)
                ax.plot(x, y, "o", color=ROUTE_COLOR, markeredgecolor=SURFACE, markersize=8)
                ax.annotate(f"{stop['tilt']:.0f}\N{DEGREE SIGN}", (x, y), xytext=(6, 6), textcoords="offset points",
                            fontsize=8, color=TEXT_PRIMARY,
                            bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1})
        ax.set_xlim(0, width)
        ax.set_ylim(0, height)
        ax.set_xlabel("meters east", color=TEXT_SECONDARY, fontsize=9)
        ax.tick_params(colors=TEXT_SECONDARY, labelsize=8, length=2)
        for spine in ax.spines.values():
            spine.set_visible(False)
    axes[0].set_ylabel("meters north", color=TEXT_SECONDARY, fontsize=9)
    fig.suptitle(
        "Drives on sols 474, 477 and 501. Orange lines are the route, and dots are stops with recorded tilt",
        x=0.01, ha="left", fontsize=13, color=TEXT_PRIMARY,
    )
    FIGURE.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE, dpi=120, facecolor=SURFACE)
    plt.close(fig)
    print(f"Saved: {FIGURE}")


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths = {record["name"]: record["path"] for record in manifest["routes"]}
    with TerrainSource(DTM_PATH) as source:
        drives = load_drives(source, paths["traverse"])
        stops = load_stops(source, paths["waypoints"])
        with rasterio.open(DERIVED / "slope_deg.tif") as slope, rasterio.open(
            DERIVED / "hazard_class.tif"
        ) as hazard:
            flagged = report_flagged_drives(source, drives, stops, slope, hazard)
            report_tilt_vs_slope(stops, slope, hazard)
            draw_figure(source, flagged, stops, slope)


if __name__ == "__main__":
    main()
