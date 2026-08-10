"""Burn severity from Sentinel-2, which is the thing v0.2 is trying to explain.

The prompt this build follows says to cut raw-imagery pipelines and use pre-aggregated
products. That holds for every input and it fails for the target: no open Canadian burn
severity product covers the study period, so severity has to be measured. Everything else in
v0.2 is read from someone else's mosaic; this module is the one place we touch scenes.

**Why not Landsat.** The plan said Landsat Collection 2 Level 2 through Earth Search, and
the collection is indeed catalogued there anonymously. Its assets are not. The hrefs point
at `usgs-landsat.s3`, which is Requester Pays: every read returns 403 without AWS
credentials, and with them it bills the reader. Cataloguing and access are different
promises, and only the first one was checked when the plan was written. Sentinel-2 L2A on
Earth Search is genuinely anonymous — it is the route v0.1 already uses for its spectral
indices — so severity is measured from Sentinel-2 instead.

The cost of that swap is the first two years. Sentinel-2B did not launch until March 2017,
so 2016 and earlier have a single satellite on a ten-day repeat, and a pre-fire season for a
2017 fire is thin. Fire years from 2018 are well covered. The archive says which years it
has rather than interpolating across the ones it does not.

Two decisions keep the cost sane.

Severity is only computed inside fire perimeters. Nothing outside a perimeter has a
severity, and the burned area in a bad year here is a few hundred square kilometres against
a study area of twenty-eight thousand. It is also the more honest framing: the model answers
"given this ground burned, did it burn severely", so it should only see ground that burned.

Pre-fire and post-fire composites come from the growing seasons either side of the fire year
rather than from adjacent scenes. Immediate post-fire imagery measures ash and scorch, which
wash off; a year later measures what actually died, which is what severity means and what
the literature calibrates against.

NBR is (NIR - SWIR2) / (NIR + SWIR2), from B8A and B12, both native 20 m.
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
COLLECTION = "sentinel-2-l2a"

#: B8A and B12. The 20 m narrow near-infrared is preferred over the 10 m B08 because it
#: shares a resolution with the SWIR band it is differenced against, so neither is resampled
#: into detail it does not have.
NIR_ASSET = "nir08"
SWIR2_ASSET = "swir22"
SCL_ASSET = "scl"

#: Scene classification values that are not clear ground: nodata, saturated, cloud shadow,
#: cloud medium and high probability, thin cirrus, and snow. Water is excluded too — a lake
#: has an NBR and it is not a severity.
SCL_REJECT = (0, 1, 3, 6, 8, 9, 10, 11)

#: Reflectance quantisation. Since processing baseline 04.00 the products also carry an
#: additive offset of -1000, and NBR is a normalised difference rather than a ratio, so a
#: common offset does not cancel out. Getting this wrong shifts every severity value.
REFLECTANCE_SCALE = 1.0 / 10000.0
BASELINE_0400 = date(2022, 1, 25)
BASELINE_0400_OFFSET = -1000.0

#: Scenes are read concurrently. The work is almost entirely waiting on HTTP range requests
#: and GDAL releases the GIL while it waits, so threads are the right tool.
SCENE_WORKERS = 8

#: Growing-season window. Late enough that snow has gone from the valleys, early enough that
#: it closes before autumn senescence starts moving NBR for reasons that are not fire.
SEASON_START = (6, 1)
SEASON_END = (9, 15)

#: Key and Benson's composite burn index breakpoints for scaled dNBR, the most widely used
#: severity classes in North America. High severity starts at 660.
#:
#: These are landscape-scale conventions rather than a calibration for the Interior
#: Douglas-fir zone, and the validation report says so.
DNBR_UNBURNED = 100.0
DNBR_LOW = 270.0
DNBR_MODERATE_LOW = 440.0
DNBR_MODERATE_HIGH = 660.0
HIGH_SEVERITY_DNBR = DNBR_MODERATE_HIGH

SEVERITY_METHOD = MethodRecord(
    method_id="dnbr_rbr_sentinel2_extended_v1",
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
        "Sentinel-2 L2A B8A and B12 at 20 m, growing-season median composites either side "
        "of the fire year. RBR is reported alongside dNBR because dNBR understates severity "
        "where pre-fire vegetation is sparse, which is much of the dry Okanagan. The "
        "high-severity threshold is Key and Benson's 660, a landscape-scale convention "
        "rather than a local calibration against field plots."
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


def _clear_mask(scl: np.ndarray) -> np.ndarray:
    """True where the scene classification says the pixel is clear ground.

    Cloud shadow matters more than cloud. A shadowed pixel keeps plausible reflectance and
    lowers NBR, which reads as severity that never happened.
    """
    classes = np.rint(scl).astype("int16")
    reject = np.zeros(classes.shape, dtype=bool)
    for value in SCL_REJECT:
        reject |= classes == value
    return ~reject


def _offset_for(item: Item) -> float:
    """The additive reflectance offset this scene needs.

    Read from the item where it is published, and otherwise inferred from the processing
    baseline change date. Inferring is a last resort and is logged, because a missing offset
    is a silent 0.1 shift in reflectance rather than an error.
    """
    for key in ("earthsearch:boa_offset_applied", "s2:processing_baseline"):
        value = item.properties.get(key)
        if key == "earthsearch:boa_offset_applied" and value is True:
            return 0.0
        if key == "s2:processing_baseline" and value is not None:
            try:
                return BASELINE_0400_OFFSET if float(value) >= 4.0 else 0.0
            except (TypeError, ValueError):
                pass

    acquired = item.datetime.date() if item.datetime else None
    if acquired is None:
        return 0.0
    return BASELINE_0400_OFFSET if acquired >= BASELINE_0400 else 0.0


def _read_asset(
    url: str,
    crs: str,
    transform: Affine,
    shape: tuple[int, int],
    *,
    resampling: Resampling,
) -> np.ndarray:
    """Read one asset into a target window, reprojecting from the scene's own UTM zone."""
    with (
        rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            AWS_NO_SIGN_REQUEST="YES",
            GDAL_HTTP_MAX_RETRY="3",
            GDAL_HTTP_RETRY_DELAY="1",
        ),
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
    crs: str,
    transform: Affine,
    shape: tuple[int, int],
) -> tuple[np.ndarray, int]:
    """Per-pixel median NBR across a season's clear observations.

    Median rather than mean: one undetected cloud edge in a short season would drag a mean
    somewhere the ground never went, and the classification band does not catch everything.
    """

    def nbr_for(item: Item) -> np.ndarray | None:
        assets = item.assets
        if NIR_ASSET not in assets or SWIR2_ASSET not in assets or SCL_ASSET not in assets:
            return None
        try:
            nir = _read_asset(
                assets[NIR_ASSET].href, crs, transform, shape, resampling=Resampling.bilinear
            )
            swir = _read_asset(
                assets[SWIR2_ASSET].href, crs, transform, shape, resampling=Resampling.bilinear
            )
            scl = _read_asset(
                assets[SCL_ASSET].href, crs, transform, shape, resampling=Resampling.nearest
            )
        except Exception as error:  # a single unreadable scene must not lose the fire
            log.warning("skipping %s: %s", item.id, error)
            return None

        offset = _offset_for(item)
        nir = (nir + offset) * REFLECTANCE_SCALE
        swir = (swir + offset) * REFLECTANCE_SCALE

        clear = _clear_mask(scl) & (nir > 0) & (swir > 0)
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
    max_scenes: int = 12,
) -> tuple[SeverityWindow, list[SourceRecord]]:
    """dNBR and RBR over one bounding box, from the seasons either side of the fire.

    `max_cloud` is deliberately loose. Scene-level cloud cover says little about whether the
    few hundred square kilometres we care about were clear, and the per-pixel classification
    plus a median composite handle the rest. Throwing away a 55%-cloudy scene can leave a
    fire with no pre-season observations at all.

    `max_scenes` caps the read: the least cloudy dozen scenes make a composite as good as
    thirty and cost a third of the time.
    """
    west, south, east, north = bounds_wgs84

    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    xs, ys = transformer.transform([west, east, west, east], [south, south, north, north])

    left = np.floor(min(xs) / resolution_m) * resolution_m
    bottom = np.floor(min(ys) / resolution_m) * resolution_m
    right = np.ceil(max(xs) / resolution_m) * resolution_m
    top = np.ceil(max(ys) / resolution_m) * resolution_m

    width = max(int((right - left) / resolution_m), 1)
    height = max(int((top - bottom) / resolution_m), 1)
    transform = Affine(resolution_m, 0.0, left, 0.0, -resolution_m, top)

    client = Client.open(STAC_URL)
    sources: list[SourceRecord] = []
    composites: dict[str, tuple[np.ndarray, int]] = {}

    for label, year in (("pre", fire_year - 1), ("post", fire_year + 1)):
        start, end = _season(year)
        found = list(
            client.search(
                collections=[COLLECTION],
                bbox=[west, south, east, north],
                datetime=f"{start.isoformat()}/{end.isoformat()}",
                query={"eo:cloud_cover": {"lt": max_cloud}},
            ).items()
        )
        items = sorted(found, key=lambda item: item.properties.get("eo:cloud_cover", 100.0))[
            :max_scenes
        ]
        log.info("%s season %d: %d scenes, using %d", label, year, len(found), len(items))

        composites[label] = _composite_nbr(items, crs, transform, (height, width))

        for item in items:
            sources.append(
                SourceRecord(
                    dataset="Sentinel-2 L2A",
                    version=item.id,
                    access_route="earth-search-stac",
                    uri=item.assets[NIR_ASSET].href if NIR_ASSET in item.assets else STAC_URL,
                    citation=(
                        "European Space Agency. Copernicus Sentinel-2 Level-2A surface "
                        "reflectance, processed by ESA and hosted on AWS Open Data."
                    ),
                    native_resolution_m=20.0,
                    native_timestep="five-day revisit",
                    licence="Copernicus Sentinel data, free and open",
                )
            )

    nbr_pre, n_pre = composites["pre"]
    nbr_post, n_post = composites["post"]

    with np.errstate(invalid="ignore"):
        dnbr = (nbr_pre - nbr_post) * 1000.0
        # Parks' relativisation. The 1.001 offset keeps the denominator away from zero where
        # pre-fire NBR is near it, which is the sparse dry ground dNBR handles worst.
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
