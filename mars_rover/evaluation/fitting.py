"""Choose preference weights from data, and estimate fairly how well the choice will work.

Checking many weights and reporting the best score would flatter the result, because some weights win by luck. So
weights are chosen using some blocks, and scored on different ones, and every block gets its turn being held out.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CrossValidation:
    """Results for segments scored by weights chosen without their block."""

    gains: np.ndarray
    blocks: np.ndarray
    chosen: list[str]  # The weights picked for each fold.


def make_folds(blocks: Sequence[int], folds: int = 5, seed: int = 2020) -> list[list[int]]:
    """Split block numbers into folds, keeping each whole block together."""
    unique = np.unique(np.asarray(blocks))
    if folds < 2 or folds > unique.size:
        raise ValueError(f"can't make {folds} folds from {unique.size} blocks")
    shuffled = np.random.default_rng(seed).permutation(unique)
    return [sorted(int(block) for block in part) for part in np.array_split(shuffled, folds)]


def best_weights(gains_by_weight: dict[str, np.ndarray]) -> str:
    """Return the weights with the highest median gain. Ties go to the first listed."""
    if not gains_by_weight:
        raise ValueError("no weights to choose from")
    return max(gains_by_weight, key=lambda name: float(np.median(gains_by_weight[name])))


def cross_validate(
    gains_by_weight: dict[str, np.ndarray], blocks: np.ndarray, folds: list[list[int]]
) -> CrossValidation:
    """Pick weights on all blocks but one fold, then score them on that fold.

    Every array holds one value per segment, in the same order.
    """
    blocks = np.asarray(blocks)
    all_gains, all_blocks, chosen = [], [], []
    for fold in folds:
        held_out = np.isin(blocks, fold)
        if not held_out.any() or held_out.all():
            continue
        training = {name: gains[~held_out] for name, gains in gains_by_weight.items()}
        pick = best_weights(training)
        chosen.append(pick)
        all_gains.append(gains_by_weight[pick][held_out])
        all_blocks.append(blocks[held_out])
    if not all_gains:
        raise ValueError("no usable folds")
    return CrossValidation(np.concatenate(all_gains), np.concatenate(all_blocks), chosen)


def median_range(
    gains: np.ndarray, blocks: np.ndarray, trials: int = 2000, seed: int = 2020
) -> tuple[float, float, float]:
    """Return the median and its 95% range, resampling whole blocks."""
    rng = np.random.default_rng(seed)
    unique = np.unique(blocks)
    groups = {block: gains[blocks == block] for block in unique}
    medians = [
        np.median(np.concatenate([groups[pick] for pick in rng.choice(unique, unique.size, replace=True)]))
        for _ in range(trials)
    ]
    low, high = np.percentile(medians, [2.5, 97.5])
    return float(np.median(gains)), float(low), float(high)
