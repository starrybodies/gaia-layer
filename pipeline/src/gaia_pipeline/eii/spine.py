"""The H3 spine: hexagonal cells, and the one integer array everything is aggregated through.

The obvious way to put a raster onto H3 cells is to ask, for every pixel, which cell it
falls in. At 30 m over 28,000 km2 that is thirty million calls into a Python-level H3
binding, which takes half an hour and has to be repeated for every layer.

So it is done once, backwards. The cells are polygons; `rasterio.features.rasterize` burns
polygons into an array in C. The result is an index raster holding, for each pixel, the row
number of the cell that owns it, or -1 for nothing. Cached to disk, it turns every
subsequent aggregation into a `np.bincount`, which is a few tens of milliseconds.

Everything downstream — components, targets, features, the riparian fraction, the majority
class of a categorical layer — goes through the three aggregators at the bottom of this
file. They share one convention: a cell with no evidence behind it comes back NaN with a
valid fraction of zero, never a zero pretending to be a measurement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import h3
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import rasterio
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import Affine
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.ops import transform as shapely_transform

from ..config import AreaOfInterest
from ..grid import AnalysisGrid
from .area import EII_CRS, EII_RESOLUTION_M, H3_PARENT_RES, H3_RES

log = logging.getLogger(__name__)

#: Nominal area of a resolution-8 cell, used only for sanity bounds and reporting.
CELL_AREA_KM2 = 0.737327598


def build_cells(area: AreaOfInterest) -> pa.Table:
    """Every resolution-8 cell whose centre falls inside the area.

    H3's polygon fill is centroid-based, so the cell set has a ragged edge that does not
    exactly follow the polygon. That is the honest behaviour: a cell is either in the index
    or it is not, and a half-covered cell on the boundary would carry a mean over whatever
    part of it happened to be inside.
    """
    ring = [(lat, lon) for lon, lat in _exterior(area)]
    shape = h3.LatLngPoly(ring)
    ids = sorted(h3.h3shape_to_cells(shape, H3_RES))
    return _cell_table(ids)


def _exterior(area: AreaOfInterest) -> list[tuple[float, float]]:
    geometry = area.geometry
    if geometry.type == "Polygon":
        return [(float(x), float(y)) for x, y in geometry.coordinates[0]]
    return [(float(x), float(y)) for x, y in geometry.coordinates[0][0]]


def _cell_table(ids: list[str]) -> pa.Table:
    latlng = [h3.cell_to_latlng(cell) for cell in ids]
    return pa.table(
        {
            "h3": pa.array(ids, pa.string()),
            "res": pa.array([H3_RES] * len(ids), pa.uint8()),
            "parent_h3": pa.array(
                [h3.cell_to_parent(cell, H3_PARENT_RES) for cell in ids], pa.string()
            ),
            "lat": pa.array([lat for lat, _ in latlng], pa.float64()),
            "lon": pa.array([lon for _, lon in latlng], pa.float64()),
            "area_km2": pa.array([h3.cell_area(cell, unit="km^2") for cell in ids], pa.float32()),
        }
    )


def _grid_for_cells(cells: pa.Table) -> AnalysisGrid:
    """A metric grid snapped outward around the cell set, with a one-cell margin."""
    transformer = Transformer.from_crs("EPSG:4326", EII_CRS, always_xy=True)

    xs: list[float] = []
    ys: list[float] = []
    for cell in cells.column("h3").to_pylist():
        for lat, lon in h3.cell_to_boundary(cell):
            x, y = transformer.transform(lon, lat)
            xs.append(x)
            ys.append(y)

    res = EII_RESOLUTION_M
    left = np.floor(min(xs) / res) * res - res
    right = np.ceil(max(xs) / res) * res + res
    bottom = np.floor(min(ys) / res) * res - res
    top = np.ceil(max(ys) / res) * res + res

    return AnalysisGrid(
        crs=EII_CRS,
        resolution_m=res,
        width=round((right - left) / res),
        height=round((top - bottom) / res),
        left=float(left),
        bottom=float(bottom),
        right=float(right),
        top=float(top),
    )


def _index_raster(cells: pa.Table, grid: AnalysisGrid) -> np.ndarray:
    """Burn each cell's boundary into an array of row indices.

    Rasterisation assigns a pixel to whichever polygon covers its centre, so shared hex
    edges resolve to exactly one owner and the tiling stays a partition.
    """
    to_metres = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True).transform

    shapes = []
    for row, cell in enumerate(cells.column("h3").to_pylist()):
        boundary = [(lon, lat) for lat, lon in h3.cell_to_boundary(cell)]
        polygon = shapely_transform(to_metres, ShapelyPolygon(boundary))
        shapes.append((polygon, row))

    transform = Affine(grid.resolution_m, 0.0, grid.left, 0.0, -grid.resolution_m, grid.top)
    burned: np.ndarray = rasterize(
        shapes,
        out_shape=grid.shape,
        transform=transform,
        fill=-1,
        dtype="int32",
        all_touched=False,
    )
    return burned


@dataclass(frozen=True)
class Spine:
    """A cell set, the grid it is measured on, and the index that joins the two."""

    cells: pa.Table
    grid: AnalysisGrid
    index: np.ndarray
    cache_dir: Path

    @property
    def n_cells(self) -> int:
        return int(self.cells.num_rows)

    @property
    def pixels_per_cell(self) -> np.ndarray:
        """How many grid pixels each cell owns. The denominator for every fraction."""
        flat = self.index.reshape(-1)
        return np.bincount(flat[flat >= 0], minlength=self.n_cells).astype("float64")

    # ------------------------------------------------------------------ construction

    @classmethod
    def build(cls, area: AreaOfInterest, cache_dir: Path) -> Spine:
        return cls._assemble(build_cells(area), cache_dir)

    @classmethod
    def for_cells(cls, ids: list[str], cache_dir: Path) -> Spine:
        return cls._assemble(_cell_table(sorted(ids)), cache_dir)

    @classmethod
    def _assemble(cls, cells: pa.Table, cache_dir: Path) -> Spine:
        cache_dir.mkdir(parents=True, exist_ok=True)
        grid = _grid_for_cells(cells)

        key = f"{cells.num_rows}-{int(grid.left)}-{int(grid.top)}-{grid.width}x{grid.height}"
        index_path = cache_dir / f"index-{key}.tif"
        cells_path = cache_dir / f"cells-{key}.parquet"

        if index_path.exists() and cells_path.exists():
            with rasterio.open(index_path) as src:
                index = src.read(1)
            return cls(pq.read_table(cells_path), grid, index, cache_dir)

        log.info("rasterising %d cells onto %dx%d", cells.num_rows, grid.width, grid.height)
        index = _index_raster(cells, grid)

        profile = {
            "driver": "GTiff",
            "height": grid.height,
            "width": grid.width,
            "count": 1,
            "dtype": "int32",
            "crs": grid.crs,
            "transform": Affine(
                grid.resolution_m, 0.0, grid.left, 0.0, -grid.resolution_m, grid.top
            ),
            "compress": "deflate",
            "tiled": True,
            "nodata": -1,
        }
        with rasterio.open(index_path, "w", **profile) as dst:
            dst.write(index, 1)
        pq.write_table(cells, cells_path)

        return cls(cells, grid, index, cache_dir)

    # ------------------------------------------------------------------- aggregation

    def _valid(self, values: np.ndarray, mask: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
        """Flattened cell indices and values for pixels that carry a measurement."""
        flat_index = self.index.reshape(-1)
        flat_values = values.reshape(-1)

        keep = (flat_index >= 0) & np.isfinite(flat_values)
        if mask is not None:
            keep &= mask.reshape(-1)

        return flat_index[keep], flat_values[keep]

    def mean(
        self, values: np.ndarray, mask: np.ndarray | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """Per-cell mean, and the fraction of the cell that carried data.

        The fraction is not decoration. A cell assembled from three valid pixels and one
        assembled from eight hundred are different claims, and the consumer needs to be able
        to tell them apart.
        """
        index, vals = self._valid(values, mask)

        totals = np.bincount(index, weights=vals, minlength=self.n_cells)
        counts = np.bincount(index, minlength=self.n_cells).astype("float64")

        with np.errstate(invalid="ignore", divide="ignore"):
            means = np.where(counts > 0, totals / np.maximum(counts, 1), np.nan)

        return means.astype("float32"), (counts / np.maximum(self.pixels_per_cell, 1)).astype(
            "float32"
        )

    def majority(
        self, values: np.ndarray, mask: np.ndarray | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """Per-cell modal class, and the share of the cell it covers.

        Class codes are labels. Averaging them produces a different label, which is a real
        code and a wrong answer, so categorical layers come through here instead.
        """
        index, vals = self._valid(values, mask)
        if index.size == 0:
            return (
                np.full(self.n_cells, np.nan, dtype="float32"),
                np.zeros(self.n_cells, dtype="float32"),
            )

        codes = vals.astype("int64")
        classes, compact = np.unique(codes, return_inverse=True)

        # One bin per (cell, class) pair, counted in a single pass.
        pairs = index.astype("int64") * classes.size + compact
        counts = np.bincount(pairs, minlength=self.n_cells * classes.size).reshape(
            self.n_cells, classes.size
        )

        best = counts.argmax(axis=1)
        best_count = counts[np.arange(self.n_cells), best].astype("float64")
        present = best_count > 0

        winner = np.where(present, classes[best], np.nan).astype("float32")
        share = (best_count / np.maximum(self.pixels_per_cell, 1)).astype("float32")

        return winner, share

    def fraction(self, flag: np.ndarray) -> np.ndarray:
        """Per-cell share of pixels where a boolean condition holds."""
        flat_index = self.index.reshape(-1)
        keep = (flat_index >= 0) & flag.reshape(-1)
        counts = np.bincount(flat_index[keep], minlength=self.n_cells).astype("float64")
        share: np.ndarray = (counts / np.maximum(self.pixels_per_cell, 1)).astype("float32")
        return share
