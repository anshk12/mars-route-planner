"""Check that weight choosing can't quietly use the data it gets scored on."""

import numpy as np
import pytest

from mars_rover.evaluation.fitting import best_weights, cross_validate, make_folds, median_range


def test_folds_cover_every_block_exactly_once():
    folds = make_folds([1, 1, 2, 3, 4, 5, 5, 6], folds=3)
    together = sorted(block for fold in folds for block in fold)
    assert together == [1, 2, 3, 4, 5, 6]


def test_folds_are_reproducible():
    assert make_folds(list(range(20)), folds=4) == make_folds(list(range(20)), folds=4)


@pytest.mark.parametrize("count", [1, 7])
def test_impossible_fold_counts_are_rejected(count):
    with pytest.raises(ValueError):
        make_folds([1, 2, 3], folds=count)


def test_best_weights_uses_the_median():
    gains = {"steady": np.array([1.0, 1.0, 1.0]), "one lucky": np.array([0.0, 0.0, 100.0])}
    assert best_weights(gains) == "steady"


def test_a_fold_is_scored_by_weights_chosen_without_it():
    """Weights 'a' only look good on block 1, so block 1 must be scored by 'b' when it's held out."""
    blocks = np.array([1, 1, 2, 2, 3, 3])
    gains = {
        "a": np.array([100.0, 100.0, -1.0, -1.0, -1.0, -1.0]),
        "b": np.array([0.0, 0.0, 1.0, 1.0, 1.0, 1.0]),
    }
    result = cross_validate(gains, blocks, [[1], [2], [3]])
    assert result.chosen[0] == "b"
    assert result.gains[result.blocks == 1].tolist() == [0.0, 0.0]


def test_cross_validation_scores_each_segment_once():
    blocks = np.array([1, 2, 3, 4])
    gains = {"a": np.array([1.0, 2.0, 3.0, 4.0])}
    result = cross_validate(gains, blocks, [[1, 2], [3, 4]])
    assert sorted(result.gains.tolist()) == [1.0, 2.0, 3.0, 4.0]


def test_noise_alone_shouldnt_look_like_a_gain():
    """The best of many random weights looks good by luck, while the cross-validated score doesn't."""
    rng = np.random.default_rng(1)
    blocks = np.repeat(np.arange(30), 5)
    gains = {f"w{i}": rng.normal(0, 1, blocks.size) for i in range(40)}
    best_score = max(float(np.median(values)) for values in gains.values())
    fair = cross_validate(gains, blocks, make_folds(blocks, folds=5))
    assert best_score > 0.2
    assert abs(float(np.median(fair.gains))) < best_score / 2


def test_median_range_contains_the_median_and_is_reproducible():
    rng = np.random.default_rng(2)
    gains = rng.normal(1.0, 0.5, 200)
    blocks = np.repeat(np.arange(20), 10)
    median, low, high = median_range(gains, blocks)
    assert low <= median <= high
    assert (median, low, high) == median_range(gains, blocks)
