"""Classify ground as safe, risky, off-limits or unknown.

The 25 degree slope limit and the choice to treat ledges as a penalty are the project author's decisions,
recorded in docs/decisions.md. Each check uses the worst value in a rover-sized footprint, because the
rover occupies more than one cell.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

import numpy as np
import rasterio
from numpy.typing import NDArray
from rasterio.windows import Window
from scipy import ndimage

from mars_rover.terrain.pipeline import iter_tiles


class HazardClass(IntEnum):
    UNKNOWN = 0
    SAFE = 1
    RISKY = 2
    OFF_LIMITS = 3


@dataclass(frozen=True)
class HazardLimits:
    slope_off_limits_deg: float = 25.0  # The author's decision.
    step_risky_m: float = 0.525  # NASA's wheel-size rule of thumb.
    roughness_risky_m: float = 0.3  # Provisional.
    footprint_cells: int = 3  # About the rover's size on a 1 m map.

    def __post_init__(self) -> None:
        if self.footprint_cells < 1 or self.footprint_cells % 2 == 0:
            raise ValueError("footprint_cells must be a positive odd number")


def _worst_in_footprint(layer: NDArray, size: int) -> tuple[NDArray, NDArray]:
    """Return the highest known value in each footprint, and where a footprint has gaps."""
    missing = ~np.isfinite(layer)
    known = np.where(missing, -np.inf, layer)
    worst = ndimage.maximum_filter(known, size=size, mode="constant", cval=-np.inf)
    # Past the map's edge counts as missing, because that ground has never been measured.
    has_gap = ndimage.maximum_filter(missing, size=size, mode="constant", cval=True)
    return worst, has_gap


def classify(
    slope_deg: NDArray,
    step_m: NDArray,
    roughness_m: NDArray,
    limits: HazardLimits = HazardLimits(),
) -> NDArray[np.uint8]:
    """Classify every cell, applying the checks in order of severity.

    1. Off-limits: a known slope in the footprint exceeds the limit, even if part of it is missing.
    2. Unknown: any measurement in the footprint is missing.
    3. Risky: a ledge or roughness exceeds its limit.
    4. Safe: everything else.
    """
    return classify_from_worst(
        worst_in_footprints(slope_deg, step_m, roughness_m, limits.footprint_cells), limits
    )


@dataclass(frozen=True)
class FootprintWorst:
    """The worst value in each footprint, kept so several limits can reuse one calculation."""

    slope_deg: NDArray
    step_m: NDArray
    roughness_m: NDArray
    has_gap: NDArray[np.bool_]


def worst_in_footprints(
    slope_deg: NDArray, step_m: NDArray, roughness_m: NDArray, footprint_cells: int
) -> FootprintWorst:
    """Measure the worst slope, step and roughness in every rover-sized footprint."""
    slope, slope_gap = _worst_in_footprint(np.asarray(slope_deg), footprint_cells)
    step, step_gap = _worst_in_footprint(np.asarray(step_m), footprint_cells)
    rough, rough_gap = _worst_in_footprint(np.asarray(roughness_m), footprint_cells)
    return FootprintWorst(slope, step, rough, slope_gap | step_gap | rough_gap)


def classify_from_worst(worst: FootprintWorst, limits: HazardLimits) -> NDArray[np.uint8]:
    """Apply hazard limits to footprint measurements. See ``classify`` for the rules."""
    result = np.full(worst.slope_deg.shape, HazardClass.SAFE, dtype=np.uint8)
    risky = (worst.step_m > limits.step_risky_m) | (worst.roughness_m > limits.roughness_risky_m)
    result[risky] = HazardClass.RISKY
    result[worst.has_gap] = HazardClass.UNKNOWN
    result[worst.slope_deg > limits.slope_off_limits_deg] = HazardClass.OFF_LIMITS
    return result


def process_hazards(
    derived_dir: str | Path,
    out_path: str | Path | None = None,
    limits: HazardLimits = HazardLimits(),
    tile_size: int = 2048,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Build the hazard map tile by tile from the terrain layers."""
    derived_dir = Path(derived_dir)
    out_path = derived_dir / "hazard_class.tif" if out_path is None else Path(out_path)
    temp_path = out_path.with_name(f".{out_path.stem}.tmp.tif")
    margin = limits.footprint_cells // 2

    inputs = {
        name: rasterio.open(derived_dir / f"{name}.tif")
        for name in ("slope_deg", "step_m", "roughness_m")
    }
    completed = False
    try:
        reference = inputs["slope_deg"]
        profile = {
            "driver": "GTiff",
            "dtype": "uint8",
            "count": 1,
            "width": reference.width,
            "height": reference.height,
            "crs": reference.crs,
            "transform": reference.transform,
            "tiled": True,
            "blockxsize": 512,
            "blockysize": 512,
            "compress": "deflate",
        }
        region = Window(0, 0, reference.width, reference.height)
        tiles = list(iter_tiles(region, tile_size))
        with rasterio.open(temp_path, "w", **profile) as output:
            for index, tile in enumerate(tiles, start=1):
                left = max(int(tile.col_off) - margin, 0)
                top = max(int(tile.row_off) - margin, 0)
                right = min(int(tile.col_off + tile.width) + margin, reference.width)
                bottom = min(int(tile.row_off + tile.height) + margin, reference.height)
                padded = Window(left, top, right - left, bottom - top)
                layers = {name: data.read(1, window=padded) for name, data in inputs.items()}
                classes = classify(layers["slope_deg"], layers["step_m"], layers["roughness_m"], limits)
                row_start = int(tile.row_off) - top
                col_start = int(tile.col_off) - left
                core = classes[
                    row_start : row_start + int(tile.height),
                    col_start : col_start + int(tile.width),
                ]
                output.write(core, 1, window=tile)
                if progress is not None:
                    progress(index, len(tiles))
        completed = True
    finally:
        for dataset in inputs.values():
            dataset.close()
        if not completed:
            temp_path.unlink(missing_ok=True)

    os.replace(temp_path, out_path)
    return out_path
