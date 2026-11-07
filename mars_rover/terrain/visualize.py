"""Draw a side-by-side view of a terrain section, for checking results by eye."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # Draw to a file, with no window needed.

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LightSource, LinearSegmentedColormap

from .metrics import TerrainMetrics

SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
MISSING_DATA = "#d9d8d3"

# One hue from light to dark, so darker always means "more" on every panel.
_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
MAGNITUDE = LinearSegmentedColormap.from_list("magnitude", _RAMP)
MAGNITUDE.set_bad(MISSING_DATA)


def _style_axes(ax, title: str, extent: tuple[float, float, float, float]) -> None:
    ax.set_title(title, loc="left", fontsize=12, color=TEXT_PRIMARY, pad=8)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=8, length=2)
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    for spine in ax.spines.values():
        spine.set_visible(False)


def _magnitude_panel(fig, ax, layer, title, unit, extent) -> None:
    """Draw one layer, capping the color scale so a few extremes don't wash it out."""
    finite = layer[np.isfinite(layer)]
    top = float(np.percentile(finite, 99.5)) if finite.size else 1.0
    top = top if top > 0 else 1.0
    image = ax.imshow(
        np.ma.masked_invalid(layer),
        cmap=MAGNITUDE,
        vmin=0,
        vmax=top,
        extent=extent,
        interpolation="nearest",
    )
    _style_axes(ax, title, extent)
    bar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.03)
    bar.outline.set_visible(False)
    bar.ax.tick_params(colors=TEXT_SECONDARY, labelsize=8, length=0)
    bar.set_label(unit, color=TEXT_SECONDARY, fontsize=9)


def plot_terrain_section(
    z: np.ndarray,
    metrics: TerrainMetrics,
    cell_size_m: float,
    title: str,
    out_path: str | Path,
) -> Path:
    """Save a four-panel figure of elevation, slope, roughness and step height."""
    height, width = z.shape
    extent = (0, width * cell_size_m, 0, height * cell_size_m)

    fig, axes = plt.subplots(2, 2, figsize=(11, 10), facecolor=SURFACE, layout="constrained")
    for ax in axes.flat:
        ax.set_facecolor(SURFACE)

    # Fill gaps only for the shading calculation, then mark them as missing again.
    filled = np.where(np.isfinite(z), z, np.nanmedian(z))
    shade = LightSource(azdeg=315, altdeg=45).hillshade(
        filled, dx=cell_size_m, dy=cell_size_m, vert_exag=2
    )
    relief = np.ma.masked_where(~np.isfinite(z), shade)
    gray = plt.get_cmap("gray").copy()
    gray.set_bad(MISSING_DATA)
    axes[0, 0].imshow(relief, cmap=gray, vmin=0, vmax=1, extent=extent, interpolation="nearest")
    low, high = np.nanmin(z), np.nanmax(z)
    _style_axes(
        axes[0, 0],
        f"Shaded relief: {low:,.0f} to {high:,.0f} m elevation",
        extent,
    )

    _magnitude_panel(
        fig, axes[0, 1], metrics.slope_deg, "Slope: how steep", "degrees", extent
    )
    _magnitude_panel(
        fig,
        axes[1, 0],
        metrics.roughness_m,
        "Roughness: bumpiness left after removing slope",
        "meters (RMS)",
        extent,
    )
    _magnitude_panel(
        fig,
        axes[1, 1],
        metrics.step_m,
        "Step height: abrupt drops and ledges",
        "meters",
        extent,
    )
    for ax in axes[1]:
        ax.set_xlabel("meters east", color=TEXT_SECONDARY, fontsize=9)
    for ax in axes[:, 0]:
        ax.set_ylabel("meters north", color=TEXT_SECONDARY, fontsize=9)

    fig.suptitle(title, x=0.01, ha="left", fontsize=14, color=TEXT_PRIMARY)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    return out_path
