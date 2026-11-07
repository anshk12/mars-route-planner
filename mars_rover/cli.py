"""Command-line tools for the project. Run ``python -m mars_rover.cli --help``."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
from rasterio.windows import Window

from .sources import DTM_URL, LANDING_SITE_LON_LAT
from .terrain.metrics import compute_metrics, required_margin
from .terrain.pipeline import process_terrain
from .terrain.source import TerrainSource
from .terrain.visualize import plot_terrain_section


def _summary(name: str, layer: np.ndarray, unit: str) -> str:
    finite = layer[np.isfinite(layer)]
    if finite.size == 0:
        return f"  {name:<12} no valid data"
    p50, p95, p99 = np.percentile(finite, [50, 95, 99])
    return (
        f"  {name:<12} median {p50:7.3f}  95th {p95:7.3f}  99th {p99:7.3f}  "
        f"max {finite.max():7.3f} {unit}"
    )


def command_info(args: argparse.Namespace) -> None:
    with TerrainSource(args.source) as source:
        print(f"Source:   {source.location}")
        print(f"Size:     {source.width} x {source.height} cells")
        print(f"Cell:     {source.cell_size_m[0]} x {source.cell_size_m[1]} map units")
        west, south = source.cell_to_lonlat(source.height - 1, 0)
        east, north = source.cell_to_lonlat(0, source.width - 1)
        print(f"Covers:   lon {west:.4f} to {east:.4f}, lat {south:.4f} to {north:.4f}")
        dx, dy = source.ground_cell_size(source.height // 2, 1)
        print(f"Ground:   middle cell is {dx[0]:.4f} m wide and {dy:.4f} m tall")


def command_sample(args: argparse.Namespace) -> None:
    margin = required_margin()
    with TerrainSource(args.source) as source:
        core = source.window_around(args.lon, args.lat, args.size)
        left = max(int(core.col_off) - margin, 0)
        top = max(int(core.row_off) - margin, 0)
        right = min(int(core.col_off + core.width) + margin, source.width)
        bottom = min(int(core.row_off + core.height) + margin, source.height)

        started = time.perf_counter()
        z = source.read(Window(left, top, right - left, bottom - top))
        read_seconds = time.perf_counter() - started

        dx, dy = source.ground_cell_size(top, bottom - top)
        started = time.perf_counter()
        metrics = compute_metrics(z, dx, dy)
        compute_seconds = time.perf_counter() - started

    keep = (
        slice(int(core.row_off) - top, int(core.row_off) - top + args.size),
        slice(int(core.col_off) - left, int(core.col_off) - left + args.size),
    )
    z_core = z[keep]
    trimmed = type(metrics)(
        slope_deg=metrics.slope_deg[keep],
        aspect_deg=metrics.aspect_deg[keep],
        roughness_m=metrics.roughness_m[keep],
        step_m=metrics.step_m[keep],
    )

    print(f"Section:  {args.size} x {args.size} cells around ({args.lon}, {args.lat})")
    print(f"Read in:  {read_seconds:.1f} s. Measured in: {compute_seconds:.2f} s")
    print(f"Valid:    {100 * np.isfinite(z_core).mean():.2f}% of cells")
    print(_summary("elevation", z_core, "m"))
    print(_summary("slope", trimmed.slope_deg, "deg"))
    print(_summary("roughness", trimmed.roughness_m, "m"))
    print(_summary("step height", trimmed.step_m, "m"))

    out = plot_terrain_section(
        z_core,
        trimmed,
        source_cell := float(abs(dy)),
        f"Jezero Crater terrain near ({args.lon}, {args.lat}), "
        f"{args.size * source_cell / 1000:.1f} km section",
        args.out,
    )
    print(f"Figure:   {out}")


def command_process(args: argparse.Namespace) -> None:
    def show_progress(done: int, total: int) -> None:
        elapsed = time.perf_counter() - started
        remaining = elapsed / done * (total - done)
        print(f"  tile {done}/{total}, about {remaining / 60:.1f} min left", flush=True)

    region = Window(*args.region) if args.region else None
    started = time.perf_counter()
    with TerrainSource(args.source) as source:
        paths = process_terrain(
            source, args.out, region=region, tile_size=args.tile_size, progress=show_progress
        )
    print(f"Finished in {(time.perf_counter() - started) / 60:.1f} min")
    for name, path in paths.items():
        print(f"  {name:<12} {path}  ({path.stat().st_size / 1e6:.1f} MB)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mars_rover", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    info = commands.add_parser("info", help="describe an elevation map")
    info.add_argument("--source", default=DTM_URL, help="local path or URL")
    info.set_defaults(run=command_info)

    sample = commands.add_parser("sample", help="measure and draw one section of terrain")
    sample.add_argument("--source", default=DTM_URL, help="local path or URL")
    sample.add_argument("--lon", type=float, default=LANDING_SITE_LON_LAT[0])
    sample.add_argument("--lat", type=float, default=LANDING_SITE_LON_LAT[1])
    sample.add_argument("--size", type=int, default=1024, help="section width in cells")
    sample.add_argument("--out", type=Path, default=Path("figures/terrain_sample.png"))
    sample.set_defaults(run=command_sample)

    process = commands.add_parser("process", help="build terrain layers, tile by tile")
    process.add_argument("--source", required=True, help="local path to the elevation map")
    process.add_argument("--out", type=Path, default=Path("data/derived"))
    process.add_argument("--tile-size", type=int, default=2048)
    process.add_argument(
        "--region",
        type=int,
        nargs=4,
        metavar=("COL", "ROW", "WIDTH", "HEIGHT"),
        help="process only this part of the map",
    )
    process.set_defaults(run=command_process)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
