"""Component C: how much riparian ground a cell holds, and whether the corridor is intact.

Riparian ground is the part of a dry interior landscape that stays wet when the rest does
not. It is where fire slows, where it sometimes stops, and — when the corridor has been
cleared, grazed or channelised — where it does neither. A layer that recorded only the
presence of a stream would say the same thing about a cottonwood gallery and a ditch through
a hayfield, so extent alone is not the measurement.

So the component is extent weighted by condition. Extent is the share of the cell inside a
30 m band around mapped streams, lakes and wetlands, from the Freshwater Atlas. Condition is
how the vegetation inside that band compares with the vegetation immediately outside it in
the same cell: a corridor standing taller than its own matrix is doing what a riparian
corridor does, and one standing shorter than the surrounding forest has lost something.

**Zero and missing are different.** A cell with no mapped water has a riparian fraction of
zero, which is a finding — the Atlas covered this ground and there is no water on it. A cell
the Atlas does not cover has no fraction at all. Collapsing those two would turn every gap
in provincial mapping into a report of dry ground.

**The sign.** `SIGN` is negative because the raw quantity runs the other way from every
other component: more intact riparian influence is *better* condition, and the index is
oriented so that positive means the fire-severe direction. Inverting the component is one
edit to that constant.

**What the comparison cannot see.** A cell entirely inside the riparian band has no matrix
to compare against, and one entirely outside it has no corridor. Both come back with an
extent and no vigour, which is the honest answer: the condition question needs both halves
of the cell and this cell only has one.
"""

from __future__ import annotations

import logging

import numpy as np
import pyarrow as pa

from ..archive import MethodRecord
from ..spine import Spine
from .reference import flag_labels as _stratum_flag_labels
from .reference import zscore_within

log = logging.getLogger(__name__)

#: The riparian band, each side of a mapped watercourse. British Columbia's Forest and Range
#: Practices Act sets riparian reserve zones of 30 to 50 m for fish-bearing streams and less
#: for small non-fish streams; 30 m is the conservative end of that range and is also one
#: pixel of the analysis grid, so the band is a whole number of pixels wide rather than an
#: aliased approximation of one.
RIPARIAN_BUFFER_M = 30.0

#: How much taller than its matrix a corridor has to stand to weigh a full one. Five metres
#: is roughly the difference between interior Douglas-fir matrix and a mature cottonwood or
#: birch gallery on the same ground. It is a stated scale, not a fitted one, and it is here
#: as a constant so that changing it is one edit rather than an audit.
VIGOUR_SCALE_M = 5.0

#: Negative: the raw quantity is intactness, and the index is oriented so positive is the
#: direction associated with more severe fire.
SIGN = -1.0

STRUCTURE_OF_THE_SIGN = (
    "Positive means less riparian influence than is normal for this stratum — a degraded or "
    "absent corridor — which is the direction associated with more severe fire."
)

#: The Freshwater Atlas does not cover this cell, so nothing can be said about its water.
UNCOVERED = 0b001
#: The cell is wholly inside or wholly outside the riparian band, so the corridor and the
#: matrix cannot be compared within it.
NO_CONTRAST = 0b010
#: There is riparian ground here but no canopy measurement on one side of the band.
NO_VEGETATION = 0b100

_FLAG_NAMES: tuple[tuple[int, str], ...] = (
    (UNCOVERED, "uncovered"),
    (NO_CONTRAST, "no_contrast"),
    (NO_VEGETATION, "no_vegetation"),
)

RIPARIAN_METHOD = MethodRecord(
    method_id="eii_component_c_riparian_v1",
    name="Riparian extent weighted by corridor condition",
    citation=(
        "British Columbia Ministry of Forests. Freshwater Atlas, stream network, lakes and "
        "wetlands. BC Data Catalogue, Open Government Licence - British Columbia. "
        "Potapov, P. et al. (2021). Mapping global forest canopy height through integration "
        "of GEDI and Landsat data. Remote Sensing of Environment 253:112165, "
        "doi:10.1016/j.rse.2020.112165."
    ),
    version="1.0",
    formula=(
        "fraction = share of the cell within 30 m of mapped water; "
        "vigour = mean canopy height inside the band - mean canopy height outside it; "
        "weight = clip(0.5 + vigour / (2 * 5 m), 0, 1); intactness = fraction * weight; "
        "c_score = -1 * z(intactness) within the cell's BEC subzone-variant and cover class."
    ),
    notes=(
        "Extent alone cannot distinguish a cottonwood gallery from a ditch, so it is "
        "weighted by how the vegetation inside the band compares with the matrix around it "
        "in the same cell. A cell wholly inside or wholly outside the band has no contrast "
        "to measure and carries its extent with no vigour rather than a fabricated one. "
        "A cell the Freshwater Atlas does not cover scores nothing; a cell it covers with no "
        "water on it scores an extent of zero, which is a measurement. The z is taken within "
        "the cell's own biogeoclimatic stratum for the same reason Component A takes its "
        "own: scored against the study area, this would restate the valley's drainage "
        "density and call it condition."
    ),
)


def flag_labels(mask: int) -> tuple[str, ...]:
    return tuple(name for bit, name in _FLAG_NAMES if mask & bit)


def condition_weight(corridor_m: np.ndarray, matrix_m: np.ndarray) -> np.ndarray:
    """How much of its extent a cell's corridor earns, on nought to one.

    Half at parity, and half is the right answer there: a corridor no taller than the forest
    around it is not evidence of a degraded corridor, it is an absence of evidence either
    way, and the weight should sit in the middle rather than at an end.
    """
    vigour = np.asarray(corridor_m, dtype="float64") - np.asarray(matrix_m, dtype="float64")
    with np.errstate(invalid="ignore"):
        weight = 0.5 + vigour / (2.0 * VIGOUR_SCALE_M)
    return np.asarray(np.where(np.isfinite(vigour), np.clip(weight, 0.0, 1.0), np.nan))


def component_c(
    spine: Spine,
    *,
    riparian_mask: np.ndarray,
    canopy: np.ndarray,
    strata: np.ndarray,
    covered: np.ndarray,
) -> pa.Table:
    """Per-cell riparian extent, corridor vigour, and their combination as a departure.

    `riparian_mask` is a boolean grid: which pixels are within the riparian band.
    `covered` is per cell: whether the Freshwater Atlas has mapping over it at all.

    The parts are persisted beside the combination. The weighting is a stated judgement and
    a model is entitled to disagree with it, which it can only do if the extent and the
    vigour survive separately.
    """
    n_cells = spine.n_cells
    mask = np.asarray(riparian_mask, dtype=bool)
    if mask.shape != spine.grid.shape:
        raise ValueError(f"riparian mask is {mask.shape}, expected {spine.grid.shape}")
    covered = np.asarray(covered, dtype=bool)
    if covered.shape != (n_cells,):
        raise ValueError(f"coverage is {covered.shape}, expected ({n_cells},)")

    fraction = spine.fraction(mask).astype("float64")
    corridor, _ = spine.mean(np.asarray(canopy, dtype="float32"), mask=mask)
    matrix, _ = spine.mean(np.asarray(canopy, dtype="float32"), mask=~mask)

    corridor = np.asarray(corridor, dtype="float64")
    matrix = np.asarray(matrix, dtype="float64")
    vigour = corridor - matrix
    weight = condition_weight(corridor, matrix)

    flags = np.zeros(n_cells, dtype="int64")
    flags |= np.where(~covered, UNCOVERED, 0)
    # No contrast is about the cell's geometry; no vegetation is about the canopy mosaic.
    contrastless = (fraction <= 0.0) | (fraction >= 1.0)
    flags |= np.where(covered & contrastless, NO_CONTRAST, 0)
    flags |= np.where(covered & ~contrastless & ~np.isfinite(vigour), NO_VEGETATION, 0)

    fraction = np.where(covered, fraction, np.nan)

    # A cell with no water in it has no corridor to weigh, and its intactness is zero by
    # extent alone. A cell with water and no usable vigour is weighed at parity, which is
    # the same neutral the weight function returns and is flagged so it can be excluded.
    usable_weight = np.where(np.isfinite(weight), weight, 0.5)
    intactness = np.where(np.isfinite(fraction), fraction * usable_weight, np.nan)

    z, stratum_n, stratum_flags = zscore_within(intactness, np.asarray(strata, dtype="int64"))
    c_score = SIGN * z

    # The doubt on a departure is the doubt on its reference, plus the doubt added by
    # weighing a corridor that could not be seen.
    spread = np.where(
        np.isfinite(c_score),
        np.where(stratum_flags > 0, 2.0, 1.0) * np.where(np.isfinite(weight), 1.0, 1.5),
        np.nan,
    )

    log.info(
        "component C: %.1f%% of cells carry riparian ground, %.1f%% have a corridor to weigh",
        100.0 * float(np.mean(np.isfinite(fraction) & (fraction > 0.0))),
        100.0 * float(np.mean(np.isfinite(vigour))),
    )

    rendered = {value: "|".join(flag_labels(value)) for value in range(8)}
    stratum_rendered = {value: "|".join(_stratum_flag_labels(value)) for value in range(8)}
    combined = [
        "|".join(
            part for part in (rendered[int(a) & 0b111], stratum_rendered[int(b) & 0b111]) if part
        )
        for a, b in zip(flags, stratum_flags, strict=True)
    ]

    return pa.table(
        {
            "h3": spine.cells.column("h3"),
            "riparian_fraction": pa.array(fraction, pa.float32()),
            "riparian_canopy_m": pa.array(corridor, pa.float32()),
            "matrix_canopy_m": pa.array(matrix, pa.float32()),
            "riparian_vigour_m": pa.array(vigour, pa.float32()),
            "intactness": pa.array(intactness, pa.float32()),
            "c_score": pa.array(c_score, pa.float32()),
            "reference_n": pa.array(np.asarray(stratum_n, dtype="int32"), pa.int32()),
            "uncertainty": pa.array(spread, pa.float32()),
            "flags": pa.array(combined, pa.string()),
        }
    )
