"""Burn severity from Landsat, which is the thing v0.2 is trying to explain.

The prompt this build follows says to cut raw-imagery pipelines and use pre-aggregated
products. That holds for every input, and it fails for the target: no open Canadian burn
severity product covers 2015-2024, so severity has to be measured. Everything else in v0.2
is read from someone else's mosaic; this module is the one place we touch scenes.

Two decisions keep the cost sane.

The first is that severity is only computed inside fire perimeters. Nothing outside a
perimeter has a severity — it did not burn — and the burned area in a bad year here is a
few hundred square kilometres against a study area of twenty-eight thousand. Working
perimeter by perimeter is a hundredfold less imagery than compositing the whole region, and
it is also the more honest framing: the model answers "given this ground burned, did it burn
severely", so it should only ever see ground that burned.

The second is the one-year extended assessment. Pre-fire and post-fire composites are taken
from the growing seasons either side of the fire year rather than from adjacent scenes.
Immediate post-fire imagery measures ash and scorch, which wash off; a year later measures
what actually died, which is what severity means and what the literature calibrates against.

Bands are Landsat Collection 2 Level 2 surface reflectance through Earth Search, which needs
no account. NBR is (NIR - SWIR2) / (NIR + SWIR2).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

import numpy as np
import rasterio
from pyproj import Transformer
from pystac import Item
from pystac_client import Client
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.warp import reproject

from ..archive import MethodRecord, SourceRecord

log = logging.getLogger(__name__)

STAC_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION = "landsat-c2-l2"

#: Landsat 8 and 9 band names in Earth Search's asset dictionary.
NIR_ASSET = "nir08"
SWIR2_ASSET = "swir22"
QA_ASSET = "qa_pixel"

#: Scenes are read concurrently. The work is almost entirely waiting on HTTP range requests
#: and GDAL releases the GIL while it does that, so threads are the right tool and the
#: ceiling is the server's patience rather than the CPU.
SCENE_WORKERS = 8

#: Growing-season window. Late enough that snow has gone from the valleys, early enough that
#: it closes before autumn senescence starts moving NBR for reasons that are not fire.
SEASON_START = (6, 1)
SEASON_END = (9, 15)

#: Collection 2 Level 2 surface reflectance is stored as scaled integers.
REFLECTANCE_SCALE = 0.0000275
REFLECTANCE_OFFSET = -0.2

#: QA_PIXEL bits that mean the pixel is not clear ground: fill, dilated cloud, cirrus,
#: cloud, cloud shadow, snow.
QA_REJECT_BITS = (0, 1, 2, 3, 4, 5)

#: Key and Benson's composite burn index breakpoints for scaled dNBR, which are the most
#: widely used severity classes in North America. The high-severity class starts at 660.
#:
#: These are landscape-scale conventions rather than a calibration for the Interior
#: Douglas-fir zone specifically, and the validation report says so. Recalibrating them
#: against field CBI plots would be a better threshold and is not something open data
#: supports here.
DNBR_UNBURNED = 100.0
DNBR_LOW = 270.0
DNBR_MODERATE_LOW = 440.0
DNBR_MODERATE_HIGH = 660.0
HIGH_SEVERITY_DNBR = DNBR_MODERATE_HIGH

SEVERITY_METHOD = MethodRecord(
    method_id="dnbr_rbr_extended_v1",
    name="Differenced and relativised burn ratio, one-year extended assessment",
    citation=(
        "Key, C.H. and Benson, N.C. (2006). Landscape assessment: sampling and analysis "
        "methods. USDA Forest Service RMRS-GTR-164-CD. Parks, S.A., Dillon, G.K. and "
        "Miller, C. (2014). A new metric for quantifying burn severity: the relativized "
        "burn ratio. Remote Sensing 6(3):1827-1844. doi:10.3390/rs6031827"
    ),
    doi="10.3390/rs6031827",
    version="1",
    formula="dNBR = (NBR_pre - NBR_post) * 1000; RBR = dNBR / (NBR_pre + 1.001)",
    notes=(
        "Growing-season median composites either side of the fire year. RBR is reported "
        "alongside dNBR because dNBR understates severity in sparse pre-fire vegetation, "
        "which is much of the dry Okanagan. The high-severity threshold is Key and Benson's "
        "660, a landscape-scale convention rather than a local CBI calibration."
    ),
)


@dataclass(frozen=True)
class SeverityWindow:
    """A patch of the analysis grid covering one fire, with its severity surfaces."""

    dnbr: np.ndarray
    rbr: np.ndarray
    nbr_pre: np.ndarray
    nbr_post: np.ndarray
    transform: Affine
    crs: str
    observations_pre: int
    observations_post: int


def _season(year: int) -> tuple[date, date]:
    return date(year, *SEASON_START), date(year, *SEASON_END)


def _clear_mask(qa: np.ndarray) -> np.ndarray:
    """True where QA_PIXEL says the pixel is clear ground.

    Cloud shadow matters more than cloud here. A shadowed pixel keeps plausible-looking
    reflectance and lowers NBR, which reads as severity that never happened.
    """
    reject = np.zeros(qa.shape, dtype=bool)
    for bit in QA_REJECT_BITS:
        reject |= (qa.astype("uint16") & (1 << bit)) > 0
    return ~reject


def _read_asset(
    url: str,
    bounds: tuple[float, float, float, float],
    crs: str,
    transform: Affine,
    shape: tuple[int, int],
    *,
    resampling: Resampling,
) -> np.ndarray:
    """Read one asset into a target window, reprojecting if the scene's CRS differs."""
    with (
        rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES"),
        rasterio.open(url) as src,
    ):
        destination = np.zeros(shape, dtype="float32")
        reproject(
            source=rasterio.band(src, 1),
            destination=destination,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=transform,
            dst_crs=crs,
            resampling=resampling,
            dst_nodata=0,
        )
    return destination


def _composite_nbr(
    items: list[Item],
    bounds: tuple[float, float, float, float],
    crs: str,
    transform: Affine,
    shape: tuple[int, int],
) -> tuple[np.ndarray, int]:
    """Per-pixel median NBR across a season's clear observations.

    Median rather than mean: one undetected cloud edge in a short season would drag a mean
    somewhere the ground never went, and the QA mask does not catch everything.
    """

    def nbr_for(item: Item) -> np.ndarray | None:
        assets = item.assets
        if NIR_ASSET not in assets or SWIR2_ASSET not in assets:
            return None
        try:
            nir = _read_asset(
                assets[NIR_ASSET].href,
                bounds,
                crs,
                transform,
                shape,
                resampling=Resampling.bilinear,
            )
            swir = _read_asset(
                assets[SWIR2_ASSET].href,
                bounds,
                crs,
                transform,
                shape,
                resampling=Resampling.bilinear,
            )
            qa = _read_asset(
                assets[QA_ASSET].href, bounds, crs, transform, shape, resampling=Resampling.nearest
            )
        except Exception as error:  # a single unreadable scene must not lose the fire
            log.warning("skipping %s: %s", item.id, error)
            return None

        nir = nir * REFLECTANCE_SCALE + REFLECTANCE_OFFSET
        swir = swir * REFLECTANCE_SCALE + REFLECTANCE_OFFSET

        clear = _clear_mask(qa) & (nir > 0) & (swir > 0)
        with np.errstate(invalid="ignore", divide="ignore"):
            nbr = np.where(clear, (nir - swir) / (nir + swir), np.nan)

        return nbr.astype("float32") if np.isfinite(nbr).any() else None

    with ThreadPoolExecutor(max_workers=SCENE_WORKERS) as pool:
        stack = [surface for surface in pool.map(nbr_for, items) if surface is not None]

    if not stack:
        return np.full(shape, np.nan, dtype="float32"), 0

    with np.errstate(invalid="ignore"):
        composite = np.nanmedian(np.stack(stack, axis=0), axis=0)
    return composite.astype("float32"), len(stack)


def severity_for_bounds(
    bounds_wgs84: tuple[float, float, float, float],
    fire_year: int,
    *,
    crs: str,
    resolution_m: float,
    max_cloud: int = 60,
) -> tuple[SeverityWindow, list[SourceRecord]]:
    """dNBR and RBR over one bounding box, from the seasons either side of the fire.

    `max_cloud` is deliberately loose. Scene-level cloud cover says little about whether the
    few hundred square kilometres we care about were clear, and the per-pixel QA mask plus a
    median composite handle the rest. Throwing away a 55%-cloudy scene can leave a fire with
    no pre-season observations at all.
    """
    west, south, east, north = bounds_wgs84

    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    xs, ys = transformer.transform([west, east, west, east], [south, south, north, north])
    left, right = min(xs), max(xs)
    bottom, top = min(ys), max(ys)

    left = np.floor(left / resolution_m) * resolution_m
    bottom = np.floor(bottom / resolution_m) * resolution_m
    right = np.ceil(right / resolution_m) * resolution_m
    top = np.ceil(top / resolution_m) * resolution_m

    width = max(int((right - left) / resolution_m), 1)
    height = max(int((top - bottom) / resolution_m), 1)
    transform = Affine(resolution_m, 0.0, left, 0.0, -resolution_m, top)

    client = Client.open(STAC_URL)
    sources: list[SourceRecord] = []
    composites: dict[str, tuple[np.ndarray, int]] = {}

    for label, year in (("pre", fire_year - 1), ("post", fire_year + 1)):
        start, end = _season(year)
        search = client.search(
            collections=[COLLECTION],
            bbox=[west, south, east, north],
            datetime=f"{start.isoformat()}/{end.isoformat()}",
            query={"eo:cloud_cover": {"lt": max_cloud}},
        )
        items = list(search.items())
        log.info("%s season %d: %d scenes", label, year, len(items))

        composites[label] = _composite_nbr(
            items, (left, bottom, right, top), crs, transform, (height, width)
        )

        for item in items:
            sources.append(
                SourceRecord(
                    dataset="Landsat Collection 2 Level 2",
                    version=item.id,
                    access_route="earth-search-stac",
                    uri=item.assets[NIR_ASSET].href if NIR_ASSET in item.assets else STAC_URL,
                    citation=(
                        "U.S. Geological Survey. Landsat Collection 2 Level-2 Science "
                        "Products. doi:10.5066/P9OGBGM6"
                    ),
                    native_resolution_m=30.0,
                    native_timestep="16-day revisit",
                    licence="public domain (USGS)",
                )
            )

    nbr_pre, n_pre = composites["pre"]
    nbr_post, n_post = composites["post"]

    with np.errstate(invalid="ignore"):
        dnbr = (nbr_pre - nbr_post) * 1000.0
        # Parks' relativisation. The 1.001 offset keeps the denominator away from zero where
        # pre-fire NBR is near it, which is exactly the sparse dry ground dNBR handles worst.
        rbr = dnbr / (nbr_pre + 1.001)

    return (
        SeverityWindow(
            dnbr=dnbr.astype("float32"),
            rbr=rbr.astype("float32"),
            nbr_pre=nbr_pre,
            nbr_post=nbr_post,
            transform=transform,
            crs=crs,
            observations_pre=n_pre,
            observations_post=n_post,
        ),
        sources,
    )


def severity_class(dnbr: np.ndarray) -> np.ndarray:
    """Key and Benson classes, 0 unburned to 4 high, NaN where severity is unknown."""
    classes = np.full(dnbr.shape, np.nan, dtype="float32")
    finite = np.isfinite(dnbr)

    classes[finite & (dnbr < DNBR_UNBURNED)] = 0.0
    classes[finite & (dnbr >= DNBR_UNBURNED) & (dnbr < DNBR_LOW)] = 1.0
    classes[finite & (dnbr >= DNBR_LOW) & (dnbr < DNBR_MODERATE_LOW)] = 2.0
    classes[finite & (dnbr >= DNBR_MODERATE_LOW) & (dnbr < DNBR_MODERATE_HIGH)] = 3.0
    classes[finite & (dnbr >= DNBR_MODERATE_HIGH)] = 4.0
    return classes


def is_high_severity(dnbr: np.ndarray) -> np.ndarray:
    """The binary the model is trained on. NaN in, False out — never a silent zero."""
    return np.isfinite(dnbr) & (dnbr >= HIGH_SEVERITY_DNBR)
