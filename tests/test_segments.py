"""Check how the route gets divided and split, using simple made-up drives."""

import numpy as np
import pytest

from mars_rover.evaluation.segments import (
    assign_blocks,
    choose_test_blocks,
    path_distance,
    segments_by_distance,
    segments_by_pause,
    straight_line,
)


def drive(sol, start_col, length=60, row=0):
    cols = np.arange(start_col, start_col + length, dtype=np.int64)
    return {"sol": sol, "length": float(length), "rows": np.full(cols.size, row, dtype=np.int64), "cols": cols}


def in_a_line(sols, length=60):
    return [drive(sol, index * length, length) for index, sol in enumerate(sols)]


def test_blocks_start_after_about_the_set_distance():
    drives = in_a_line([1, 2, 3, 4, 5, 6], length=500)
    assert assign_blocks(drives, block_m=1000) == [0, 0, 1, 1, 2, 2]


def test_test_split_is_reproducible_and_the_right_size():
    assert choose_test_blocks(40) == choose_test_blocks(40)
    assert len(choose_test_blocks(40, test_share=0.3)) == 12


def test_different_seeds_give_different_splits():
    assert choose_test_blocks(40, seed=1) != choose_test_blocks(40, seed=2)


@pytest.mark.parametrize("kwargs", [{"block_count": 1}, {"block_count": 10, "test_share": 0}, {"block_count": 10, "test_share": 1}])
def test_bad_splits_are_rejected(kwargs):
    with pytest.raises(ValueError):
        choose_test_blocks(**kwargs)


def test_long_pause_starts_a_new_segment():
    drives = in_a_line([10, 11, 12, 20, 21, 22])
    segments = segments_by_pause(drives, [0] * 6, pause_sols=3, min_length_m=100)
    assert [(s.first_sol, s.last_sol) for s in segments] == [(10, 12), (20, 22)]


def test_short_pause_doesnt_split():
    drives = in_a_line([10, 12, 14])
    assert len(segments_by_pause(drives, [0] * 3, pause_sols=3, min_length_m=100)) == 1


def test_segments_never_cross_a_block_boundary():
    """Otherwise test data would leak into the development set."""
    drives = in_a_line([1, 2, 3, 4, 5, 6])
    blocks = [0, 0, 0, 1, 1, 1]
    for segments in (segments_by_pause(drives, blocks), segments_by_distance(drives, blocks)):
        assert {s.block for s in segments} == {0, 1}
        for segment in segments:
            if segment.block == 0:
                assert segment.last_sol <= 3
            else:
                assert segment.first_sol >= 4


def test_position_jump_splits_a_segment():
    drives = [drive(1, 0, 120), drive(2, 500, 120)]
    assert len(segments_by_pause(drives, [0, 0], min_length_m=100)) == 2


def test_drive_off_the_map_splits_a_segment():
    drives = in_a_line([1, 2, 3], length=120)
    drives[1]["rows"] = np.array([], dtype=np.int64)
    drives[1]["cols"] = np.array([], dtype=np.int64)
    assert len(segments_by_pause(drives, [0, 0, 0], min_length_m=100)) == 2


def test_segments_shorter_than_the_minimum_are_dropped():
    assert segments_by_pause(in_a_line([1]), [0], min_length_m=100) == []


def test_fixed_distance_covers_the_path_without_gaps():
    drives = in_a_line([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], length=60)
    segments = segments_by_distance(drives, [0] * 10, length_m=200, min_length_m=100)
    assert len(segments) == 3
    for before, after in zip(segments, segments[1:]):
        assert before.cols[-1] == after.cols[0]


def test_straight_line_runs_between_its_endpoints():
    rows, cols = straight_line((0, 0), (10, 5))
    assert (rows[0], cols[0]) == (0, 0)
    assert (rows[-1], cols[-1]) == (10, 5)
    assert rows.size == 11


def test_path_distance_is_zero_for_identical_paths():
    rows, cols = straight_line((0, 0), (0, 50))
    assert path_distance(rows, cols, rows, cols) == 0.0


def test_path_distance_measures_a_parallel_offset():
    rows, cols = straight_line((0, 0), (0, 50))
    assert path_distance(rows, cols, rows + 5, cols) == pytest.approx(5.0)


def test_path_distance_catches_a_route_covering_only_part_of_the_path():
    """A one-way measure would score this as a perfect match."""
    full_rows, full_cols = straight_line((0, 0), (0, 100))
    short_rows, short_cols = straight_line((0, 0), (0, 10))
    assert path_distance(short_rows, short_cols, full_rows, full_cols) > 15.0
