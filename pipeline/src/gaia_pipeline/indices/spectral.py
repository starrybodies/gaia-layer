"""Spectral indices, and the published methods behind them.

Each index ships with its citation. That is not decoration: the build prompt's standard is
that an underwriting agent must be able to cite any number the layer returns, and a
normalised difference of two reflectances means nothing to a reader who cannot look up which
two and why.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..schemas.common import IndicatorId
from ..schemas.provenance import Method

# Sentinel-2 asset names as Earth Search publishes them.
ASSET_RED = "red"  # B04, 10 m
ASSET_NIR = "nir"  # B08, 10 m
ASSET_NIR_NARROW = "nir08"  # B8A, 20 m
ASSET_SWIR1 = "swir16"  # B11, 20 m
ASSET_SWIR2 = "swir22"  # B12, 20 m
ASSET_SCL = "scl"  # scene classification, 20 m

# Sentinel-2 L2A reflectance is scaled by 10000 with a 1000 offset from processing
# baseline 04.00 onward. The offset is read per scene from the item properties rather than
# assumed, because mixing baselines silently shifts every index.
REFLECTANCE_SCALE = 10_000.0


@dataclass(frozen=True)
class SpectralIndex:
    indicator: IndicatorId
    numerator_asset: str
    denominator_asset: str
    method: Method

    @property
    def assets(self) -> tuple[str, str]:
        return (self.numerator_asset, self.denominator_asset)


NDVI = SpectralIndex(
    indicator=IndicatorId.NDVI,
    numerator_asset=ASSET_NIR,
    denominator_asset=ASSET_RED,
    method=Method(
        name="Normalised Difference Vegetation Index (NDVI)",
        citation=(
            "Rouse, J.W., Haas, R.H., Schell, J.A. and Deering, D.W. (1974). Monitoring "
            "vegetation systems in the Great Plains with ERTS. NASA Special Publication "
            "351, Third ERTS-1 Symposium, 309-317."
        ),
        formula="(B08 - B04) / (B08 + B04)",
        notes=(
            "Green vegetation density and vigour. Falls as canopy cures or is removed. "
            "Saturates over dense closed canopy, so it is a weaker discriminator in mature "
            "Douglas-fir than in open or mixed stands."
        ),
    ),
)

NDMI = SpectralIndex(
    indicator=IndicatorId.NDMI,
    numerator_asset=ASSET_NIR_NARROW,
    denominator_asset=ASSET_SWIR1,
    method=Method(
        name="Normalised Difference Moisture Index (NDMI)",
        citation=(
            "Gao, B.-C. (1996). NDWI - A normalized difference water index for remote "
            "sensing of vegetation liquid water from space. Remote Sensing of Environment, "
            "58(3), 257-266. doi:10.1016/S0034-4257(96)00067-3"
        ),
        doi="10.1016/S0034-4257(96)00067-3",
        formula="(B8A - B11) / (B8A + B11)",
        notes=(
            "Canopy liquid water content, and the closest open-data proxy for live fuel "
            "moisture. B8A rather than B08 so both bands are the sensor's native 20 m."
        ),
    ),
)

NBR = SpectralIndex(
    indicator=IndicatorId.NBR,
    numerator_asset=ASSET_NIR_NARROW,
    denominator_asset=ASSET_SWIR2,
    method=Method(
        name="Normalised Burn Ratio (NBR)",
        citation=(
            "Key, C.H. and Benson, N.C. (2006). Landscape Assessment: Sampling and Analysis "
            "Methods. In: FIREMON: Fire Effects Monitoring and Inventory System. USDA Forest "
            "Service, Rocky Mountain Research Station, General Technical Report RMRS-GTR-164-CD."
        ),
        formula="(B8A - B12) / (B8A + B12)",
        notes=(
            "Sensitive to the combination of live vegetation and soil or char exposure. "
            "Used here as a standing structural indicator, not as a post-fire severity "
            "measure — that requires a differenced pre and post pair, which v0.1 does not "
            "compute."
        ),
    ),
)

SPECTRAL_INDICES: tuple[SpectralIndex, ...] = (NDVI, NDMI, NBR)

# Every asset any spectral index needs, plus the classification layer.
REQUIRED_ASSETS: tuple[str, ...] = (
    ASSET_RED,
    ASSET_NIR,
    ASSET_NIR_NARROW,
    ASSET_SWIR1,
    ASSET_SWIR2,
    ASSET_SCL,
)


def normalised_difference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """(a - b) / (a + b), with a zero denominator yielding NaN rather than an exception.

    A zero sum means both bands read zero, which is an absence of signal, not a ratio of
    zero. Returning NaN keeps it out of the composite instead of dragging the median to 0.
    """
    numerator = a - b
    denominator = a + b
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(denominator == 0, np.nan, numerator / denominator)
    return out.astype("float32")


def to_reflectance(raw: np.ndarray, offset: float) -> np.ndarray:
    """Convert L2A digital numbers to surface reflectance.

    From processing baseline 04.00 Sentinel-2 L2A carries a -1000 radiometric offset, so a
    scene's own `boa_offset` has to be applied before scaling. Assuming a single baseline
    across a twelve-month archive would introduce a step change in every index at the date
    the baseline switched, which would look exactly like an ecological signal.
    """
    return np.asarray((raw + offset) / REFLECTANCE_SCALE, dtype="float32")


# Scene classification values kept as valid land observations.
SCL_VEGETATION = 4
SCL_NOT_VEGETATED = 5
SCL_UNCLASSIFIED = 7
SCL_WATER = 6

VALID_SCL_CLASSES: tuple[int, ...] = (SCL_VEGETATION, SCL_NOT_VEGETATED, SCL_UNCLASSIFIED)


def clear_land_mask(scl: np.ndarray) -> np.ndarray:
    """True where the pixel is a clear observation of land.

    Excludes cloud (8, 9), thin cirrus (10), cloud shadow (3), dark and topographic shadow
    (2), saturated (1), no data (0), snow and ice (11), and water (6).
    """
    classes = np.rint(scl)
    mask = np.zeros(scl.shape, dtype=bool)
    for value in VALID_SCL_CLASSES:
        mask |= classes == value
    return mask


def water_mask(scl: np.ndarray) -> np.ndarray:
    """True where the pixel is classified as water."""
    return np.asarray(np.rint(scl) == SCL_WATER, dtype=bool)
