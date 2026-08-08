"""The analysis grid.

Every raster the pipeline produces lands on one grid per area of interest, so indices from
different sensors and different months are pixel-aligned and can be compared without
resampling at query time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pyproj import Transformer
from rasterio.transform import Affine

from .config import AreaOfInterest
from .schemas.common import BBox


@dataclass(frozen=True)
class AnalysisGrid:
    """A snapped, metric raster grid covering an area of interest."""

    crs: str
    resolution_m: float
    width: int
    height: int
    left: float
    bottom: float
    right: float
    top: float

    @property
    def transform(self) -> Affine:
        return Affine(self.resolution_m, 0.0, self.left, 0.0, -self.resolution_m, self.top)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (self.left, self.bottom, self.right, self.top)

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def pixel_count(self) -> int:
        return self.width * self.height

    def pixel_area_km2(self) -> float:
        return (self.resolution_m * self.resolution_m) / 1_000_000.0

    def bounds_wgs84(self) -> BBox:
        transformer = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)
        west, south = transformer.transform(self.left, self.bottom)
        east, north = transformer.transform(self.right, self.top)
        return BBox(west=west, south=south, east=east, north=north)


def grid_for(aoi: AreaOfInterest) -> AnalysisGrid:
    """Build the analysis grid for an area of interest.

    Bounds are snapped outward to whole multiples of the resolution. Snapping keeps the grid
    identical no matter how the area's bounding box was rounded upstream, which is what lets
    a re-ingest land on exactly the same pixels as the first one.
    """
    bbox = aoi.bbox()
    transformer = Transformer.from_crs("EPSG:4326", aoi.analysis_crs, always_xy=True)

    # Project all four corners, not just two: the projected footprint of a lat/lon box is
    # not itself a box, and taking only the diagonal corners would clip the bulging edges.
    xs: list[float] = []
    ys: list[float] = []
    for lon, lat in (
        (bbox.west, bbox.south),
        (bbox.east, bbox.south),
        (bbox.east, bbox.north),
        (bbox.west, bbox.north),
    ):
        x, y = transformer.transform(lon, lat)
        xs.append(x)
        ys.append(y)

    res = aoi.grid_resolution_m
    left = math.floor(min(xs) / res) * res
    bottom = math.floor(min(ys) / res) * res
    right = math.ceil(max(xs) / res) * res
    top = math.ceil(max(ys) / res) * res

    return AnalysisGrid(
        crs=aoi.analysis_crs,
        resolution_m=res,
        width=round((right - left) / res),
        height=round((top - bottom) / res),
        left=left,
        bottom=bottom,
        right=right,
        top=top,
    )
