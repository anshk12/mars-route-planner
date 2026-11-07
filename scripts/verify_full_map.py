"""Check the finished full-map layers and draw an overview with Perseverance's route.

1. Seams: recompute sections that straddle tile boundaries in one piece, directly from the
   raw map, and require a match with the saved layers.
2. Coverage: report how much of the map has valid data, and summarize each layer.
3. Overview: draw slope across the whole map with the real route on top.

Run from the project folder: python -m scripts.verify_full_map
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window

from mars_rover.sources import DTM_URL
from mars_rover.terrain.metrics import compute_metrics, required_margin
from mars_rover.terrain.pipeline import LAYERS
from mars_rover.terrain.source import TerrainSource
from mars_rover.terrain.visualize import MAGNITUDE, SURFACE, TEXT_PRIMARY, TEXT_SECONDARY

DERIVED = Path("data/derived")
DTM_PATH = Path("data/raw") / DTM_URL.rsplit("/", 1)[-1]
MANIFEST = Path("data/raw/MANIFEST.json")
OVERVIEW = Path("figures/full_map_slope_route.png")
TILE_SIZE = 2048
ROUTE_COLOR = "#eb6834"
DECIMATE = 8


def check_seams(source: TerrainSource) -> None:
    """Compare saved layers with a one-piece calculation across tile boundaries."""
    size = 400
    margin = required_margin()
    centers = [
        (TILE_SIZE, TILE_SIZE),
        (3 * TILE_SIZE, 5 * TILE_SIZE),
        (7 * TILE_SIZE, 2 * TILE_SIZE),
        (5 * TILE_SIZE, 9 * TILE_SIZE),
        (9 * TILE_SIZE, 7 * TILE_SIZE),
    ]
    print("Seam check: saved layer vs. one-piece calculation")
    for row_center, col_center in centers:
        top, left = row_center - size // 2, col_center - size // 2
        padded = Window(left - margin, top - margin, size + 2 * margin, size + 2 * margin)
        z = source.read(padded)
        dx, dy = source.ground_cell_size(top - margin, size + 2 * margin)
        fresh = compute_metrics(z, dx, dy)
        core = (slice(margin, margin + size), slice(margin, margin + size))
        line = [f"  boundary at row {row_center}, col {col_center}:"]
        for name in LAYERS:
            with rasterio.open(DERIVED / f"{name}.tif") as dataset:
                saved = dataset.read(1, window=Window(left, top, size, size))
            expected = getattr(fresh, name)[core].astype(np.float32)
            if not np.array_equal(np.isnan(saved), np.isnan(expected)):
                raise SystemExit(f"FAILED: missing-data pattern differs in {name}")
            both = np.isfinite(saved)
            # Aspect wraps around at 360 degrees, so compare it around the circle.
            gap = np.abs(saved[both] - expected[both])
            if name == "aspect_deg":
                gap = np.minimum(gap, 360.0 - gap)
            worst = float(gap.max()) if gap.size else 0.0
            if worst > 1e-3:
                raise SystemExit(f"FAILED: {name} differs by {worst} at this boundary")
            line.append(f"{name} {worst:.1e}")
        print(" ".join(line))
    print("  All boundaries match.")


def summarize_layers() -> None:
    """Report valid coverage and percentiles from each full layer, one block at a time."""
    print("\nFull-map layers:")
    for name in LAYERS:
        path = DERIVED / f"{name}.tif"
        with rasterio.open(path) as dataset:
            valid_cells = 0
            samples = []
            rng = np.random.default_rng(0)
            for _, window in dataset.block_windows(1):
                block = dataset.read(1, window=window)
                finite = block[np.isfinite(block)]
                valid_cells += finite.size
                if finite.size:
                    samples.append(rng.choice(finite, size=max(1, finite.size // 50)))
            total = dataset.width * dataset.height
        values = np.concatenate(samples)
        p50, p95, p99 = np.percentile(values, [50, 95, 99])
        print(
            f"  {name:<12} {path.stat().st_size / 1e6:7.0f} MB  valid {100 * valid_cells / total:5.1f}%  "
            f"median {p50:7.2f}  95th {p95:7.2f}  99th {p99:7.2f}  (2% sample)"
        )


def route_lines(source: TerrainSource) -> tuple[list[np.ndarray], float]:
    """Return route segments as map kilometers, plus the share inside the map."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    path = next(r["path"] for r in manifest["routes"] if r["name"] == "traverse")
    features = json.loads(Path(path).read_text(encoding="utf-8"))["features"]
    west, south, east, north = source.bounds
    lines, inside, total = [], 0, 0
    for feature in features:
        lonlat = np.array(feature["geometry"]["coordinates"])[:, :2]
        x, y = source._to_map.transform(lonlat[:, 0], lonlat[:, 1])
        x, y = np.asarray(x), np.asarray(y)
        total += x.size
        inside += int(np.sum((west <= x) & (x <= east) & (south <= y) & (y <= north)))
        lines.append(np.column_stack(((x - west) / 1000, (y - south) / 1000)))
    return lines, inside / total


def draw_overview(source: TerrainSource) -> None:
    with rasterio.open(DERIVED / "slope_deg.tif") as dataset:
        slope = dataset.read(
            1,
            out_shape=(dataset.height // DECIMATE, dataset.width // DECIMATE),
            resampling=Resampling.average,
        )
    west, south, east, north = source.bounds
    extent = (0, (east - west) / 1000, 0, (north - south) / 1000)
    lines, inside_share = route_lines(source)

    fig, ax = plt.subplots(figsize=(10, 9), facecolor=SURFACE, layout="constrained")
    ax.set_facecolor(SURFACE)
    image = ax.imshow(
        np.ma.masked_invalid(slope), cmap=MAGNITUDE, vmin=0, vmax=25, extent=extent,
        interpolation="nearest",
    )
    for line in lines:
        ax.plot(line[:, 0], line[:, 1], color=SURFACE, linewidth=3.5, solid_capstyle="round")
    for line in lines:
        ax.plot(line[:, 0], line[:, 1], color=ROUTE_COLOR, linewidth=2, solid_capstyle="round")
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])

    landing = lines[0][0]
    ax.annotate(
        "Perseverance's real route", xy=landing, xytext=(landing[0] + 0.6, landing[1] - 1.6),
        color=TEXT_PRIMARY, fontsize=11,
        bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 3},
    )
    ax.set_title(
        "Jezero Crater slope map, with Perseverance's route through sol 2022",
        loc="left", fontsize=14, color=TEXT_PRIMARY, pad=10,
    )
    ax.set_xlabel("km east of the map's west edge", color=TEXT_SECONDARY, fontsize=9)
    ax.set_ylabel("km north of the map's south edge", color=TEXT_SECONDARY, fontsize=9)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=8, length=2)
    for spine in ax.spines.values():
        spine.set_visible(False)
    bar = fig.colorbar(image, ax=ax, fraction=0.04, pad=0.02, extend="max")
    bar.outline.set_visible(False)
    bar.ax.tick_params(colors=TEXT_SECONDARY, labelsize=8, length=0)
    bar.set_label(f"slope in degrees, averaged over {DECIMATE} m", color=TEXT_SECONDARY, fontsize=9)

    OVERVIEW.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OVERVIEW, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    print(f"\nOverview: {OVERVIEW}")
    print(f"Route points inside this map: {100 * inside_share:.1f}%")


def main() -> None:
    with TerrainSource(DTM_PATH) as source:
        check_seams(source)
        summarize_layers()
        draw_overview(source)


if __name__ == "__main__":
    main()
