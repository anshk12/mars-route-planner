"""Divide the real route into test segments, and keep part of it locked away for a fair test.

Nearby stretches of route look alike, so test data is held back in whole blocks of consecutive drives. Picking single
drives at random would leave near-identical neighbors on both sides of the split.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.spatial import cKDTree

MAX_JUMP_CELLS = 20  # A bigger jump between drives means the two can't be joined into one path.


@dataclass(frozen=True)
class Segment:
    """One stretch of the real route, in full-map cells."""

    method: str
    block: int
    first_sol: int
    last_sol: int
    rows: NDArray[np.int64]
    cols: NDArray[np.int64]


def _path_length(rows: NDArray, cols: NDArray) -> float:
    return float(np.hypot(np.diff(rows), np.diff(cols)).sum())


def assign_blocks(drives: list[dict], block_m: float = 1000.0) -> list[int]:
    """Give each drive a block number, starting a new block about every ``block_m`` meters."""
    blocks, block, travelled = [], 0, 0.0
    for drive in drives:
        if travelled >= block_m:
            block += 1
            travelled = 0.0
        blocks.append(block)
        travelled += float(drive["length"])
    return blocks


def choose_test_blocks(block_count: int, test_share: float = 0.3, seed: int = 2027) -> list[int]:
    """Pick which blocks to lock away. A fixed seed makes the split reproducible."""
    if not 0 < test_share < 1:
        raise ValueError("test_share must be between 0 and 1")
    if block_count < 2:
        raise ValueError("need at least 2 blocks to split")
    count = max(1, round(block_count * test_share))
    picks = np.random.default_rng(seed).permutation(block_count)[:count]
    return sorted(int(pick) for pick in picks)


def _continuous_runs(drives: list[dict], blocks: list[int], max_sol_gap: int | None) -> list[list[int]]:
    """Group consecutive drives that can be joined into one continuous path."""
    runs: list[list[int]] = []
    current: list[int] = []
    previous = None
    for index, drive in enumerate(drives):
        if drive["rows"].size == 0:
            if current:
                runs.append(current)
            current, previous = [], None
            continue
        split = False
        if previous is not None:
            before = drives[previous]
            jump = np.hypot(drive["rows"][0] - before["rows"][-1], drive["cols"][0] - before["cols"][-1])
            gap = int(float(drive["sol"])) - int(float(before["sol"]))
            split = (
                blocks[index] != blocks[previous]
                or jump > MAX_JUMP_CELLS
                or (max_sol_gap is not None and gap >= max_sol_gap)
            )
        if split:
            runs.append(current)
            current = []
        current.append(index)
        previous = index
    if current:
        runs.append(current)
    return runs


def _join(drives: list[dict], run: list[int]) -> tuple[NDArray, NDArray, NDArray]:
    rows = np.concatenate([drives[i]["rows"] for i in run])
    cols = np.concatenate([drives[i]["cols"] for i in run])
    sols = np.concatenate([np.full(drives[i]["rows"].size, int(float(drives[i]["sol"]))) for i in run])
    return rows, cols, sols


def segments_by_pause(
    drives: list[dict], blocks: list[int], pause_sols: int = 3, min_length_m: float = 100.0
) -> list[Segment]:
    """Cut wherever the rover went ``pause_sols`` or more sols between drives."""
    segments = []
    for run in _continuous_runs(drives, blocks, pause_sols):
        rows, cols, sols = _join(drives, run)
        if _path_length(rows, cols) >= min_length_m:
            segments.append(Segment("pause", blocks[run[0]], int(sols[0]), int(sols[-1]), rows, cols))
    return segments


def segments_by_distance(
    drives: list[dict], blocks: list[int], length_m: float = 200.0, min_length_m: float = 100.0
) -> list[Segment]:
    """Cut the route into pieces of about ``length_m`` meters of driving."""
    segments = []
    for run in _continuous_runs(drives, blocks, None):
        rows, cols, sols = _join(drives, run)
        travelled = np.r_[0.0, np.cumsum(np.hypot(np.diff(rows), np.diff(cols)))]
        pieces = np.floor(travelled / length_m).astype(np.int64)
        for piece in np.unique(pieces):
            keep = np.flatnonzero(pieces == piece)
            # Share one point with the next piece, so no driving falls between pieces.
            end = min(int(keep[-1]) + 2, rows.size)
            part_rows, part_cols = rows[keep[0] : end], cols[keep[0] : end]
            if _path_length(part_rows, part_cols) >= min_length_m:
                segments.append(
                    Segment(
                        "distance",
                        blocks[run[0]],
                        int(sols[keep[0]]),
                        int(sols[end - 1]),
                        part_rows,
                        part_cols,
                    )
                )
    return segments


def straight_line(start: tuple[int, int], goal: tuple[int, int]) -> tuple[NDArray, NDArray]:
    """Return the cells along a straight line between two cells."""
    steps = max(abs(goal[0] - start[0]), abs(goal[1] - start[1]), 1) + 1
    rows = np.rint(np.linspace(start[0], goal[0], steps)).astype(np.int64)
    cols = np.rint(np.linspace(start[1], goal[1], steps)).astype(np.int64)
    return rows, cols


def path_distance(rows_a: NDArray, cols_a: NDArray, rows_b: NDArray, cols_b: NDArray) -> float:
    """Return the average distance between two paths, measured from both sides.

    Checking both directions matters. A short route that follows only part of the real path would look perfect if
    distance were measured from the short route alone.
    """
    a = np.column_stack((rows_a, cols_a)).astype(np.float64)
    b = np.column_stack((rows_b, cols_b)).astype(np.float64)
    from_a, _ = cKDTree(b).query(a)
    from_b, _ = cKDTree(a).query(b)
    return float((from_a.mean() + from_b.mean()) / 2.0)
