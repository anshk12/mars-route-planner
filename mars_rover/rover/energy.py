"""Estimate the energy needed to drive in a given direction across a slope.

This is simple physics with assumed values, not a measured model of Perseverance. No published energy
use per meter was found, so results are useful for comparing routes with each other, and shouldn't be
read as the rover's real energy use. See docs/decisions.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray


@dataclass(frozen=True)
class RoverPhysics:
    """Physical values for the energy estimate."""

    mass_kg: float = 1025.0  # NASA's published rover mass.
    gravity_m_s2: float = 3.71  # Mars surface gravity.
    rolling_resistance: float = 0.15  # Assumed.
    drivetrain_efficiency: float = 0.5  # Assumed.


def along_track_grade_deg(slope_deg: ArrayLike, aspect_deg: ArrayLike, heading_deg: ArrayLike) -> NDArray:
    """Return the slope in the direction of travel. Positive means driving uphill.

    Aspect is the compass direction facing downhill, so driving in that direction is the steepest
    descent, and driving across the slope is level. Flat ground has no aspect, which gives a grade of 0.
    """
    slope = np.radians(np.asarray(slope_deg, dtype=np.float64))
    aspect = np.asarray(aspect_deg, dtype=np.float64)
    heading = np.asarray(heading_deg, dtype=np.float64)
    downhill_alignment = np.cos(np.radians(heading - aspect))
    grade = np.degrees(np.arctan(-np.tan(slope) * downhill_alignment))
    return np.where(np.isfinite(slope) & (slope == 0), 0.0, grade)


def energy_per_meter(
    slope_deg: ArrayLike,
    aspect_deg: ArrayLike,
    heading_deg: ArrayLike,
    physics: RoverPhysics = RoverPhysics(),
) -> NDArray:
    """Return the estimated energy in joules per meter of driving.

    The wheels must overcome rolling resistance and any climb. When gravity alone is enough to keep the
    rover moving downhill, the energy needed is zero, and none is recovered. Missing inputs stay NaN.
    """
    grade = np.radians(along_track_grade_deg(slope_deg, aspect_deg, heading_deg))
    force_n = (
        physics.mass_kg
        * physics.gravity_m_s2
        * (physics.rolling_resistance * np.cos(grade) + np.sin(grade))
    )
    return np.where(np.isnan(force_n), np.nan, np.clip(force_n, 0.0, None)) / physics.drivetrain_efficiency
