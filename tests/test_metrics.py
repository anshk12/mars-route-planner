"""Check terrain math on shapes whose correct answer is known exactly."""

import numpy as np
import pytest

from mars_rover.terrain.metrics import (
    compute_metrics,
    gradient_horn,
    required_margin,
    roughness_rms,
    slope_aspect,
    step_height,
)


def plane(east=0.0, north=0.0, shape=(60, 60), dx=1.0, dy=1.0, base=-2500.0):
    """Make a perfect plane. Row numbers increase southward, so north is -row."""
    rows, cols = np.indices(shape, dtype=np.float64)
    return base + east * cols * dx + north * (-rows) * dy


def test_slope_of_known_plane():
    # A rise of 1 m per 10 m is arctan(0.1), or about 5.71 degrees.
    z = plane(east=0.1)
    slope, _ = slope_aspect(*gradient_horn(z, 1.0, 1.0))
    assert np.nanmean(slope) == pytest.approx(np.degrees(np.arctan(0.1)), abs=1e-9)


@pytest.mark.parametrize(
    "east, north, downhill_deg",
    [
        (0.2, 0.0, 270.0),  # Rising eastward means it faces west.
        (-0.2, 0.0, 90.0),
        (0.0, 0.2, 180.0),  # Rising northward means it faces south.
        (0.0, -0.2, 0.0),
    ],
)
def test_aspect_points_downhill(east, north, downhill_deg):
    _, aspect = slope_aspect(*gradient_horn(plane(east, north), 1.0, 1.0))
    assert np.nanmedian(aspect) == pytest.approx(downhill_deg, abs=1e-9)


def test_flat_ground_has_no_aspect():
    slope, aspect = slope_aspect(*gradient_horn(plane(), 1.0, 1.0))
    assert np.nanmax(slope) == 0.0
    assert np.isnan(aspect).all()


def test_slope_uses_true_ground_width():
    # On a 0.95 m wide cell, the same height change is a steeper slope.
    cell_width = 0.95
    z = plane(east=0.1, dx=cell_width)
    dz_east, _ = gradient_horn(z, cell_width, 1.0)
    assert np.nanmean(dz_east) == pytest.approx(0.1, abs=1e-9)
    ignored_stretch, _ = gradient_horn(z, 1.0, 1.0)
    assert np.nanmean(ignored_stretch) == pytest.approx(0.095, abs=1e-9)


def test_gradient_accepts_one_width_per_row():
    z = plane(east=0.1)
    widths = np.linspace(0.9, 1.0, z.shape[0])
    dz_east, _ = gradient_horn(z, widths, 1.0)
    expected = 0.1 / widths[1:-1]
    assert dz_east[1:-1, 30] == pytest.approx(expected)


def test_outer_cells_are_nan_not_zero():
    dz_east, dz_north = gradient_horn(plane(0.1), 1.0, 1.0)
    for edge in (dz_east[0], dz_east[-1], dz_east[:, 0], dz_east[:, -1], dz_north[0]):
        assert np.isnan(edge).all()


def test_tilted_plane_has_no_roughness():
    rough = roughness_rms(plane(0.3, -0.2), window=11)
    assert np.nanmax(rough) == pytest.approx(0.0, abs=1e-5)


def test_roughness_recovers_known_noise():
    rng = np.random.default_rng(42)
    sigma = 0.25
    z = plane(0.2, 0.1, shape=(300, 300)) + rng.normal(0, sigma, (300, 300))
    rough = roughness_rms(z, window=21)
    # A plane removes 3 of the 441 degrees of freedom in each window.
    expected = sigma * np.sqrt(1 - 3 / 441)
    assert np.nanmean(rough) == pytest.approx(expected, rel=0.01)


def test_tilted_plane_has_no_step():
    step = step_height(plane(0.3, -0.2))
    assert np.nanmax(step) == pytest.approx(0.0, abs=1e-5)


def test_ledge_is_detected_with_documented_bias():
    height = 2.0
    z = plane()
    z[:, 30:] += height
    step = step_height(z, window=3, plane_window=21)
    # Finds most of the ledge: the docstring says it runs about 14% low.
    assert step[30, 29] == pytest.approx(height * (1 - 1 / 7), rel=0.01)
    # Far enough from both the ledge and the map edge for a full window.
    assert step[30, 15] == pytest.approx(0.0, abs=1e-6)


def test_step_on_a_slope_is_still_only_the_ledge():
    height = 2.0
    flat = plane()
    flat[:, 30:] += height
    sloped = plane(east=0.2)
    sloped[:, 30:] += height
    assert step_height(sloped)[30, 29] == pytest.approx(
        step_height(flat)[30, 29], abs=1e-6
    )


def test_missing_data_never_looks_safe():
    z = plane(0.1)
    z[30, 30] = np.nan
    metrics = compute_metrics(z, 1.0, 1.0)
    assert np.isnan(metrics.slope_deg[29:32, 29:32]).all()
    # Windows touching the gap are NaN, and windows far from it are not.
    assert np.isnan(metrics.roughness_m[30, 35])
    assert np.isfinite(metrics.roughness_m[30, 41])
    assert np.isnan(metrics.step_m[30, 40])
    assert np.isfinite(metrics.step_m[30, 41])


def test_all_missing_input_is_handled():
    metrics = compute_metrics(np.full((40, 40), np.nan), 1.0, 1.0)
    assert np.isnan(metrics.slope_deg).all()
    assert np.isnan(metrics.roughness_m).all()
    assert np.isnan(metrics.step_m).all()


@pytest.mark.parametrize("window", [1, 2, 4])
def test_bad_windows_are_rejected(window):
    with pytest.raises(ValueError):
        roughness_rms(plane(), window=window)


def test_margin_covers_the_largest_window():
    assert required_margin(11, 3, 21) == 10
    assert required_margin(31, 3, 21) == 15
