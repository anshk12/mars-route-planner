"""Read small windows from a huge elevation file, either local or online.

The Jezero map has about 460 million cells, which is roughly 1.8 GB. This class never
loads all of it. It reads only the requested window, and handles two details that are
easy to get wrong: missing-data markers and the map projection's east-west stretch.
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType

import numpy as np
import rasterio
from numpy.typing import NDArray
from pyproj import CRS, Transformer
from rasterio.windows import Window

# Anything this far below zero is a missing-data marker. Mars's lowest point is only
# about 8 km below its reference surface.
_IMPLAUSIBLE_ELEVATION_M = -1.0e30


class TerrainSource:
    """A read-only view of an elevation raster. Use it in a ``with`` block."""

    def __init__(self, location: str | Path):
        location = str(location)
        self.location = location
        is_remote = location.startswith(("http://", "https://"))
        self._path = f"/vsicurl/{location}" if is_remote else location
        self._env = rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
        )
        self._env.__enter__()
        try:
            self._ds = rasterio.open(self._path)
            if self._ds.crs is None:
                raise ValueError(f"{location} has no map projection information")
            self._crs = CRS.from_wkt(self._ds.crs.to_wkt())
        except Exception:
            self._env.__exit__(None, None, None)
            raise

        geographic = self._crs.geodetic_crs
        self._to_map = Transformer.from_crs(geographic, self._crs, always_xy=True)
        self._to_lonlat = Transformer.from_crs(self._crs, geographic, always_xy=True)
        self.radius_m = float(self._crs.ellipsoid.semi_major_metre)

    def __enter__(self) -> "TerrainSource":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if not self._ds.closed:
            self._ds.close()
            self._env.__exit__(None, None, None)

    @property
    def width(self) -> int:
        return self._ds.width

    @property
    def height(self) -> int:
        return self._ds.height

    @property
    def transform(self):
        return self._ds.transform

    @property
    def crs(self):
        return self._ds.crs

    @property
    def bounds(self):
        return self._ds.bounds

    @property
    def cell_size_m(self) -> tuple[float, float]:
        """Return cell width and height in map units, before any ground correction."""
        return abs(self.transform.a), abs(self.transform.e)

    def read(self, window: Window) -> NDArray[np.float32]:
        """Read a window as elevations, with missing data set to NaN."""
        z = self._ds.read(1, window=window, boundless=False, out_dtype="float32")
        z = np.array(z, dtype=np.float32)
        missing = ~np.isfinite(z) | (z < _IMPLAUSIBLE_ELEVATION_M)
        if self._ds.nodata is not None and np.isfinite(self._ds.nodata):
            missing |= z == np.float32(self._ds.nodata)
        z[missing] = np.nan
        return z

    def lonlat_to_cell(self, lon: float, lat: float) -> tuple[int, int]:
        """Return the (row, column) containing a longitude and latitude."""
        x, y = self._to_map.transform(lon, lat)
        inverse = ~self.transform
        col = inverse.a * x + inverse.b * y + inverse.c
        row = inverse.d * x + inverse.e * y + inverse.f
        return int(np.floor(row)), int(np.floor(col))

    def cell_to_lonlat(self, row: float, col: float) -> tuple[float, float]:
        """Return the longitude and latitude at the center of a cell."""
        x, y = self._cell_center_xy(col, row)
        lon, lat = self._to_lonlat.transform(x, y)
        return float(lon), float(lat)

    def _cell_center_xy(self, col, row):
        """Return map coordinates of a cell center. Accepts single values or arrays."""
        t = self.transform
        col_mid, row_mid = col + 0.5, row + 0.5
        return t.a * col_mid + t.b * row_mid + t.c, t.d * col_mid + t.e * row_mid + t.f

    def window_around(self, lon: float, lat: float, size: int) -> Window:
        """Return a square window centered on a location, kept inside the map."""
        if size < 1 or size > min(self.width, self.height):
            raise ValueError(f"size {size} doesn't fit a {self.width}x{self.height} map")
        row, col = self.lonlat_to_cell(lon, lat)
        if not (0 <= row < self.height and 0 <= col < self.width):
            raise ValueError(f"({lon}, {lat}) is outside this map")
        col_off = int(np.clip(col - size // 2, 0, self.width - size))
        row_off = int(np.clip(row - size // 2, 0, self.height - size))
        return Window(col_off, row_off, size, size)

    def ground_cell_size(
        self, row_start: int, row_count: int
    ) -> tuple[NDArray[np.float64], float]:
        """Return true ground cell width per row, and the cell height, in meters.

        Equirectangular maps keep east-west cells the same size in map units, but the
        real ground distance shrinks as cos(latitude). At Jezero (about 18.4 degrees N)
        a "1 m" map cell is only about 0.95 m wide. Skipping this correction would
        understate east-west slopes by about 5%.
        """
        rows = np.arange(row_start, row_start + row_count, dtype=np.float64)
        map_x, map_y = self._cell_center_xy(np.zeros_like(rows), rows)
        map_step_x, map_step_y = self.cell_size_m

        lon_a, lat_a = self._to_lonlat.transform(map_x, map_y)
        lon_b, _ = self._to_lonlat.transform(map_x + map_step_x, map_y)
        _, lat_c = self._to_lonlat.transform(map_x, map_y + map_step_y)

        lat_rad = np.radians(np.asarray(lat_a))
        dlon_rad = np.radians(np.abs(np.asarray(lon_b) - np.asarray(lon_a)))
        dx = self.radius_m * np.cos(lat_rad) * dlon_rad
        dy = self.radius_m * np.radians(np.abs(np.asarray(lat_c) - np.asarray(lat_a)))
        return dx, float(np.mean(dy))
