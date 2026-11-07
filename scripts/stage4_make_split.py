"""Choose which parts of the real route to lock away, and save the choice.

Run this once, before looking at any Stage 4 results. The saved file is committed to Git so the split's date is on
record. Limits and weights get chosen using the development set only, and the test set gets one final evaluation.

Run from the project folder: python -m scripts.stage4_make_split
"""

from __future__ import annotations

import json
from pathlib import Path

from mars_rover.evaluation.segments import assign_blocks, choose_test_blocks
from mars_rover.sources import DTM_URL
from mars_rover.terrain.source import TerrainSource
from scripts.check_hazards_against_route import load_drives

DTM_PATH = Path("data/raw") / DTM_URL.rsplit("/", 1)[-1]
MANIFEST = Path("data/raw/MANIFEST.json")
SPLIT = Path("docs/stage4_split.json")
SEED = 2027
TEST_SHARE = 0.3
BLOCK_M = 1000.0


def main() -> None:
    if SPLIT.exists():
        raise SystemExit(f"{SPLIT} already exists. Changing the split after seeing results would defeat its purpose.")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths = {record["name"]: record["path"] for record in manifest["routes"]}
    with TerrainSource(DTM_PATH) as source:
        drives = load_drives(source, paths["traverse"])
    blocks = assign_blocks(drives, BLOCK_M)
    count = max(blocks) + 1
    test_blocks = choose_test_blocks(count, TEST_SHARE, SEED)

    summary = []
    for block in range(count):
        members = [drive for drive, assigned in zip(drives, blocks) if assigned == block]
        summary.append(
            {
                "block": block,
                "set": "test" if block in test_blocks else "development",
                "first_sol": int(float(members[0]["sol"])),
                "last_sol": int(float(members[-1]["sol"])),
                "drives": len(members),
                "length_m": round(sum(float(d["length"]) for d in members), 1),
            }
        )
    record = {
        "seed": SEED,
        "test_share": TEST_SHARE,
        "block_m": BLOCK_M,
        "route_files": [{"name": r["name"], "sha256": r["sha256"]} for r in manifest["routes"]],
        "test_blocks": test_blocks,
        "blocks": summary,
    }
    SPLIT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    test_length = sum(b["length_m"] for b in summary if b["set"] == "test")
    total = sum(b["length_m"] for b in summary)
    print(f"{count} blocks, {len(test_blocks)} locked as test: {test_length / 1000:.1f} of {total / 1000:.1f} km")
    print(f"Test blocks: {test_blocks}")
    print(f"Saved: {SPLIT}")


if __name__ == "__main__":
    main()
