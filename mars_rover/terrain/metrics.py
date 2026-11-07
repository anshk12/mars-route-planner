"""Terrain measurements computed from a grid of elevations.

Everything here is pure array math with no file access, so each function can be
checked against shapes whose correct answer is known, like a perfect plane.

Conventions
-----------
* Row 0 is the northern edge and columns increase eastward, matching the raster.
* Invalid elevations are NaN. A result is NaN unless every cell it needs is valid,
  so missing data can't quietly look like safe, flat ground.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import ndimage

FloatArray = NDArray[np.floating]


@dataclass(frozen=True)
class TerrainMetrics:
    """Terrain layers that share the shape of the input grid."""

    slope_deg: FloatArray
    aspect_deg: FloatArray
    roughness_m: FloatArray
    step_m: FloatArray


def gradient_horn(
    z: FloatArray, dx_m: ArrayLike, dy_m: float
) -> tuple[FloatArray, FloatArray]:
    """Return eastward and northward slope (rise over run), using Horn's 3x3 method.

    ``dx_m`` is the true ground width of a cell. It can be one number or one value
    per row, because east-west distance on the ground shrinks with latitude. Outer
    cells are NaN since they lack a full set of neighbors.
    """
    z = np.asarray(z, dtype=np.float64)
    if z.ndim != 2:
        raise ValueError("z must be a 2-D array")
    height, width = z.shape
    dz_east = np.full(z.shape, np.nan)
    dz_north = np.full(z.shape, np.nan)
    if height < 3 or width < 3:
        return dz_east, dz_north

    dx = np.broadcast_to(np.asarray(dx_m, dtype=np.float64), (height,))[1:-1, None]

    east = (z[:-2, 2:] + 2 * z[1:-1, 2:] + z[2:, 2:]) - (
        z[:-2, :-2] + 2 * z[1:-1, :-2] + z[2:, :-2]
    )
    # Row numbers increase southward, so north minus south is top minus bottom.
    north = (z[:-2, :-2] + 2 * z[:-2, 1:-1] + z[:-2, 2:]) - (
        z[2:, :-2] + 2 * z[2:, 1:-1] + z[2:, 2:]
    )
    dz_east[1:-1, 1:-1] = east / (8.0 * dx)
    dz_north[1:-1, 1:-1] = north / (8.0 * dy_m)

    # Horn's formula skips the center cell, so a missing cell could otherwise get a
    # believable slope from its neighbors and look safe to drive on.
    missing = ~np.isfinite(z)
    dz_east[missing] = np.nan
    dz_north[missing] = np.nan
    return dz_east, dz_north


def slope_aspect(
    dz_east: FloatArray, dz_north: FloatArray
) -> tuple[FloatArray, FloatArray]:
    """Convert a gradient to slope and to the compass direction facing downhill.

    Aspect is in degrees clockwise from north, and is NaN where the ground is flat
    because flat ground doesn't face any direction.
    """
    slope = np.degrees(np.arctan(np.hypot(dz_east, dz_north)))
    aspect = np.degrees(np.arctan2(-dz_east, -dz_north)) % 360.0
    aspect = np.where((dz_east == 0) & (dz_north == 0), np.nan, aspect)
    return slope, aspect


def _window_sum(a: FloatArray, weights_x: FloatArray, weights_y: FloatArray) -> FloatArray:
    """Weighted sum over a square window, applied one axis at a time for speed."""
    # Direct convolution, not a running sum, which would accumulate rounding error.
    out = ndimage.correlate1d(a, weights_x, axis=1, mode="constant", cval=0.0)
    return ndimage.correlate1d(out, weights_y, axis=0, mode="constant", cval=0.0)


@dataclass(frozen=True)
class _PlaneFit:
    """Least-squares plane through each window, in units of meters per cell."""

    east_per_cell: FloatArray
    north_per_cell: FloatArray
    filled: FloatArray  # Centered elevations with invalid cells set to zero.
    valid_window: NDArray[np.bool_]  # True where the whole window has data.
    mean_x2: float  # Mean squared cell offset across the window.
    mean_z: FloatArray


def _check_window(window: int) -> int:
    if window < 3 or window % 2 == 0:
        raise ValueError(f"window must be an odd number of at least 3, got {window}")
    return window // 2


def _fit_planes(z: FloatArray, window: int) -> _PlaneFit:
    """Fit a plane to every window in one pass.

    On a regular grid the cell offsets are symmetric, so the least-squares fit
    simplifies to a few weighted sums and needs no matrix solve per window.
    """
    radius = _check_window(window)
    z = np.asarray(z, dtype=np.float64)
    valid = np.isfinite(z)

    # Elevations near -2500 m would waste precision, so center them first.
    center = float(z[valid].mean()) if valid.any() else 0.0
    filled = np.where(valid, z - center, 0.0)

    ones = np.ones(window)
    offsets = np.arange(-radius, radius + 1, dtype=np.float64)
    sum_x2 = float(np.sum(offsets**2)) * window

    valid_window = (
        _window_sum(valid.astype(np.float64), ones, ones) > window * window - 0.5
    )
    # Filters read the array top-down, so positive y offsets point south.
    east_per_cell = _window_sum(filled, offsets, ones) / sum_x2
    north_per_cell = -_window_sum(filled, ones, offsets) / sum_x2
    mean_z = _window_sum(filled, ones, ones) / (window * window)

    return _PlaneFit(
        east_per_cell=east_per_cell,
        north_per_cell=north_per_cell,
        filled=filled,
        valid_window=valid_window,
        mean_x2=sum_x2 / (window * window),
        mean_z=mean_z,
    )


def roughness_rms(z: FloatArray, window: int = 11) -> FloatArray:
    """Return how far the ground departs from its best-fit plane, in meters.

    This is the root-mean-square leftover after removing the plane, so a smooth hill
    of any steepness scores zero. Slope and roughness then measure different risks.
    The result doesn't depend on the map's east-west stretch, since stretching a
    plane still leaves a plane.
    """
    fit = _fit_planes(z, window)
    ones = np.ones(window)
    mean_z2 = _window_sum(fit.filled**2, ones, ones) / (window * window)
    # Variance left over = total variance - variance explained by the plane.
    residual = (
        mean_z2
        - fit.mean_z**2
        - (fit.east_per_cell**2 + fit.north_per_cell**2) * fit.mean_x2
    )
    rms = np.sqrt(np.clip(residual, 0.0, None))
    return np.where(fit.valid_window, rms, np.nan)


def step_height(z: FloatArray, window: int = 3, plane_window: int = 21) -> FloatArray:
    """Estimate abrupt drops or ledges in meters, ignoring the height change of the slope.

    In a square window, a perfect plane has a height range of exactly
    ``(|east| + |north|) * (window - 1)``. Subtracting that leaves only the abrupt part.

    The gradient comes from a much larger window, because a small one would absorb
    the ledge into its slope and hide it. A sharp ledge is still underestimated by
    about 14% with the defaults.
    """
    _check_window(window)
    _check_window(plane_window)
    if plane_window <= window:
        raise ValueError("plane_window must be larger than window")

    fit = _fit_planes(z, plane_window)
    # The filled grid is safe here: only windows with complete data are kept below.
    relief = ndimage.maximum_filter(fit.filled, size=window, mode="nearest") - (
        ndimage.minimum_filter(fit.filled, size=window, mode="nearest")
    )
    expected = (np.abs(fit.east_per_cell) + np.abs(fit.north_per_cell)) * (window - 1)
    step = np.clip(relief - expected, 0.0, None)
    return np.where(fit.valid_window, step, np.nan)


def compute_metrics(
    z: FloatArray,
    dx_m: ArrayLike,
    dy_m: float,
    roughness_window: int = 11,
    step_window: int = 3,
    plane_window: int = 21,
) -> TerrainMetrics:
    """Compute every terrain layer for one grid of elevations."""
    dz_east, dz_north = gradient_horn(z, dx_m, dy_m)
    slope, aspect = slope_aspect(dz_east, dz_north)
    return TerrainMetrics(
        slope_deg=slope,
        aspect_deg=aspect,
        roughness_m=roughness_rms(z, roughness_window),
        step_m=step_height(z, step_window, plane_window),
    )


def required_margin(
    roughness_window: int = 11, step_window: int = 3, plane_window: int = 21
) -> int:
    """Return how many extra cells each side a tile needs so seams don't show."""
    return max(1, roughness_window // 2, step_window // 2, plane_window // 2)
