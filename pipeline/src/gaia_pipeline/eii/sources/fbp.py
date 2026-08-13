"""Canadian FBP fuel types: the baseline this index has to beat.

The Fire Behaviour Prediction system sorts the country into sixteen benchmark fuel types,
and a good deal of operational fire science stops there — know the fuel type and the
weather and you have a prediction. One of the models in the validation experiment uses
nothing but this grid, which is the point of carrying it: a landscape-condition index that
cannot beat fuel type alone is not telling anyone something they did not already have.

It is worth being plain about what the grid is. Baron et al. (2024, Fire Ecology 20:15,
doi:10.1186/s42408-024-00249-z) took the FBP typology to the field in interior British
Columbia — the same forests this study area covers — and found that for 58% of plots no FBP
fuel type suitably matched the actual fuel structure, with dense dry forests systematically
typed as open. So this is a lossy prior, not ground truth, and that gap is a large part of
why an index measuring condition rather than looking it up in a table exists at all.

The 100 m GeoTIFF is read rather than the 30 m zip. It streams over ranged GETs without
unpacking three gigabytes, and the honest reason the finer product is not worth the trouble
is that it would not add fuel-type detail the source has: the typology is assigned from a
250 m kNN forest-attribute map. The provenance therefore records 100 m as the native
resolution. The analysis grid is 30 m; the measurement behind it is not.
"""

from __future__ import annotations

import logging
import math

import numpy as np
from rasterio.enums import Resampling

from ...raster import read_window
from ..archive import MethodRecord, SourceRecord
from ..spine import Spine

log = logging.getLogger(__name__)

FBP_URL = (
    "https://cwfis.cfs.nrcan.gc.ca/downloads/fuels/current/"
    "FBP_fueltypes_Canada_100m_EPSG3978_20240527.tif"
)

#: The benchmark types and the non-fuel codes, as published.
#:
#: The GeoTIFF carries no colour table and no raster attribute table, so the mapping comes
#: from two Natural Resources Canada documents in the same download tree, which agree:
#: `fuels/development/Canadian_Forest_FBP_Fuel_Types/FBPfueltypes_sample_colour_classification.xlsx`,
#: which lists every code, and the CanFG metadata sheet beside it, which lists the subset
#: that occurs. Nothing here is inferred from the pixel values.
_BENCHMARK_CLASSES: dict[int, str] = {
    1: "C-1 spruce-lichen woodland",
    2: "C-2 boreal spruce",
    3: "C-3 mature jack or lodgepole pine",
    4: "C-4 immature jack or lodgepole pine",
    5: "C-5 red and white pine",
    6: "C-6 conifer plantation",
    7: "C-7 ponderosa pine / Douglas-fir",
    11: "D-1 leafless aspen",
    12: "D-2 green aspen",
    13: "D-1/D-2 aspen",
    21: "S-1 jack or lodgepole pine slash",
    22: "S-2 white spruce / balsam slash",
    23: "S-3 coastal cedar / hemlock / Douglas-fir slash",
    31: "O-1a matted grass",
    32: "O-1b standing grass",
    40: "M-1 boreal mixedwood, leafless",
    50: "M-2 boreal mixedwood, green",
    60: "M-1/M-2 boreal mixedwood",
    70: "M-3 dead balsam fir mixedwood, leafless",
    80: "M-4 dead balsam fir mixedwood, green",
    90: "M-3/M-4 dead balsam fir mixedwood",
    100: "Not available",
    101: "Non-fuel",
    102: "Water",
    103: "Unknown",
    104: "Unclassified",
    105: "Vegetated non-fuel",
    106: "Urban or built-up area",
}

#: The mixedwood types carry a mixture percentage in the code itself: 400 plus percent
#: conifer for M-1, 500 for M-2, 600 for the undifferentiated M-1/M-2, and 700, 800 and 900
#: plus percent dead fir for M-3, M-4 and M-3/M-4. Generated rather than typed out, because
#: a hundred and fourteen hand-written lines is a hundred and fourteen chances to slip a
#: digit and mislabel a fuel type.
_MIXTURE_BLOCKS: tuple[tuple[int, str], ...] = (
    (400, "M-1 boreal mixedwood, leafless ({percent}% conifer)"),
    (500, "M-2 boreal mixedwood, green ({percent}% conifer)"),
    (600, "M-1/M-2 boreal mixedwood ({percent}% conifer)"),
    (700, "M-3 dead balsam fir mixedwood, leafless ({percent}% dead fir)"),
    (800, "M-4 dead balsam fir mixedwood, green ({percent}% dead fir)"),
    (900, "M-3/M-4 dead balsam fir mixedwood ({percent}% dead fir)"),
)

FUEL_CLASSES: dict[int, str] = _BENCHMARK_CLASSES | {
    base + percent: label.format(percent=percent)
    for base, label in _MIXTURE_BLOCKS
    for percent in range(5, 100, 5)
}

_KNOWN_CODES = np.array(sorted(FUEL_CLASSES), dtype="float32")

FBP_METHOD = MethodRecord(
    method_id="fbp_fueltype_canfg_2024",
    name="Canadian FBP fuel type on the analysis grid",
    citation=(
        "Canadian Forest Service (2024). Canadian Forest FBP Fuel Types (CanFG), 100 m "
        "grid, 27 May 2024 release. Canadian Wildland Fire Information System, Natural "
        "Resources Canada. Typology: Hirsch, K.G. (1996). Canadian Forest Fire Behavior "
        "Prediction (FBP) System: user's guide. Natural Resources Canada, Special Report 7."
    ),
    version="CanFG 2024-05-27",
    formula="nearest-neighbour resample to the analysis grid, then modal code per cell",
    doi=None,
    notes=(
        "The grid itself carries no DOI. Its forest attributes descend from Beaudoin, A. "
        "et al. (2014), Mapping attributes of Canada's forests at moderate resolution "
        "through kNN and MODIS imagery, Canadian Journal of Forest Research 44:521-532, "
        "doi:10.1139/cjfr-2013-0401, a 250 m product, with fuel types reassigned inside "
        "burned areas from the National Burn Area Composite and inside land-use change "
        "from the National Deforestation Monitoring System. Resampled and aggregated by "
        "nearest neighbour and majority throughout, never averaged: the values are labels. "
        "Baron et al. (2024) found no suitable FBP type for 58% of interior British "
        "Columbia field plots, so this is a prior on fuel structure, not a measurement "
        "of it."
    ),
)


def fetch(spine: Spine) -> tuple[np.ndarray, SourceRecord]:
    """FBP fuel type codes on the spine's grid, nearest-neighbour. NaN outside coverage."""
    # Nearest neighbour is the whole point. The values are class codes, and the average of
    # C-2 (2) and C-7 (7) is C-4 (4) — a real fuel type, a wrong answer, and one nothing
    # downstream could detect.
    codes = read_window(FBP_URL, spine.grid, resampling=Resampling.nearest, dtype="float32")

    # Anything outside the published legend is not a measurement. `read_window` already
    # hands back NaN for the source's own -9999 and for ground the raster does not cover;
    # this catches a code the legend cannot name, which would be a change in the source.
    values: np.ndarray = np.where(np.isin(codes, _KNOWN_CODES), codes, np.nan).astype("float32")

    valid = np.isfinite(values)
    if not valid.any():
        raise RuntimeError(f"no FBP fuel types cover the grid at {spine.grid.bounds}")

    present, counts = np.unique(values[valid].astype("int64"), return_counts=True)
    dominant = int(present[int(np.argmax(counts))])
    log.info(
        "fbp fuel types: %d classes over %.0f%% of the grid, dominant %s",
        present.size,
        100.0 * float(valid.mean()),
        FUEL_CLASSES[dominant],
    )

    source = SourceRecord(
        dataset="Canadian Forest FBP Fuel Types (CanFG)",
        version="2024-05-27, 100 m",
        access_route="cwfis-datamart",
        uri=FBP_URL,
        citation=(
            "Canadian Forest Service (2024). Canadian Forest FBP Fuel Types (CanFG), "
            "100 m grid, 27 May 2024 release. Canadian Wildland Fire Information System, "
            "Natural Resources Canada."
        ),
        native_resolution_m=100.0,
        native_timestep="single epoch (2024 release)",
        licence="Open Government Licence - Canada",
    )
    return values, source


def cell_fuel_type(spine: Spine) -> tuple[np.ndarray, np.ndarray, SourceRecord]:
    """Per-cell majority fuel type and the share of the cell it covers.

    The share is the honest part. A cell that is 90% C-3 and a cell that is 34% C-3 both
    come back as C-3, and a baseline model that cannot see the difference between them
    should at least have been offered it.
    """
    values, source = fetch(spine)
    winner, share = spine.majority(values)
    return winner, share, source


# ----------------------------------------------------------------- rate of spread


#: Head fire rate-of-spread coefficients from the FBP System's own equation 26,
#: ROS = a * (1 - exp(-b * ISI)) ^ c, tabulated in Forestry Canada Fire Danger Group (1992),
#: Development and Structure of the Canadian Forest Fire Behavior Prediction System,
#: Information Report ST-X-3, Table 6. They are here so that the constraint layer can check
#: a model's ordering against a published one rather than against an ordering somebody in
#: this repository felt was about right.
#:
#: Only the benchmark types have coefficients of their own. The mixedwoods do not: their
#: spread is defined by interpolation between the conifer and deciduous types in proportion
#: to their conifer content, which `head_fire_rate_of_spread` does rather than inventing
#: coefficients for a hundred and fourteen mixture codes.
ROS_COEFFICIENTS: dict[int, tuple[float, float, float]] = {
    1: (90.0, 0.0649, 4.5),  # C-1 spruce-lichen woodland
    2: (110.0, 0.0282, 1.5),  # C-2 boreal spruce
    3: (110.0, 0.0444, 3.0),  # C-3 mature jack or lodgepole pine
    4: (110.0, 0.0293, 1.5),  # C-4 immature jack or lodgepole pine
    5: (30.0, 0.0697, 4.0),  # C-5 red and white pine
    6: (30.0, 0.0800, 3.0),  # C-6 conifer plantation
    7: (45.0, 0.0305, 2.0),  # C-7 ponderosa pine / Douglas-fir
    11: (30.0, 0.0232, 1.6),  # D-1 leafless aspen
    12: (30.0, 0.0232, 1.6),  # D-2 green aspen, which FBP gives no spread of its own
    13: (30.0, 0.0232, 1.6),  # D-1/D-2
    21: (75.0, 0.0297, 1.3),  # S-1 jack or lodgepole pine slash
    22: (40.0, 0.0438, 1.7),  # S-2 white spruce / balsam slash
    23: (55.0, 0.0829, 3.2),  # S-3 coastal cedar / hemlock / Douglas-fir slash
    31: (190.0, 0.0310, 1.4),  # O-1a matted grass
    32: (250.0, 0.0350, 1.7),  # O-1b standing grass
}

#: The Initial Spread Index the ordering is evaluated at. Ten is the value the FBP System's
#: own worked examples use and is a moderate-to-high spread day in the interior. The choice
#: matters because the curves cross: at very low ISI the grass types have not yet pulled
#: away from the conifers.
REFERENCE_ISI = 10.0

#: Conifer content is encoded in the mixedwood codes themselves — 415 is M-1 at 15% conifer.
_MIXTURE_BASES: dict[int, str] = {400: "M-1", 500: "M-2", 600: "M-1/M-2"}
_DEAD_FIR_BASES: dict[int, str] = {700: "M-3", 800: "M-4", 900: "M-3/M-4"}


def head_fire_rate_of_spread(code: int, isi: float = REFERENCE_ISI) -> float:
    """Head fire rate of spread in metres a minute, or NaN for a type that has none.

    Non-fuel, water, urban and unclassified have no rate of spread — not a rate of zero,
    which would place them in the ordering below a slow-burning aspen stand rather than
    outside it — so they come back missing and the constraint layer drops them.

    Mixedwood spread is interpolated between C-2 and D-1 by conifer content, which is what
    the FBP System does for M-1 and M-2. The dead-fir types M-3 and M-4 are treated the same
    way against their dead-fir fraction; that is a simplification of a separate published
    equation and is flagged as such in the method note rather than presented as the system's
    own answer.
    """
    if code in ROS_COEFFICIENTS:
        a, b, c = ROS_COEFFICIENTS[code]
        return float(a * (1.0 - math.exp(-b * isi)) ** c)

    base = (code // 100) * 100
    if base in _MIXTURE_BASES or base in _DEAD_FIR_BASES:
        share = (code - base) / 100.0
        conifer = head_fire_rate_of_spread(2, isi)
        deciduous = head_fire_rate_of_spread(11, isi)
        return float(share * conifer + (1.0 - share) * deciduous)

    return float("nan")


def rate_of_spread_ordering(codes: np.ndarray, isi: float = REFERENCE_ISI) -> np.ndarray:
    """Reference rate of spread for an array of fuel codes. NaN where the type has none."""
    values = np.asarray(codes, dtype="float64")
    out = np.full(values.shape, np.nan)
    for position, value in enumerate(values.ravel()):
        if np.isfinite(value):
            out.ravel()[position] = head_fire_rate_of_spread(int(value), isi)
    return out
