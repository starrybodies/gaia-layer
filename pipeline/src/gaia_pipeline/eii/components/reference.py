"""The stratified reference, shared by the components that score against one.

Two components ask the same question in different variables: how far is this cell from
normal *for its own kind of place*. Component A asks it of stand structure, Component C of
riparian extent, and both need the same three refusals — a stratum too small to be a
reference, a stratum with no spread, and a cell with no measurement — to behave identically.
Two implementations of that would drift, and the drift would be invisible because each one
would look reasonable on its own.

So the stratification and the z live here, and the components that use them supply only the
variable and the meaning.
"""

from __future__ import annotations

import numpy as np

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


# --------------------------------------------------------------- the temporal reference

#: Below this many reference seasons, a departure is not a departure from anything. Ten is
#: the number of study years available, so this is deliberately reachable rather than
#: aspirational; the flag and the widened uncertainty are what carry the thinness onward.
MINIMUM_REFERENCE_SEASONS = 5

#: The reference distribution had no spread, so a departure from it is undefined.
DEGENERATE_SEASONS = 0b001
#: Fewer than `MINIMUM_REFERENCE_SEASONS` seasons survived behind the departure.
THIN_SEASONS = 0b010
#: The series did not reach the window asked for, so there is no current value to place.
NO_WINDOW = 0b100

_SEASON_FLAG_NAMES: tuple[tuple[int, str], ...] = (
    (DEGENERATE_SEASONS, "degenerate_reference"),
    (THIN_SEASONS, "thin_reference"),
    (NO_WINDOW, "no_window"),
)


def season_flag_labels(mask: int) -> tuple[str, ...]:
    return tuple(name for bit, name in _SEASON_FLAG_NAMES if mask & bit)


def standardise_against_seasons(
    current: np.ndarray,
    reference: np.ndarray,
    *,
    minimum_seasons: int = MINIMUM_REFERENCE_SEASONS,
    high_is_dry: bool = False,
) -> np.ndarray:
    """Place a value in the distribution of the same calendar window in other years.

    `reference` is one row per node and one column per reference season. Seasons that are
    missing are dropped from that node's distribution rather than counted, because a year
    the reanalysis did not report is not a year of no rain.

    Comparing an August window against a year-round distribution would score every August as
    dry and say nothing about which Augusts were dry, so the reference is always the same
    calendar window rather than the whole record.

    `high_is_dry` says which way the underlying variable runs. Water balance and soil
    moisture fall as conditions dry, so their departure is `(mean - value) / sd`; the drought
    codes climb, so theirs is `(value - mean) / sd`. Both come back oriented so that positive
    is the direction associated with more severe fire.
    """
    current = np.asarray(current, dtype="float64")
    reference = np.asarray(reference, dtype="float64")
    counts = np.isfinite(reference).sum(axis=1)

    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.nanmean(np.where(np.isfinite(reference), reference, np.nan), axis=1)
        spread = np.nanstd(np.where(np.isfinite(reference), reference, np.nan), axis=1, ddof=1)
        deviation = (current - mean) if high_is_dry else (mean - current)
        z = deviation / spread

    usable = (
        (counts >= minimum_seasons) & np.isfinite(spread) & (spread > 0.0) & np.isfinite(current)
    )
    return np.asarray(np.where(usable, z, np.nan))


def season_flags(
    current: np.ndarray,
    reference: np.ndarray,
    *,
    minimum_seasons: int = MINIMUM_REFERENCE_SEASONS,
) -> np.ndarray:
    """Why a departure is missing or weak, as a bitmask per node."""
    current = np.asarray(current, dtype="float64")
    reference = np.asarray(reference, dtype="float64")
    counts = np.isfinite(reference).sum(axis=1)
    with np.errstate(invalid="ignore"):
        spread = np.nanstd(np.where(np.isfinite(reference), reference, np.nan), axis=1, ddof=1)

    flags = np.zeros(current.shape, dtype="int64")
    flags |= np.where(~np.isfinite(current), NO_WINDOW, 0)
    flags |= np.where(counts < minimum_seasons, THIN_SEASONS, 0)
    flags |= np.where(np.isfinite(spread) & (spread <= 0.0), DEGENERATE_SEASONS, 0)
    return flags
