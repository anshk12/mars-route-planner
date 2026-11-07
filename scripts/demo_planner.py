"""Plan real routes across Jezero and compare them with Perseverance's actual path.

This is a first look, not Stage 4's systematic comparison. It plans between pairs of real stops on one long stretch of
the route and a sample of single drives, with three sets of preferences. All the preference weights are provisional.

Run from the project folder: python -m scripts.demo_planner
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from rasterio.windows import Window

from mars_rover.planner.loading import load_grid, score_path
from mars_rover.planner.search import BALANCED, CAUTIOUS, SHORTEST, nearest_passable, plan_route
from mars_rover.sources import DTM_URL
from mars_rover.terrain.source import TerrainSource
from mars_rover.terrain.visualize import MAGNITUDE, SURFACE, TEXT_PRIMARY, TEXT_SECONDARY
from scripts.check_hazards_against_route import load_drives

DERIVED = Path("data/derived")
DTM_PATH = Path("data/raw") / DTM_URL.rsplit("/", 1)[-1]
MANIFEST = Path("data/raw/MANIFEST.json")
FIGURE = Path("figures/planned_vs_real_route.png")
PREFERENCES = {"shortest": SHORTEST, "balanced": BALANCED, "cautious": CAUTIOUS}
PAD_M = 300
LONG_STRETCH_TARGET_M = 2500
SINGLE_DRIVE_SAMPLE = 40
# Colors for planned routes, plus the real route, in the validated categorical order.
ROUTE_COLORS = {"real": "#0b0b0b", "shortest": "#2a78d6", "balanced": "#eb6834", "cautious": "#1baf7a"}


def bounding_window(rows, cols, height, width, pad=PAD_M) -> Window:
    top = max(int(np.min(rows)) - pad, 0)
    left = max(int(np.min(cols)) - pad, 0)
    bottom = min(int(np.max(rows)) + pad + 1, height)
    right = min(int(np.max(cols)) + pad + 1, width)
    return Window(left, top, right - left, bottom - top)


def endpoints(grid, rows, cols):
    """Move the real start and end to the nearest usable cells, if needed."""
    start = nearest_passable(grid, (int(rows[0]), int(cols[0])))
    goal = nearest_passable(grid, (int(rows[-1]), int(cols[-1])))
    return start, goal


def describe(label, score, seconds=None, nodes=None, method="") -> None:
    extra = f"  {seconds:6.2f} s, {nodes:>9,} nodes, {method}" if seconds is not None else ""
    print(
        f"  {label:<9} {score.length_m:8.0f} m | climb {score.climb_m:6.1f} m | energy x{score.energy_ratio:5.2f} | "
        f"hazard {score.mean_penalty:5.2f} | worst grade {score.worst_grade_deg:4.1f} deg | "
        f"blocked {score.blocked_cells:3d}{extra}"
    )


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths = {record["name"]: record["path"] for record in manifest["routes"]}
    with TerrainSource(DTM_PATH) as source:
        drives = [d for d in load_drives(source, paths["traverse"]) if d["rows"].size >= 2]
        height, width = source.height, source.width

    # A long stretch: join consecutive drives from the start, stopping once it's long enough.
    stretch_rows, stretch_cols, total = [], [], 0.0
    for drive in drives:
        stretch_rows.append(drive["rows"])
        stretch_cols.append(drive["cols"])
        total += drive["length"]
        if total >= LONG_STRETCH_TARGET_M:
            break
    rows = np.concatenate(stretch_rows)
    cols = np.concatenate(stretch_cols)

    window = bounding_window(rows, cols, height, width)
    started = time.perf_counter()
    grid = load_grid(DERIVED, window)
    top, left = grid.origin
    print(f"Long stretch: {len(stretch_rows)} drives, grid {grid.shape[1]} x {grid.shape[0]} cells, "
          f"loaded in {time.perf_counter() - started:.1f} s, {100 * grid.passable.mean():.1f}% drivable")
    local_rows, local_cols = rows - top, cols - left
    start, goal = endpoints(grid, local_rows, local_cols)
    describe("real", score_path(grid, local_rows, local_cols))
    planned = {}
    for name, prefs in PREFERENCES.items():
        started = time.perf_counter()
        route = plan_route(grid, start, goal, prefs)
        seconds = time.perf_counter() - started
        if not route.found:
            print(f"  {name:<9} no route: {route.reason}")
            continue
        planned[name] = route
        r, c = zip(*route.cells)
        describe(name, score_path(grid, np.array(r), np.array(c)), seconds, route.nodes_expanded, route.method)

    rng = np.random.default_rng(2020)
    long_enough = [d for d in drives if d["length"] >= 20]
    picks = rng.choice(len(long_enough), size=min(SINGLE_DRIVE_SAMPLE, len(long_enough)), replace=False)
    print(f"\nSingle drives ({picks.size} random drives of at least 20 m): planned length vs. real length")
    print("preferences | routes found | median planned / real length | median distance from real path")
    results = {name: {"ratio": [], "gap": [], "found": 0} for name in ("straight", *PREFERENCES)}
    attempts = 0
    for pick in picks:
        drive = long_enough[int(pick)]
        window_local = bounding_window(drive["rows"], drive["cols"], height, width, pad=100)
        local = load_grid(DERIVED, window_local)
        r_real = drive["rows"] - local.origin[0]
        c_real = drive["cols"] - local.origin[1]
        real_score = score_path(local, r_real, c_real)
        from_cell, to_cell = endpoints(local, r_real, c_real)
        if from_cell is None or to_cell is None or real_score.length_m == 0:
            continue
        attempts += 1
        real_points = np.column_stack((r_real, c_real)).astype(np.float64)
        # A straight line is the baseline: a planner has to beat this to add anything.
        steps = max(abs(to_cell[0] - from_cell[0]), abs(to_cell[1] - from_cell[1]), 1) + 1
        line = np.column_stack(
            (np.linspace(from_cell[0], to_cell[0], steps), np.linspace(from_cell[1], to_cell[1], steps))
        )
        line_rows, line_cols = np.rint(line).astype(np.int64).T
        line_score = score_path(local, line_rows, line_cols)
        results["straight"]["found"] += 1
        results["straight"]["ratio"].append(line_score.length_m / real_score.length_m)
        results["straight"]["gap"].append(
            float(np.median(np.min(np.linalg.norm(line[:, None, :] - real_points[None, :, :], axis=2), axis=1)))
        )
        results["straight"]["blocked"] = results["straight"].get("blocked", 0) + (line_score.blocked_cells > 0)
        for name, prefs in PREFERENCES.items():
            route = plan_route(local, from_cell, to_cell, prefs)
            if not route.found:
                continue
            results[name]["found"] += 1
            r, c = zip(*route.cells)
            score = score_path(local, np.array(r), np.array(c))
            results[name]["ratio"].append(score.length_m / real_score.length_m)
            cells = np.column_stack((r, c)).astype(np.float64)
            gaps = np.min(np.linalg.norm(cells[:, None, :] - real_points[None, :, :], axis=2), axis=1)
            results[name]["gap"].append(float(np.median(gaps)))
    for name, values in results.items():
        print(
            f"{name:<11} | {values['found']:3d} of {attempts:3d}   | {np.median(values['ratio']):27.3f} | "
            f"{np.median(values['gap']):8.1f} m"
        )
    print(f"Straight lines crossing off-limits or unknown ground: {results['straight'].get('blocked', 0)} of {attempts}")

    extent = (0, grid.shape[1] / 1000, 0, grid.shape[0] / 1000)
    figure_width = 8.5
    figure_height = figure_width * grid.shape[0] / grid.shape[1] + 0.3
    fig, ax = plt.subplots(figsize=(figure_width, figure_height), facecolor=SURFACE, layout="constrained")
    with __import__("rasterio").open(DERIVED / "slope_deg.tif") as dataset:
        slope = dataset.read(1, window=Window(left, top, grid.shape[1], grid.shape[0]))
    image = ax.imshow(np.ma.masked_invalid(slope), cmap=MAGNITUDE, vmin=0, vmax=30, extent=extent, interpolation="nearest")

    def to_km(path_rows, path_cols):
        return (np.asarray(path_cols) + 0.5) / 1000, (grid.shape[0] - (np.asarray(path_rows) + 0.5)) / 1000

    x, y = to_km(local_rows, local_cols)
    ax.plot(x, y, color=SURFACE, linewidth=4)
    ax.plot(x, y, color=ROUTE_COLORS["real"], linewidth=2.2, label="Perseverance's real route")
    for name, route in planned.items():
        r, c = zip(*route.cells)
        x, y = to_km(r, c)
        ax.plot(x, y, color=SURFACE, linewidth=3.6)
        ax.plot(x, y, color=ROUTE_COLORS[name], linewidth=1.8, label=f"Planned: {name}")
    ax.legend(loc="upper right", frameon=True, facecolor=SURFACE, edgecolor="none", fontsize=10)
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    fig.suptitle("Planned and real routes between the same start and end", x=0.02, ha="left",
                 fontsize=13, color=TEXT_PRIMARY)
    ax.set_xlabel("km east", color=TEXT_SECONDARY, fontsize=9)
    ax.set_ylabel("km north", color=TEXT_SECONDARY, fontsize=9)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=8, length=2)
    for spine in ax.spines.values():
        spine.set_visible(False)
    bar = fig.colorbar(image, ax=ax, fraction=0.03, pad=0.02, extend="max")
    bar.outline.set_visible(False)
    bar.ax.tick_params(colors=TEXT_SECONDARY, labelsize=8, length=0)
    bar.set_label("slope in degrees", color=TEXT_SECONDARY, fontsize=9)
    FIGURE.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    print(f"\nSaved: {FIGURE}")


if __name__ == "__main__":
    main()
