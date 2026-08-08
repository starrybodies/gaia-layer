"""Raster IO: windowed reads from remote COGs, and writing our own.

The pipeline never downloads a whole Sentinel-2 tile. `read_window` asks rasterio for the
rectangle of a remote cloud-optimised GeoTIFF that covers the analysis grid, and rasterio
turns that into HTTP range requests against the file's internal tiling. A 110 x 110 km scene
becomes a few megabytes. This is the single reason a twelve-month ingest is feasible on a
Mac mini.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.io import MemoryFile
from rasterio.shutil import copy as rio_copy
from rasterio.warp import reproject
from rasterio.windows import from_bounds
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .grid import AnalysisGrid

log = logging.getLogger(__name__)

# GDAL settings that make remote COG reads behave. Without these, GDAL will happily fetch
# far more of a file than it needs, or stall on a directory listing that never comes.
GDAL_ENV: dict[str, Any] = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff",
    "GDAL_HTTP_MULTIPLEX": True,
    "GDAL_HTTP_VERSION": "2",
    "GDAL_HTTP_MAX_RETRY": 5,
    "GDAL_HTTP_RETRY_DELAY": 2,
    "VSI_CACHE": True,
    "VSI_CACHE_SIZE": 64 * 1024 * 1024,
    "GDAL_CACHEMAX": 512,
}


class WindowReadError(RuntimeError):
    """A remote raster could not be read after retries."""


@retry(
    retry=retry_if_exception_type((RasterioIOError, OSError)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def read_window(
    url: str,
    grid: AnalysisGrid,
    *,
    resampling: Resampling = Resampling.average,
    dtype: str = "float32",
) -> np.ndarray:
    """Read the analysis-grid window of a remote raster, resampled onto that grid.

    ``resampling`` should be ``average`` for continuous reflectance — it is the area-weighted
    mean that the plan calls for — and ``nearest`` for anything categorical, where averaging
    class codes would produce classes that do not exist.
    """
    with rasterio.Env(**GDAL_ENV), rasterio.open(url) as src:
        if str(src.crs) == grid.crs:
            window = from_bounds(*grid.bounds, transform=src.transform)
            data = src.read(
                1,
                window=window,
                out_shape=grid.shape,
                resampling=resampling,
                boundless=True,
                fill_value=src.nodata if src.nodata is not None else 0,
            )
            out = data.astype(dtype)
        else:
            # Different projection: reproject the window rather than the whole scene.
            window = from_bounds(*_bounds_in(src.crs, grid), transform=src.transform)
            source = src.read(1, window=window, boundless=True, fill_value=0)
            out = np.zeros(grid.shape, dtype=dtype)
            reproject(
                source=source,
                destination=out,
                src_transform=src.window_transform(window),
                src_crs=src.crs,
                dst_transform=grid.transform,
                dst_crs=grid.crs,
                resampling=resampling,
            )

        nodata = src.nodata
        if nodata is not None:
            out = np.where(np.isclose(out, float(nodata)), np.nan, out)
        return np.asarray(out, dtype=dtype)


def _bounds_in(crs: Any, grid: AnalysisGrid) -> tuple[float, float, float, float]:
    from pyproj import Transformer

    transformer = Transformer.from_crs(grid.crs, str(crs), always_xy=True)
    xs: list[float] = []
    ys: list[float] = []
    for x, y in (
        (grid.left, grid.bottom),
        (grid.right, grid.bottom),
        (grid.right, grid.top),
        (grid.left, grid.top),
    ):
        px, py = transformer.transform(x, y)
        xs.append(px)
        ys.append(py)
    return (min(xs), min(ys), max(xs), max(ys))


def write_cog(path: Path, data: np.ndarray, grid: AnalysisGrid, *, description: str = "") -> Path:
    """Write an array to a cloud-optimised GeoTIFF on the analysis grid.

    NaN is the nodata value throughout. Using a sentinel number instead would eventually be
    read as data by something that did not know the convention.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    array = np.ascontiguousarray(data.astype("float32"))

    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "nodata": np.nan,
        "width": grid.width,
        "height": grid.height,
        "count": 1,
        "crs": grid.crs,
        "transform": grid.transform,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "compress": "deflate",
        "predictor": 3,
        "zlevel": 6,
    }

    with MemoryFile() as memfile:
        with memfile.open(**profile) as tmp:
            tmp.write(array, 1)
            if description:
                tmp.set_band_description(1, description)
            tmp.build_overviews([2, 4, 8, 16], Resampling.average)
        with memfile.open() as tmp:
            rio_copy(tmp, str(path), driver="GTiff", copy_src_overviews=True, **profile)

    return path


def read_cog(path: Path) -> np.ndarray:
    with rasterio.open(path) as src:
        return np.asarray(src.read(1), dtype="float32")


def sample_cog(path: Path, x: float, y: float) -> float:
    """Sample one pixel by projected coordinate. Returns NaN outside the raster."""
    with rasterio.open(path) as src:
        row, col = src.index(x, y)
        if not (0 <= row < src.height and 0 <= col < src.width):
            return float("nan")
        value = src.read(1, window=((row, row + 1), (col, col + 1)))
        return float(value[0, 0])
