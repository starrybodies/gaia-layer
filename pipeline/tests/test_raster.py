"""Windowed reads, and the one thing they must never do: invent a number.

`read_window` is asked for a rectangle of ground that is usually larger than the raster it
is reading. Copernicus DEM ships one-degree tiles, so nine of them cover the v0.2 study
area and each individual read is mostly outside its own tile. What comes back for the part
the tile does not cover decides whether a mosaic can be assembled at all: if it is NaN the
next tile fills it in, and if it is zero the next tile is silently discarded and the grid
keeps a fabricated sea-level plain.

That is not hypothetical. It is what happened: 38,829 of 43,303 spine cells carried an
elevation of exactly 0.0 m, because the first DEM tile returned zeros everywhere outside
its own degree square and the mosaic step treated a finite zero as data already present.

The fixtures are written to disk rather than fetched, because the failure is purely
geometric — a raster that covers part of a grid — and needs no real terrain to reproduce.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_bounds as transform_from_bounds

from gaia_pipeline.grid import AnalysisGrid
from gaia_pipeline.raster import read_window

#: BC Albers, the v0.2 analysis CRS, and a 6 x 6 km grid inside the study area.
GRID_CRS = "EPSG:3005"

#: A point in the Okanagan, used only to put the fixtures on real ground so that the
#: 4326 -> 3005 reprojection under test is the same shape of transform as in production.
ANCHOR_LAT, ANCHOR_LON = 49.90, -119.50


@pytest.fixture(scope="module")
def grid() -> AnalysisGrid:
    to_albers = Transformer.from_crs("EPSG:4326", GRID_CRS, always_xy=True)
    x, y = to_albers.transform(ANCHOR_LON, ANCHOR_LAT)
    left = float(np.floor(x / 30.0) * 30.0)
    top = float(np.floor(y / 30.0) * 30.0)
    return AnalysisGrid(
        crs=GRID_CRS,
        resolution_m=30.0,
        width=200,
        height=200,
        left=left,
        bottom=top - 6000.0,
        right=left + 6000.0,
        top=top,
    )


def _write(
    path: Path,
    *,
    crs: str,
    bounds: tuple[float, float, float, float],
    shape: tuple[int, int],
    value: float,
    nodata: float | None = None,
) -> str:
    """A constant-valued GeoTIFF over the given bounds. Returns the path as a URL string."""
    height, width = shape
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform_from_bounds(*bounds, width, height),
        nodata=nodata,
    ) as dst:
        dst.write(np.full((height, width), value, dtype="float32"), 1)
    return str(path)


def _wgs84_bounds(grid: AnalysisGrid) -> tuple[float, float, float, float]:
    to_wgs84 = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    xs, ys = [], []
    for x, y in ((grid.left, grid.bottom), (grid.right, grid.bottom), (grid.right, grid.top)):
        lon, lat = to_wgs84.transform(x, y)
        xs.append(lon)
        ys.append(lat)
    lon0, lon1 = min(xs), max(xs)
    lat0, lat1 = min(ys), max(ys)
    return (lon0, lat0, lon1, lat1)


class TestGroundTheSourceDoesNotCover:
    def test_a_same_crs_source_covering_half_the_grid_leaves_the_rest_missing(
        self, grid, tmp_path
    ) -> None:
        """A raster that stops halfway across the grid has said nothing about the far half."""
        url = _write(
            tmp_path / "west.tif",
            crs=GRID_CRS,
            bounds=(grid.left, grid.bottom, grid.left + 3000.0, grid.top),
            shape=(100, 100),
            value=1000.0,
        )

        out = read_window(url, grid)

        assert np.isfinite(out[:, :100]).all()
        assert np.isclose(out[:, :100], 1000.0).all()
        assert np.isnan(out[:, 100:]).all()

    def test_a_reprojected_source_covering_part_of_the_grid_leaves_the_rest_missing(
        self, grid, tmp_path
    ) -> None:
        """The Copernicus DEM case: a 4326 tile reprojected onto a metric grid it underfills."""
        west, south, east, north = _wgs84_bounds(grid)
        url = _write(
            tmp_path / "tile.tif",
            crs="EPSG:4326",
            bounds=(west - 0.01, south - 0.01, west + (east - west) * 0.4, north + 0.01),
            shape=(240, 240),
            value=1000.0,
        )

        out = read_window(url, grid)

        assert np.isclose(out[:, :60], 1000.0).all()
        assert np.isnan(out[:, 130:]).all()
        assert not (out[np.isfinite(out)] == 0.0).any()

    def test_a_source_that_misses_the_grid_entirely_reads_as_all_missing(
        self, grid, tmp_path
    ) -> None:
        """No overlap is an answer of "nothing", not a grid of zeroes."""
        url = _write(
            tmp_path / "elsewhere.tif",
            crs=GRID_CRS,
            bounds=(grid.left + 60_000.0, grid.bottom, grid.left + 66_000.0, grid.top),
            shape=(100, 100),
            value=1000.0,
        )

        out = read_window(url, grid)

        assert out.shape == grid.shape
        assert np.isnan(out).all()


class TestMosaicking:
    def test_two_tiles_covering_half_the_grid_each_assemble_into_one_surface(
        self, grid, tmp_path
    ) -> None:
        """The merge in `terrain_features`, which only works if uncovered ground is NaN."""
        west, south, east, north = _wgs84_bounds(grid)
        middle = west + (east - west) / 2.0
        tiles = [
            _write(
                tmp_path / "left.tif",
                crs="EPSG:4326",
                bounds=(west - 0.01, south - 0.01, middle, north + 0.01),
                shape=(240, 240),
                value=1000.0,
            ),
            _write(
                tmp_path / "right.tif",
                crs="EPSG:4326",
                bounds=(middle, south - 0.01, east + 0.01, north + 0.01),
                shape=(240, 240),
                value=2000.0,
            ),
        ]

        mosaic = np.full(grid.shape, np.nan, dtype="float32")
        for tile in tiles:
            patch = read_window(tile, grid)
            mosaic = np.where(np.isfinite(mosaic), mosaic, patch)

        assert np.isfinite(mosaic).mean() > 0.99
        assert set(np.unique(mosaic[np.isfinite(mosaic)]).tolist()) <= {1000.0, 2000.0}
        assert np.isclose(mosaic[:, :80], 1000.0).all()
        assert np.isclose(mosaic[:, 120:], 2000.0).all()


class TestNodataInsideTheFootprint:
    def test_a_declared_nodata_value_still_reads_as_missing(self, grid, tmp_path) -> None:
        """Unchanged behaviour, asserted so the coverage fix cannot quietly drop it."""
        url = _write(
            tmp_path / "void.tif",
            crs=GRID_CRS,
            bounds=grid.bounds,
            shape=(200, 200),
            value=-32767.0,
            nodata=-32767.0,
        )

        out = read_window(url, grid)

        assert np.isnan(out).all()

    def test_a_real_zero_survives_a_read_that_runs_off_the_source(self, grid, tmp_path) -> None:
        """Bare ground is a canopy height of zero, and sea level is an elevation of zero.

        The coverage mask has to come from geometry rather than from the fill value, or a
        source whose measurements include zero loses them at exactly the point where the
        read also has ground to refuse.
        """
        height, width = 100, 100
        values = np.full((height, width), 5.0, dtype="float32")
        values[:50] = 0.0
        path = tmp_path / "bare.tif"
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=height,
            width=width,
            count=1,
            dtype="float32",
            crs=GRID_CRS,
            transform=transform_from_bounds(
                grid.left, grid.bottom, grid.left + 3000.0, grid.top, width, height
            ),
        ) as dst:
            dst.write(values, 1)

        out = read_window(str(path), grid)

        assert np.isfinite(out[:, :100]).all()
        assert (out[:100, :100] == 0.0).all()
        assert (out[100:, :100] == 5.0).all()
        assert np.isnan(out[:, 100:]).all()

    def test_a_covered_grid_is_fully_measured(self, grid, tmp_path) -> None:
        """The ordinary case: a source larger than the grid loses nothing to the mask."""
        url = _write(
            tmp_path / "everywhere.tif",
            crs=GRID_CRS,
            bounds=(
                grid.left - 3000.0,
                grid.bottom - 3000.0,
                grid.right + 3000.0,
                grid.top + 3000.0,
            ),
            shape=(400, 400),
            value=742.0,
        )

        out = read_window(url, grid)

        assert np.isclose(out, 742.0).all()
