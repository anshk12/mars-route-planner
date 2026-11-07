"""Load a planning grid for part of the map, and score any path on it the same way."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from numpy.typing import NDArray
from rasterio.windows import Window

from mars_rover.rover.energy import RoverPhysics
from mars_rover.rover.hazards import HazardClass, HazardLimits, classify_from_worst, worst_in_footprints
from mars_rover.terrain.source import TerrainSource

from .search import PlanningGrid, build_grid


def load_grid(
    derived_dir: str | Path,
    window: Window,
    limits: HazardLimits = HazardLimits(),
    physics: RoverPhysics = RoverPhysics(),
) -> PlanningGrid:
    """Read the terrain layers for one window of the map and build its planning grid."""
    derived_dir = Path(derived_dir)
    margin = limits.footprint_cells // 2
    with TerrainSource(derived_dir / "slope_deg.tif") as source:
        left = max(int(window.col_off) - margin, 0)
        top = max(int(window.row_off) - margin, 0)
        right = min(int(window.col_off + window.width) + margin, source.width)
        bottom = min(int(window.row_off + window.height) + margin, source.height)
        padded = Window(left, top, right - left, bottom - top)
        dx, dy = source.ground_cell_size(int(window.row_off), int(window.height))

    layers = {}
    for name in ("slope_deg", "aspect_deg", "step_m", "roughness_m"):
        with rasterio.open(derived_dir / f"{name}.tif") as dataset:
            layers[name] = dataset.read(1, window=padded)

    worst = worst_in_footprints(layers["slope_deg"], layers["step_m"], layers["roughness_m"], limits.footprint_cells)
    classes = classify_from_worst(worst, limits)
    core = (
        slice(int(window.row_off) - top, int(window.row_off) - top + int(window.height)),
        slice(int(window.col_off) - left, int(window.col_off) - left + int(window.width)),
    )
    return build_grid(
        classes[core],
        layers["slope_deg"][core],
        layers["aspect_deg"][core],
        worst.step_m[core],
        worst.roughness_m[core],
        dx,
        dy,
        limits,
        physics,
        origin=(int(window.row_off), int(window.col_off)),
    )


@dataclass(frozen=True)
class PathScore:
    """Measurements of a path, using the same rules for planned and real routes."""

    length_m: float
    energy_ratio: float  # Average energy per meter compared with flat ground.
    climb_m: float
    mean_penalty: float
    worst_grade_deg: float
    blocked_cells: int
    cells: int


def score_path(grid: PlanningGrid, rows: NDArray, cols: NDArray) -> PathScore:
    """Measure a path given in this grid's cells. Consecutive repeated cells are ignored."""
    rows = np.asarray(rows, dtype=np.int64)
    cols = np.asarray(cols, dtype=np.int64)
    keep = np.r_[True, (rows[1:] != rows[:-1]) | (cols[1:] != cols[:-1])]
    rows, cols = rows[keep], cols[keep]
    east = np.diff(cols) * grid.dx_m[rows[:-1]]
    north = -np.diff(rows) * grid.dy_m
    length = np.hypot(east, north)
    moved = length > 0
    rise_per_m = (
        grid.dz_east[rows[:-1], cols[:-1]][moved] * east[moved]
        + grid.dz_north[rows[:-1], cols[:-1]][moved] * north[moved]
    ) / length[moved]
    cos_grade = 1.0 / np.sqrt(1.0 + rise_per_m**2)
    force = np.clip(grid.rolling_resistance * cos_grade + rise_per_m * cos_grade, 0.0, None)
    total = float(length[moved].sum())
    if total == 0:
        energy_ratio, climb, worst_grade, mean_penalty = 0.0, 0.0, 0.0, 0.0
    else:
        energy_ratio = float(np.sum(force / grid.rolling_resistance * length[moved]) / total)
        climb = float(np.sum(np.clip(rise_per_m, 0.0, None) * length[moved]))
        worst_grade = float(np.degrees(np.arctan(np.abs(rise_per_m).max())))
        mean_penalty = float(np.mean(grid.penalty[rows, cols]))
    return PathScore(
        length_m=total,
        energy_ratio=energy_ratio,
        climb_m=climb,
        mean_penalty=mean_penalty,
        worst_grade_deg=worst_grade,
        blocked_cells=int(np.sum(~grid.passable[rows, cols])),
        cells=int(rows.size),
    )


__all__ = ["HazardClass", "PathScore", "load_grid", "score_path"]
