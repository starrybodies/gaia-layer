"""Raster IO: windowed reads from remote COGs, and writing our own.

The pipeline never downloads a whole Sentinel-2 tile. `read_window` asks rasterio for the
rectangle of a remote cloud-optimised GeoTIFF that covers the analysis grid, and rasterio
turns that into HTTP range requests against the file's internal tiling. A 110 x 110 km scene
becomes a few megabytes. This is the single reason a twelve-month ingest is feasible on a
Mac mini.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.io import MemoryFile
from rasterio.shutil import copy as rio_copy
from rasterio.warp import reproject
from rasterio.windows import Window, WindowError, from_bounds, intersection
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

    Ground the source does not cover comes back NaN, never a fill value. The grid is
    routinely larger than the raster being read — nine Copernicus DEM tiles cover the v0.2
    study area, so eight ninths of every DEM read is outside its own tile — and a source
    with no declared nodata would otherwise hand back zeroes there. A zero elevation is
    indistinguishable from a measured valley floor, so it survives every downstream sanity
    check and, worse, makes the next tile in a mosaic look redundant. ``dtype`` therefore
    has to be a floating type; NaN is the only way to say "not measured here".
    """
    with rasterio.Env(**GDAL_ENV), rasterio.open(url) as src:
        if str(src.crs) == grid.crs:
            out = _read_aligned(src, grid, resampling=resampling, dtype=dtype)
        else:
            out = _read_reprojected(src, grid, resampling=resampling, dtype=dtype)

        nodata = src.nodata
        if nodata is not None:
            out = np.where(np.isclose(out, float(nodata)), np.nan, out)
        return np.asarray(out, dtype=dtype)


def _read_aligned(
    src: Any, grid: AnalysisGrid, *, resampling: Resampling, dtype: str
) -> np.ndarray:
    """Decimated read of a source already in the grid's CRS.

    ``out_shape`` is what lets GDAL serve this from the file's overviews rather than the
    full-resolution pixels, which is the reason a twelve-month ingest is feasible, so the
    boundless read stays and the coverage is worked out separately.

    It is worked out arithmetically rather than taken from rasterio's own boundless mask
    because that mask is built by giving the fill value to a VRT as its nodata, which would
    also mask any real pixel that happens to equal the fill. A canopy height of zero is
    bare ground and an elevation of zero is sea level; neither is absence.

    A grid pixel counts as covered only when the whole block of source pixels it averages
    lies inside the source. Half-covered pixels at the edge are refused rather than
    averaged against the fill, which costs one pixel line at the boundary and fabricates
    nothing. The next tile in a mosaic covers it.
    """
    window = from_bounds(*grid.bounds, transform=src.transform)
    data = src.read(
        1,
        window=window,
        out_shape=grid.shape,
        resampling=resampling,
        boundless=True,
        fill_value=0,
    )

    per_col = window.width / grid.width
    per_row = window.height / grid.height
    starts_col = window.col_off + np.arange(grid.width) * per_col
    starts_row = window.row_off + np.arange(grid.height) * per_row
    inside_col = (starts_col >= 0.0) & (starts_col + per_col <= src.width)
    inside_row = (starts_row >= 0.0) & (starts_row + per_row <= src.height)

    covered = inside_row[:, None] & inside_col[None, :]
    return np.where(covered, data.astype(dtype), np.nan).astype(dtype)


def _read_reprojected(
    src: Any, grid: AnalysisGrid, *, resampling: Resampling, dtype: str
) -> np.ndarray:
    """Reproject the source's own overlap with the grid, leaving the rest missing.

    The window is clipped to the source rather than read boundlessly: outside its own
    extent there is nothing to reproject, and reading it would only carry a fill value
    across the warp. The destination starts as NaN and GDAL writes only where the source
    reaches, so uncovered ground keeps saying nothing.
    """
    out = np.full(grid.shape, np.nan, dtype=dtype)

    asked = from_bounds(*_bounds_in(src.crs, grid), transform=src.transform)
    # Out to whole pixels and one further, so the warp has neighbours to interpolate from
    # at the edge of the grid rather than losing its last row and column to rounding.
    left = math.floor(asked.col_off) - 1
    top = math.floor(asked.row_off) - 1
    right = math.ceil(asked.col_off + asked.width) + 1
    bottom = math.ceil(asked.row_off + asked.height) + 1
    try:
        window = intersection(
            Window(left, top, right - left, bottom - top), Window(0, 0, src.width, src.height)
        )
    except WindowError:
        return out
    if window.width <= 0 or window.height <= 0:
        return out

    reproject(
        source=src.read(1, window=window),
        destination=out,
        src_transform=src.window_transform(window),
        src_crs=src.crs,
        dst_transform=grid.transform,
        dst_crs=grid.crs,
        src_nodata=src.nodata,
        dst_nodata=np.nan,
        resampling=resampling,
    )
    return out


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

    # Creation options travel to the final copy; the geospatial profile comes from the
    # in-memory source, so passing it again would collide.
    creation_options = {
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "compress": "deflate",
        "predictor": 3,
        # Level 1 rather than 6: on a 10-megapixel float grid the extra levels cost
        # more wall clock during a twelve-month ingest than they save in disk.
        "zlevel": 1,
    }

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
        "zlevel": 1,
    }

    with MemoryFile() as memfile:
        with memfile.open(**profile) as tmp:
            tmp.write(array, 1)
            if description:
                tmp.set_band_description(1, description)
            tmp.build_overviews([2, 4, 8], Resampling.average)
        with memfile.open() as tmp:
            rio_copy(tmp, str(path), driver="GTiff", copy_src_overviews=True, **creation_options)

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
