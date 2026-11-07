"""Check whether real drives avoid what the model measures, compared with a straight line.

If real routes have lower hazard or energy than a straight line between the same points, drivers may be avoiding what
the model measures, and better weights could help. If not, orbital measurements may not explain their choices.

Uses the development set results from scripts/stage4_compare.py. Uncertainty comes from resampling whole blocks.

Run from the project folder: python -m scripts.stage4_real_vs_line
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

RESULTS = Path("data/derived/stage4_development.csv")
BOOTSTRAPS = 5000


def compare(rows: list[dict], real_key: str, line_key: str) -> str:
    real = np.array([float(r[real_key]) for r in rows])
    line = np.array([float(r[line_key]) for r in rows])
    blocks = np.array([r["block"] for r in rows])
    unique = np.unique(blocks)
    groups = {block: np.flatnonzero(blocks == block) for block in unique}
    rng = np.random.default_rng(2020)
    medians = []
    for _ in range(BOOTSTRAPS):
        picks = np.concatenate([groups[block] for block in rng.choice(unique, unique.size, replace=True)])
        medians.append(np.median(real[picks] - line[picks]))
    low, high = np.percentile(medians, [2.5, 97.5])
    return (
        f"real median {np.median(real):7.3f} | straight median {np.median(line):7.3f} | "
        f"real lower in {100 * np.mean(real < line):3.0f}% | median difference {np.median(real - line):+.3f} "
        f"({low:+.3f} to {high:+.3f})"
    )


def main() -> None:
    with RESULTS.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["planner"] == "straight" and int(row["found"])]
    for method, title in (("pause", "Cut at long pauses"), ("distance", "Cut every 200 m")):
        members = [row for row in rows if row["method"] == method]
        print(f"\n{title} ({len(members)} segments)")
        print(f"  hazard penalty per cell: {compare(members, 'real_mean_penalty', 'mean_penalty')}")
        print(f"  energy vs. flat ground:  {compare(members, 'real_energy_ratio', 'energy_ratio')}")
        direct = [row for row in members if float(row["detour"]) <= 1.2]
        print(f"  fairly direct only ({len(direct)} segments):")
        print(f"    hazard penalty per cell: {compare(direct, 'real_mean_penalty', 'mean_penalty')}")
        print(f"    energy vs. flat ground:  {compare(direct, 'real_energy_ratio', 'energy_ratio')}")


if __name__ == "__main__":
    main()
