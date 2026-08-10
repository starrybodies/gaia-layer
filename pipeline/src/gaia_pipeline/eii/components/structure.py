"""Component A: vegetation structure, measured against the stand's own context.

The question is not how tall a stand is or how closed its canopy is. It is how far the stand
departs from what is normal for its own kind of place. Twenty metres of canopy at sixty per
cent closure is an ordinary stand in a wet Interior Cedar-Hemlock subzone and a heavy one in
Interior Douglas-fir Very Dry Hot Okanagan, and the two say opposite things about how much
live fuel is standing where live fuel does not usually stand.

So the reference distribution every z-score here is taken against is **the BEC subzone-variant
crossed with the land-cover class** — IDFxh1 forest, IDFxh1 shrubland, MSdm1 forest, each its
own population — and never the study area as a whole. Scoring against the study area would
turn this component into a restatement of the valley's moisture gradient: the dry south would
read as uniformly sparse and the Monashee foothills as uniformly dense, which is a map of
where the subzones are and tells a reader nothing the BEC map did not already.

Stratifying finely enough to mean something also leaves some strata too small to be a
reference at all. A stratum with fewer than `MINIMUM_REFERENCE_CELLS` members is not quietly
scored against six neighbours; it falls back to the study-area distribution, says so in its
flags, and carries a wider uncertainty, because a z of 2.1 against six cells and a z of 2.1
against six hundred are not the same claim.

The sign is not asserted. Parks et al. (2018) found live fuel the dominant variable group
behind high-severity fire across the western United States, which argues that a stand
carrying more structure than its context usually carries burns harder. Whitman et al. (2018)
found open, high-basal-area stands in the northwestern boreal burning *less* severely, which
argues the other way for some of the same variables. Both are real results about real
forests and neither settles what the Okanagan does. `a_score` therefore combines the signed
z-scores in the direction given by `SIGN`, and that direction is a named constant rather than
a minus sign buried in an expression: it is a hypothesis the validation tests, and if the
validation contradicts it, one edit inverts the component and the methodology resource
records why. The per-variable z-scores are persisted beside the combination, so a model that
disagrees with our combination can ignore it and use the parts.
"""

from __future__ import annotations

import logging

import numpy as np
import pyarrow as pa

from ..archive import MethodRecord
from ..spine import Spine

log = logging.getLogger(__name__)

#: The structure variables, in the order the output table reports them. Canopy height comes
#: from the GLAD mosaic; crown closure and stand age from VRI's rank 1 polygons.
VARIABLES: tuple[str, ...] = ("canopy_height", "crown_closure", "stand_age")

#: The hypothesis, stated as data so that inverting it is one edit rather than an audit.
#:
#: Positive means: a stand carrying more of this than its biogeoclimatic context normally
#: carries is expected to burn more severely. That is Parks et al.'s live-fuel finding read
#: onto a dry interior forest, and it is the direction the validation is pointed at. It is
#: not a finding of this build. Whitman et al. report the opposite for open high-basal-area
#: boreal stands, so a negative coefficient falling out of validation would be a result, not
#: a bug, and the fix would be to flip the entries here and say so in the methodology.
SIGN: dict[str, float] = {
    "canopy_height": 1.0,
    "crown_closure": 1.0,
    "stand_age": 1.0,
}

#: Below this many members a stratum is a handful of neighbours rather than a distribution.
#: Thirty is the conventional floor for treating a sample mean and spread as usable, and the
#: consequence of being under it here is a documented fallback, not a silently confident z.
MINIMUM_REFERENCE_CELLS = 30

#: A cell whose BEC unit or cover class is unknown belongs to no stratum.
NO_STRATUM = -1

#: The stratum was too small, so the study-area distribution was used instead.
SPARSE_REFERENCE = 0b001
#: The reference had no spread at all, so the deviation is zero by construction.
DEGENERATE_REFERENCE = 0b010
#: The cell has no BEC unit or no cover class, so it could not be placed in a stratum.
UNSTRATIFIED = 0b100

_FLAG_NAMES: tuple[tuple[int, str], ...] = (
    (SPARSE_REFERENCE, "sparse_reference"),
    (DEGENERATE_REFERENCE, "degenerate_reference"),
    (UNSTRATIFIED, "unstratified"),
)

#: How much wider a z is when the reference behind it is not the cell's own context. There is
#: no estimate to derive this from — the error is a mis-specified reference, not a sampling
#: error — so it is a stated doubling rather than a computed quantity, and it is applied to
#: every flagged z so that no flagged value can be read as confidently as an unflagged one.
WEAK_REFERENCE_PENALTY = 2.0

STRUCTURE_METHOD = MethodRecord(
    method_id="eii_component_a_structure_v1",
    name="Vegetation structure deviation within BEC subzone-variant and cover class",
    citation=(
        "Parks, S.A. et al. (2018). High-severity fire: evaluating its key drivers and "
        "mapping its probability across western US forests. Environmental Research Letters "
        "13:044037, doi:10.1088/1748-9326/aab791. Whitman, E. et al. (2018). Variability and "
        "drivers of burn severity in the northwestern Canadian boreal forest. Ecosphere "
        "9(2):e02128, doi:10.1002/ecs2.2128. Reference units follow the British Columbia "
        "biogeoclimatic ecosystem classification (BEC Map subzone-variant polygons)."
    ),
    version="1.0",
    formula=(
        "z = (x - mean(stratum)) / sd(stratum), stratum = BEC subzone-variant x cover class, "
        "population sd; a_score = mean over available variables of SIGN[v] * z_v"
    ),
    notes=(
        "The combination direction in SIGN is a hypothesis under test, not a result. Parks "
        "et al. make live fuel the dominant variable group; Whitman et al. found open "
        "high-basal-area stands burning less severely. Signed per-variable z-scores are "
        "stored alongside the combined score so the combination can be discarded without "
        "losing the measurements. Strata with fewer than 30 members are scored against the "
        "study-area distribution and flagged sparse_reference; strata with no spread score "
        "zero and are flagged degenerate_reference; a cell with no measurement scores "
        "nothing rather than zero."
    ),
)


def flag_labels(mask: int) -> tuple[str, ...]:
    """The names of the flags set in a mask, in the order they are defined."""
    return tuple(name for bit, name in _FLAG_NAMES if mask & bit)


def reference_strata(bec_codes: np.ndarray, cover_codes: np.ndarray) -> np.ndarray:
    """Per-cell stratum id from the BEC unit crossed with the cover class.

    Both inputs arrive as the float class codes `Spine.majority` produces, NaN where the cell
    had no evidence of either. A cell missing either half has no context to be compared
    within and gets `NO_STRATUM`; it is scored against the study area and flagged, rather than
    dropped, because a cell off the edge of the BEC mapping is still a cell an underwriter can
    ask about.

    Ids are dense integers assigned from the pairs present in this call, in the same spirit as
    `bcgw.rasterise`: they identify a stratum within one run and carry no meaning across runs.
    """
    bec = np.asarray(bec_codes, dtype="float64")
    cover = np.asarray(cover_codes, dtype="float64")
    if bec.shape != cover.shape:
        raise ValueError(f"BEC codes are {bec.shape} against cover codes {cover.shape}")

    strata = np.full(bec.shape, NO_STRATUM, dtype="int64")
    known = np.isfinite(bec) & np.isfinite(cover)
    if not known.any():
        return strata

    _, bec_rank = np.unique(bec[known], return_inverse=True)
    cover_levels, cover_rank = np.unique(cover[known], return_inverse=True)
    _, crossed = np.unique(bec_rank * cover_levels.size + cover_rank, return_inverse=True)
    strata[known] = crossed
    return strata


def zscore_within(
    values: np.ndarray,
    strata: np.ndarray,
    *,
    minimum: int = MINIMUM_REFERENCE_CELLS,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Z-scores against each cell's own stratum. Returns (z, stratum_n, flags).

    Three refusals are the whole point of this function.

    A stratum with fewer than `minimum` members is not a reference. Those cells are scored
    against the study-area distribution and flagged `SPARSE_REFERENCE`, so that a confident
    looking z is never produced out of six neighbours without saying so.

    A stratum with no spread yields zero, flagged `DEGENERATE_REFERENCE`. Dividing by it would
    give an infinity for anything off the constant and a nan for the constant itself, and both
    would travel downstream as if they were measurements.

    A missing value yields a missing z. A cell with no canopy measurement has no deviation to
    report, and a zero there would read as a stand that is exactly average, which is the
    strongest possible claim rather than the absence of one.

    `stratum_n` is the number of cells that share the cell's own stratum and carry a value,
    whichever reference was actually used. When `SPARSE_REFERENCE` is set it is therefore the
    count that was too small — the number that explains the flag.

    Spread is the population standard deviation. The stratum is the population of interest
    here, not a sample drawn from a larger one, and at the sizes that pass the minimum the
    choice moves a z by under two per cent anyway. Variance is taken in a second pass over the
    deviations rather than from a sum of squares, which costs one more `bincount` and avoids
    the cancellation that turns a small spread in large numbers, such as stand age, into a
    negative variance.
    """
    observed = np.asarray(values, dtype="float64")
    labels = np.asarray(strata, dtype="int64")
    if observed.shape != labels.shape:
        raise ValueError(f"{observed.shape} values against {labels.shape} strata")

    present = np.isfinite(observed)
    counted = present & (labels >= 0)
    # One trailing slot always exists, so the gather below is safe even with no strata at all.
    width = max(int(labels.max()) + 1, 1) if labels.size else 1

    counts = np.bincount(labels[counted], minlength=width)
    totals = np.bincount(labels[counted], weights=observed[counted], minlength=width)
    with np.errstate(invalid="ignore"):
        means = np.where(counts > 0, totals / np.maximum(counts, 1), np.nan)

    deviations = np.zeros(observed.shape, dtype="float64")
    deviations[counted] = observed[counted] - means[labels[counted]]
    squares = np.bincount(labels[counted], weights=deviations[counted] ** 2, minlength=width)
    with np.errstate(invalid="ignore"):
        spreads = np.sqrt(np.where(counts > 0, squares / np.maximum(counts, 1), np.nan))

    placed = labels >= 0
    gather = np.where(placed, labels, 0)
    stratum_n = np.where(placed, counts[gather], 0).astype("int64")

    global_mean = float(np.mean(observed[present])) if present.any() else np.nan
    global_spread = float(np.std(observed[present])) if present.any() else np.nan

    own = placed & (stratum_n >= minimum)
    reference_mean = np.where(own, means[gather], global_mean)
    reference_spread = np.where(own, spreads[gather], global_spread)

    flags = np.zeros(observed.shape, dtype="uint8")
    flags[present & ~placed] |= UNSTRATIFIED
    flags[present & placed & ~own] |= SPARSE_REFERENCE

    usable = present & np.isfinite(reference_spread) & (reference_spread > 0.0)
    # A present cell always has a finite reference mean: it is a member of whichever
    # distribution was used, so anything unusable here is unusable for want of spread.
    degenerate = present & ~usable
    flags[degenerate] |= DEGENERATE_REFERENCE

    z = np.full(observed.shape, np.nan, dtype="float64")
    z[usable] = (observed[usable] - reference_mean[usable]) / reference_spread[usable]
    z[degenerate] = 0.0

    return z, stratum_n, flags


def component_a(
    spine: Spine,
    *,
    canopy_height: np.ndarray,
    crown_closure: np.ndarray,
    stand_age: np.ndarray,
    bec_codes: np.ndarray,
    cover_codes: np.ndarray,
) -> pa.Table:
    """Per-cell structure deviation: the three z-scores, their combination and its doubts.

    Columns: `h3`, `z_canopy_height`, `z_crown_closure`, `z_stand_age`, `a_score`,
    `contributing_variables`, `bec_stratum`, `reference_n`, `uncertainty`, `flags`.

    `a_score` is the mean of whichever z-scores the cell has, each multiplied by its entry in
    `SIGN`. A mean rather than a sum, so that a cell with one variable and a cell with three
    are on the same scale; `contributing_variables` is how a reader tells those two apart, and
    nothing downstream should treat a one-variable score as the equal of a three-variable one.

    `uncertainty` is a standard error in z units. Per variable it is the delta-method error of
    a standardised deviate taken against an estimated mean and spread, sqrt((1 + z^2/2) / n),
    widened by `WEAK_REFERENCE_PENALTY` wherever a flag is set. Across variables the errors are
    averaged rather than added in quadrature: height, closure and age are strongly correlated
    in a real stand, and combining them as independent would shrink the interval by up to
    sqrt(3) on the strength of an independence we do not have.
    """
    n_cells = spine.n_cells
    measured = {
        "canopy_height": _checked("canopy_height", canopy_height, n_cells),
        "crown_closure": _checked("crown_closure", crown_closure, n_cells),
        "stand_age": _checked("stand_age", stand_age, n_cells),
    }
    strata = reference_strata(
        _checked("bec_codes", bec_codes, n_cells), _checked("cover_codes", cover_codes, n_cells)
    )

    scores = np.empty((len(VARIABLES), n_cells), dtype="float64")
    references = np.empty((len(VARIABLES), n_cells), dtype="int64")
    spreads = np.empty((len(VARIABLES), n_cells), dtype="float64")
    flags = np.zeros(n_cells, dtype="uint8")

    for row, name in enumerate(VARIABLES):
        z, stratum_n, marks = zscore_within(measured[name], strata)
        scores[row] = z
        references[row] = stratum_n
        spreads[row] = _standard_error(z, stratum_n, marks)
        flags |= marks

    available = np.isfinite(scores)
    contributing = available.sum(axis=0)
    divisor = np.maximum(contributing, 1)
    signs = np.array([SIGN[name] for name in VARIABLES], dtype="float64").reshape(-1, 1)

    scored = contributing > 0
    a_score = np.where(scored, np.nansum(scores * signs, axis=0) / divisor, np.nan)
    uncertainty = np.where(scored, np.nansum(spreads, axis=0) / divisor, np.nan)

    # The weakest reference behind any z that made it into the score, since that is the one
    # that limits what the score can be trusted to mean.
    reference_n = np.where(scored, np.where(available, references, np.iinfo("int64").max).min(0), 0)

    log.info(
        "component A: %d strata over %d cells, %.1f%% scored outside their own context",
        int(strata.max()) + 1 if (strata >= 0).any() else 0,
        n_cells,
        100.0 * float(np.mean((flags & (SPARSE_REFERENCE | UNSTRATIFIED)) > 0)),
    )

    rendered = {mask: "|".join(flag_labels(mask)) for mask in range(8)}
    return pa.table(
        {
            "h3": spine.cells.column("h3"),
            "z_canopy_height": pa.array(scores[0], pa.float32()),
            "z_crown_closure": pa.array(scores[1], pa.float32()),
            "z_stand_age": pa.array(scores[2], pa.float32()),
            "a_score": pa.array(a_score, pa.float32()),
            "contributing_variables": pa.array(contributing.astype("uint8"), pa.uint8()),
            "bec_stratum": pa.array(strata.astype("int32"), pa.int32()),
            "reference_n": pa.array(reference_n.astype("int32"), pa.int32()),
            "uncertainty": pa.array(uncertainty, pa.float32()),
            "flags": pa.array([rendered[int(mask)] for mask in flags], pa.string()),
        }
    )


def _standard_error(z: np.ndarray, stratum_n: np.ndarray, flags: np.ndarray) -> np.ndarray:
    """Error of one z in z units, widened wherever its reference was not the cell's own.

    An unstratified cell has a stratum of nothing, which floors at one member and so comes out
    at the widest error the formula can give. That is the intended reading: we do not know
    what this cell should be compared with.
    """
    members = np.maximum(stratum_n, 1).astype("float64")
    with np.errstate(invalid="ignore"):
        error = np.sqrt((1.0 + np.square(z) / 2.0) / members)
    widened = np.where(flags != 0, error * WEAK_REFERENCE_PENALTY, error)
    return np.asarray(np.where(np.isfinite(z), widened, np.nan), dtype="float64")


def _checked(name: str, values: np.ndarray, n_cells: int) -> np.ndarray:
    array = np.asarray(values, dtype="float64")
    if array.shape != (n_cells,):
        raise ValueError(f"{name} is shaped {array.shape} for a spine of {n_cells} cells")
    return array
