"""Stage 1: load huge elevation maps safely and measure the terrain."""

from .metrics import TerrainMetrics, compute_metrics
from .pipeline import process_terrain
from .source import TerrainSource

__all__ = ["TerrainMetrics", "TerrainSource", "compute_metrics", "process_terrain"]
