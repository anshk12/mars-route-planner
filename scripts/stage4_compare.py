"""Compare planned routes and straight lines with real drives.

By default this only uses the development set. The test set in docs/stage4_split.json stays locked until limits and
weights are final, and running it needs an explicit --unlock-test flag.

A planner earns credit only by beating a straight line between the same two points. Every comparison is paired by
segment, and uncertainty comes from resampling whole blocks, because segments in one block aren't independent.

Run from the project folder: python -m scripts.stage4_compare
"""

from __future__ import annotations

import argparse
import csv
import json
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from rasterio.windows import Window

from mars_rover.evaluation.segments import (
    Segment,
    assign_blocks,
    path_distance,
    segments_by_distance,
    segments_by_pause,
    straight_line,
)
from mars_rover.planner.loading import load_grid, score_path
from mars_rover.planner.search import BALANCED, CAUTIOUS, SHORTEST, nearest_passable, plan_route, smooth_route
from mars_rover.sources import DTM_URL
from mars_rover.terrain.source import TerrainSource
from scripts.check_hazards_against_route import load_drives

DERIVED = Path("data/derived")
DTM_PATH = Path("data/raw") / DTM_URL.rsplit("/", 1)[-1]
MANIFEST = Path("data/raw/MANIFEST.json")
SPLIT = Path("docs/stage4_split.json")
PREFERENCES = {"shortest": SHORTEST, "balanced": BALANCED, "cautious": CAUTIOUS}
PLANNERS = ("straight", "shortest", "shortest+", "balanced", "balanced+", "cautious", "cautious+")
PAD_M = 200
BOOTSTRAPS = 2000
DIRECT_MAX_DETOUR = 1.2
FIELDS = [
    "segment", "method", "block", "first_sol", "last_sol", "planner", "found", "distance_m", "length_m",
    "real_length_m", "detour", "tricky", "climb_m", "real_climb_m", "energy_ratio", "real_energy_ratio",
    "mean_penalty", "real_mean_penalty", "real_blocked", "nodes",
]


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
    real = score_path(grid, real_rows, real_cols)

    start = nearest_passable(grid, (int(real_rows[0]), int(real_cols[0])))
    goal = nearest_passable(grid, (int(real_rows[-1]), int(real_cols[-1])))
    base = {
        "segment": segment_id, "method": segment.method, "block": segment.block, "first_sol": segment.first_sol,
        "last_sol": segment.last_sol, "real_length_m": real.length_m, "real_climb_m": real.climb_m,
        "real_energy_ratio": real.energy_ratio, "real_mean_penalty": real.mean_penalty,
        "real_blocked": real.blocked_cells,
    }
    if start is None or goal is None or start == goal:
        return [{**base, "planner": "skipped", "found": 0}]

    line_rows, line_cols = straight_line(start, goal)
    line = score_path(grid, line_rows, line_cols)
    tricky = int(line.blocked_cells > 0)
    detour = real.length_m / max(line.length_m, 1e-9)
    shared = {"detour": detour, "tricky": tricky}

    def row_for(name: str, rows, cols, nodes: int) -> dict:
        score = score_path(grid, rows, cols)
        return {
            **base, **shared, "planner": name, "found": 1,
            "distance_m": path_distance(rows, cols, real_rows, real_cols), "length_m": score.length_m,
            "climb_m": score.climb_m, "energy_ratio": score.energy_ratio, "mean_penalty": score.mean_penalty,
            "nodes": nodes,
        }

    results = [row_for("straight", line_rows, line_cols, 0)]
    for name, prefs in PREFERENCES.items():
        route = plan_route(grid, start, goal, prefs)
        if not route.found:
            results.append({**base, **shared, "planner": name, "found": 0})
            results.append({**base, **shared, "planner": f"{name}+", "found": 0})
            continue
        rows, cols = (np.array(values) for values in zip(*route.cells))
        results.append(row_for(name, rows, cols, route.nodes_expanded))
        smoothed = smooth_route(grid, route, prefs)
        rows, cols = (np.array(values) for values in zip(*smoothed.cells))
        results.append(row_for(f"{name}+", rows, cols, route.nodes_expanded))
    return results


def summarize(rows: list[dict], label: str) -> None:
    print(f"\n=== {label} ===")
    measured = [r for r in rows if r["planner"] != "skipped"]
    if not measured:
        print("  no segments")
        return
    segment_count = len({r["segment"] for r in measured})
    print(f"  {segment_count} segments in {len({r['block'] for r in measured})} blocks")
    straight = {r["segment"]: float(r["distance_m"]) for r in measured if r["planner"] == "straight"}
    print("  planner   | found   | median distance from real | length vs. real | closer than straight | median gain, 95% range")
    rng = np.random.default_rng(2020)
    for name in PLANNERS:
        attempts = [r for r in measured if r["planner"] == name]
        members = [r for r in attempts if int(r["found"])]
        if not members:
            print(f"  {name:<9} | 0/{len(attempts)}")
            continue
        distance = np.array([float(r["distance_m"]) for r in members])
        ratio = np.array([float(r["length_m"]) / float(r["real_length_m"]) for r in members])
        line = (
            f"  {name:<9} | {len(members):3d}/{len(attempts):<3d} | {np.median(distance):22.1f} m  | "
            f"{np.median(ratio):14.3f}  |"
        )
        if name != "straight":
            gains = np.array([straight[r["segment"]] - float(r["distance_m"]) for r in members])
            blocks = np.array([r["block"] for r in members])
            unique = np.unique(blocks)
            groups = {block: gains[blocks == block] for block in unique}
            medians = [
                np.median(np.concatenate([groups[pick] for pick in rng.choice(unique, unique.size, replace=True)]))
                for _ in range(BOOTSTRAPS)
            ]
            low, high = np.percentile(medians, [2.5, 97.5])
            line += f" {100 * np.mean(gains > 0):18.0f}%  | {np.median(gains):+.1f} m ({low:+.1f} to {high:+.1f})"
        print(line)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unlock-test", action="store_true", help="evaluate the locked test set. Only do this once.")
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()

    split = json.loads(SPLIT.read_text(encoding="utf-8"))
    locked = set(split["test_blocks"])
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths = {record["name"]: record["path"] for record in manifest["routes"]}
    with TerrainSource(DTM_PATH) as source:
        drives = load_drives(source, paths["traverse"])
    blocks = assign_blocks(drives, split["block_m"])

    segments = segments_by_pause(drives, blocks) + segments_by_distance(drives, blocks)
    use_block = (lambda block: block in locked) if args.unlock_test else (lambda block: block not in locked)
    chosen = [segment for segment in segments if use_block(segment.block)]
    set_name = "test" if args.unlock_test else "development"
    print(f"Evaluating the {set_name} set: {len(chosen)} segments", flush=True)

    out = DERIVED / f"stage4_{set_name}.csv"
    results = []
    with Pool(args.workers) as pool, out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for index, rows in enumerate(pool.imap_unordered(evaluate, list(enumerate(chosen))), start=1):
            writer.writerows(rows)
            handle.flush()
            results.extend(rows)
            if index % 50 == 0 or index == len(chosen):
                print(f"  {index}/{len(chosen)}", flush=True)

    for method, title in (("pause", "Cut at long pauses"), ("distance", "Cut every 200 m")):
        rows = [r for r in results if r["method"] == method]
        summarize(rows, f"{title}: all segments")
        tricky = {r["segment"] for r in rows if r["planner"] == "straight" and int(r["tricky"])}
        summarize([r for r in rows if r["segment"] in tricky], f"{title}: only where a straight line hits blocked ground")
        direct = {
            r["segment"] for r in rows if r["planner"] == "straight" and float(r["detour"]) <= DIRECT_MAX_DETOUR
        }
        summarize(
            [r for r in rows if r["segment"] in direct],
            f"{title}: only fairly direct drives, real route at most {DIRECT_MAX_DETOUR} times the straight line",
        )
    skipped = sum(1 for r in results if r["planner"] == "skipped")
    print(f"\nSkipped because an endpoint had no drivable ground nearby: {skipped}")
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
