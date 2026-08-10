"""Canopy height, read from the GLAD global forest height mosaic.

Structure is half of what separates a stand that burns from one that does not, and the first
thing to know about structure is how tall the trees are.

The plan named ETH's 10 m global canopy height for this. That product's published path,
`share.phys.ethz.ch/~pf/nlangdata/`, now redirects the whole directory to its DOI, and the
Research Collection page behind the DOI answers a programmatic client with 403. A Nextcloud
share link still serves the 3-degree tiles by byte range, but a decade of provenance should
not hang on a file-sharing link that has already moved once. GLAD's mosaic is served from a
static URL, and at 30 m it is the analysis grid's own resolution, so the read is a resample
between like and like rather than a threefold coarsening of something finer.

One thing about the read. The mosaic is a 5.7 GB LZW GeoTIFF that is striped rather than
tiled — one scanline of 436,004 pixels per block — so a windowed read still works over HTTP,
but the smallest thing it can ask for is a whole row of North America. That is roughly 36 kB
compressed per row against the 6,400 rows the study area spans, so a full pass costs a few
hundred megabytes where a tiled COG would cost a few. Affordable for a layer with one epoch,
read once; it would not be for a monthly series.
"""

from __future__ import annotations

import logging

import numpy as np
from rasterio.enums import Resampling

from ...raster import read_window
from ..archive import MethodRecord, SourceRecord
from ..spine import Spine

log = logging.getLogger(__name__)

MOSAIC_URL = "https://glad.umd.edu/Potapov/Forest_height_2019/Forest_height_2019_NAM.tif"

CITATION = (
    "Potapov, P. et al. (2021). Mapping global forest canopy height through integration of "
    "GEDI and Landsat data. Remote Sensing of Environment 253:112165."
)
DOI = "10.1016/j.rse.2020.112165"

#: The product's ceiling, and the line between a measurement and a flag. GLAD stores height
#: in metres in a single byte, 0-60, and reserves the codes above that for conditions rather
#: than trees: 101 water, 102 snow and ice, 103 no data. Over the study window only 101
#: appears, on Okanagan Lake and the smaller lakes around it, at about 2 per cent of pixels.
MAX_HEIGHT_M = 60.0

CANOPY_METHOD = MethodRecord(
    method_id="canopy_height_glad_2019",
    name="Canopy height from the GLAD global forest height mosaic",
    citation=CITATION,
    doi=DOI,
    version="2019 mosaic, 2026 read",
    formula="area-weighted mean of 30 m source pixels; any pixel a flag contributed to is dropped",
    notes=(
        "Substituted for the ETH 10 m canopy height named in the plan, whose published path "
        "now redirects to a DOI landing page that refuses programmatic clients. GLAD is 30 m "
        "native, which is the analysis grid, and 2019, which sits inside the study decade "
        "but is a single epoch: a cell's height is the same number in every period, so it "
        "describes where a stand was in 2019 and not how it changed."
    ),
)


def fetch(spine: Spine) -> tuple[np.ndarray, SourceRecord]:
    """Canopy height in metres on the spine's grid. NaN where the source has no data.

    The mosaic declares no nodata value, so a grid reaching past its edge would come back as
    zero height rather than as missing. Nothing here relies on that going unnoticed: the
    North American tile runs from 13 to 52 degrees north and the study area sits inside it.
    """
    grid = spine.grid

    # Height is continuous, so an output pixel is the area-weighted mean of the source pixels
    # under it. Nearest would keep one of them and discard the rest of the evidence.
    heights = read_window(MOSAIC_URL, grid, resampling=Resampling.average, dtype="float32")

    # Averaging is also what makes the flags dangerous. They sit in the same band as the
    # measurements, so a mean taken across a shoreline blends 101 m of lake into the trees
    # beside it: one water pixel and three 20 m stands arrive as a plausible, entirely
    # fictional 40 m canopy. This second pass asks a different question — did anything
    # flagged contribute to this pixel at all — by taking the largest source value behind it
    # instead of the average. It costs one more sweep of the same rows, and it turns the
    # shoreline into pixels that are missing rather than pixels that are wrong.
    peak = read_window(MOSAIC_URL, grid, resampling=Resampling.max, dtype="float32")

    measured = peak <= MAX_HEIGHT_M
    log.info("canopy: %.1f%% of the window is flagged", 100.0 * float(1.0 - measured.mean()))

    return np.asarray(np.where(measured, heights, np.nan), dtype="float32"), _source()


def cell_heights(spine: Spine) -> tuple[np.ndarray, np.ndarray, SourceRecord]:
    """Per-cell mean canopy height and the fraction of the cell that carried data.

    The fraction is what tells a reader whether a cell's mean stands on a whole hex of forest
    or on the strip of it that was not lake.
    """
    heights, source = fetch(spine)
    means, fraction = spine.mean(heights)
    return means, fraction, source


def _source() -> SourceRecord:
    return SourceRecord(
        dataset="GLAD forest height",
        version="2019",
        access_route="https-mosaic",
        uri=MOSAIC_URL,
        citation=CITATION,
        native_resolution_m=30.0,
        native_timestep="single epoch (2019)",
        licence="not stated by the publisher",
    )
