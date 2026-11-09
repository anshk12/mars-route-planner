"""Estimate what the real drives favored, by trying many preference weights.

Each set of weights plans a route on every development segment, and a set scores well if its routes land closer to the
real path than a straight line does. Results are saved after each segment, so a stopped run picks up where it left off.
The locked test blocks are never used here.

Run from the project folder: python -m scripts.stage5_fit_weights
"""

from __future__ import annotations

import argparse
import csv
import json
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from rasterio.windows import Window

from mars_rover.evaluation.fitting import cross_validate, make_folds, median_range
from mars_rover.evaluation.segments import (
    Segment,
    assign_blocks,
    path_distance,
    segments_by_distance,
    segments_by_pause,
    straight_line,
)
from mars_rover.planner.loading import load_grid
from mars_rover.planner.search import RoutePreferences, nearest_passable, plan_route, smooth_route
from mars_rover.sources import DTM_URL
from mars_rover.terrain.source import TerrainSource
from scripts.check_hazards_against_route import load_drives

DERIVED = Path("data/derived")
DTM_PATH = Path("data/raw") / DTM_URL.rsplit("/", 1)[-1]
MANIFEST = Path("data/raw/MANIFEST.json")
SPLIT = Path("docs/stage4_split.json")
RESULTS = DERIVED / "stage5_weight_grid.csv"
PAD_M = 200
ENERGY_WEIGHTS = (0.0, 1.0, 3.0)
HAZARD_WEIGHTS = (0.0, 1.0, 3.0, 10.0, 30.0)
FIELDS = ["segment", "method", "block", "energy", "hazard", "found", "distance_m", "straight_m", "tricky"]


def evaluate(item: tuple[int, Segment]) -> list[dict]:
    segment_id, segment = item
    with TerrainSource(DERIVED / "slope_deg.tif") as source:
        height, width = source.height, source.width
    top = max(int(segment.rows.min()) - PAD_M, 0)
    left = max(int(segment.cols.min()) - PAD_M, 0)
    bottom = min(int(segment.rows.max()) + PAD_M + 1, height)
    right = min(int(segment.cols.max()) + PAD_M + 1, width)
    grid = load_grid(DERIVED, Window(left, top, right - left, bottom - top))
    real_rows, real_cols = segment.rows - top, segment.cols - left
    start = nearest_passable(grid, (int(real_rows[0]), int(real_cols[0])))
    goal = nearest_passable(grid, (int(real_rows[-1]), int(real_cols[-1])))
    base = {"segment": segment_id, "method": segment.method, "block": segment.block}
    if start is None or goal is None or start == goal:
        return [{**base, "energy": "", "hazard": "", "found": 0}]

    line_rows, line_cols = straight_line(start, goal)
    straight = path_distance(line_rows, line_cols, real_rows, real_cols)
    tricky = int(not grid.passable[line_rows, line_cols].all())
    rows_out = []
    for energy in ENERGY_WEIGHTS:
        for hazard in HAZARD_WEIGHTS:
            prefs = RoutePreferences(distance=1.0, energy=energy, hazard=hazard)
            route = smooth_route(grid, plan_route(grid, start, goal, prefs), prefs)
            row = {**base, "energy": energy, "hazard": hazard, "found": int(route.found), "straight_m": straight,
                   "tricky": tricky}
            if route.found:
                rows, cols = (np.array(values) for values in zip(*route.cells))
                row["distance_m"] = path_distance(rows, cols, real_rows, real_cols)
            rows_out.append(row)
    return rows_out


def run_grid(workers: int) -> None:
    split = json.loads(SPLIT.read_text(encoding="utf-8"))
    locked = set(split["test_blocks"])
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths = {record["name"]: record["path"] for record in manifest["routes"]}
    with TerrainSource(DTM_PATH) as source:
        drives = load_drives(source, paths["traverse"])
    blocks = assign_blocks(drives, split["block_m"])
    segments = segments_by_pause(drives, blocks) + segments_by_distance(drives, blocks)
    chosen = [segment for segment in segments if segment.block not in locked]

    done = set()
    if RESULTS.exists():
        with RESULTS.open(newline="", encoding="utf-8") as handle:
            done = {int(row["segment"]) for row in csv.DictReader(handle)}
    todo = [(index, segment) for index, segment in enumerate(chosen) if index not in done]
    print(f"{len(chosen)} development segments, {len(done)} already done, {len(todo)} to go", flush=True)
    if not todo:
        return
    is_new = not RESULTS.exists()
    with Pool(workers) as pool, RESULTS.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        for count, rows in enumerate(pool.imap_unordered(evaluate, todo), start=1):
            writer.writerows(rows)
            handle.flush()
            if count % 20 == 0 or count == len(todo):
                print(f"  {count}/{len(todo)}", flush=True)


def analyze() -> None:
    with RESULTS.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["energy"] != ""]

    for method, title in (("pause", "Cut at long pauses"), ("distance", "Cut every 200 m")):
        members = [row for row in rows if row["method"] == method]
        segment_ids = sorted({int(row["segment"]) for row in members})
        index = {segment: position for position, segment in enumerate(segment_ids)}
        blocks = np.zeros(len(segment_ids), dtype=np.int64)
        tricky = np.zeros(len(segment_ids), dtype=bool)
        gains: dict[str, np.ndarray] = {}
        for row in members:
            name = f"energy {float(row['energy']):g}, hazard {float(row['hazard']):g}"
            values = gains.setdefault(name, np.full(len(segment_ids), np.nan))
            position = index[int(row["segment"])]
            blocks[position] = int(row["block"])
            tricky[position] = bool(int(row["tricky"]))
            if int(row["found"]):
                values[position] = float(row["straight_m"]) - float(row["distance_m"])
        usable = np.all([np.isfinite(values) for values in gains.values()], axis=0)
        gains = {name: values[usable] for name, values in gains.items()}
        blocks = blocks[usable]
        tricky = tricky[usable]

        print(f"\n=== {title}: {blocks.size} segments in {np.unique(blocks).size} blocks ===")
        print("weights                  | median gain, 95% range      | closer than straight")
        ranked = sorted(gains, key=lambda name: float(np.median(gains[name])), reverse=True)
        for name in ranked:
            median, low, high = median_range(gains[name], blocks)
            print(f"{name:<24} | {median:+6.2f} m ({low:+6.2f} to {high:+6.2f}) | {100 * np.mean(gains[name] > 0):5.0f}%")

        folds = make_folds(blocks, folds=5)
        fair = cross_validate(gains, blocks, folds)
        median, low, high = median_range(fair.gains, fair.blocks)
        print(f"\nCross-validated gain, weights chosen without the blocks they're scored on:")
        print(f"  {median:+.2f} m ({low:+.2f} to {high:+.2f}), closer in {100 * np.mean(fair.gains > 0):.0f}% of segments")
        print(f"  weights chosen per fold: {fair.chosen}")

        if tricky.sum() >= 5:
            hard = {name: values[tricky] for name, values in gains.items()}
            hard_ranked = sorted(hard, key=lambda name: float(np.median(hard[name])), reverse=True)
            print(f"  where a straight line is blocked ({int(tricky.sum())} segments), best two by median gain:")
            for name in hard_ranked[:2]:
                print(f"    {name}: {float(np.median(hard[name])):+.2f} m (too few segments for a reliable range)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--analyze-only", action="store_true")
    args = parser.parse_args()
    if not args.analyze_only:
        run_grid(args.workers)
    analyze()


if __name__ == "__main__":
    main()
