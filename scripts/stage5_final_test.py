"""Run the one-time final test described in docs/stage5_test_plan.md.

This uses only the locked test blocks, with the weights and rules fixed before it ran. It refuses to run again once a
result exists, so the test can't be repeated until the answer looks good.

Run from the project folder: python -m scripts.stage5_final_test
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from rasterio.windows import Window

from mars_rover.evaluation.fitting import median_range
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
RESULT = Path("docs/stage5_test_result.json")
PREFS = RoutePreferences(distance=1.0, energy=1.0, hazard=30.0)
PAD_M = 200
TRIALS = 5000


def evaluate(segment: Segment) -> dict:
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
    result = {"method": segment.method, "block": segment.block, "first_sol": segment.first_sol,
              "last_sol": segment.last_sol}
    if start is None or goal is None or start == goal:
        return {**result, "status": "skipped"}
    line_rows, line_cols = straight_line(start, goal)
    straight = path_distance(line_rows, line_cols, real_rows, real_cols)
    route = smooth_route(grid, plan_route(grid, start, goal, PREFS), PREFS)
    if not route.found:
        return {**result, "status": "no route", "straight_m": straight}
    rows, cols = (np.array(values) for values in zip(*route.cells))
    planned = path_distance(rows, cols, real_rows, real_cols)
    return {
        **result, "status": "ok", "straight_m": straight, "planned_m": planned, "gain_m": straight - planned,
        "tricky": bool(not grid.passable[line_rows, line_cols].all()),
    }


def summarize(rows: list[dict]) -> dict:
    measured = [row for row in rows if row["status"] == "ok"]
    summary = {
        "segments": len(rows),
        "measured": len(measured),
        "skipped": sum(row["status"] == "skipped" for row in rows),
        "no_route": sum(row["status"] == "no route" for row in rows),
        "blocks": len({row["block"] for row in measured}),
    }
    if not measured:
        return summary
    gains = np.array([row["gain_m"] for row in measured])
    blocks = np.array([row["block"] for row in measured])
    median, low, high = median_range(gains, blocks, trials=TRIALS, seed=2020)
    verdict = "gain" if low > 0 else "worse than a straight line" if high < 0 else "no clear gain"
    tricky = [row["gain_m"] for row in measured if row["tricky"]]
    return {
        **summary,
        "median_straight_m": float(np.median([row["straight_m"] for row in measured])),
        "median_planned_m": float(np.median([row["planned_m"] for row in measured])),
        "median_gain_m": median,
        "gain_range_95_m": [low, high],
        "closer_than_straight": float(np.mean(gains > 0)),
        "verdict": verdict,
        "blocked_straight_segments": len(tricky),
        "blocked_straight_median_gain_m": float(np.median(tricky)) if tricky else None,
    }


def main() -> None:
    if RESULT.exists():
        raise SystemExit(f"{RESULT} already exists. The final test runs only once.")
    split = json.loads(SPLIT.read_text(encoding="utf-8"))
    locked = set(split["test_blocks"])
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths = {record["name"]: record["path"] for record in manifest["routes"]}
    with TerrainSource(DTM_PATH) as source:
        drives = load_drives(source, paths["traverse"])
    blocks = assign_blocks(drives, split["block_m"])
    segments = [s for s in segments_by_pause(drives, blocks) + segments_by_distance(drives, blocks) if s.block in locked]
    print(f"Test set: {len(segments)} segments from locked blocks {sorted(locked)}", flush=True)

    with Pool(6) as pool:
        rows = pool.map(evaluate, segments)

    record = {
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "plan": "docs/stage5_test_plan.md",
        "weights": {"distance": PREFS.distance, "energy": PREFS.energy, "hazard": PREFS.hazard},
        "main_long_pause": summarize([row for row in rows if row["method"] == "pause"]),
        "secondary_200m": summarize([row for row in rows if row["method"] == "distance"]),
        "segments": rows,
    }
    RESULT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    for key, title in (("main_long_pause", "MAIN: long-pause segments"), ("secondary_200m", "Secondary: 200 m segments")):
        print(f"\n{title}")
        for name, value in record[key].items():
            print(f"  {name}: {value}")
    print(f"\nSaved: {RESULT}")


if __name__ == "__main__":
    main()
