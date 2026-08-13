"""Component E: drought, computed as SPEI rather than downloaded as one.

The specification says a multi-scale SPEI blend from a SPEIbase download. SPEIbase v2.11 is
anonymously reachable and reads fine over byte ranges — `sources/speibase.py` does exactly
that — but its record ends in December 2022. The study years run to 2024 and the case study
is an August 2023 fire, so the published product covers neither of the two years the demo
turns on. Carrying 2022's drought forward into a 2023 fire season would be fabricating the
one variable the demonstration is about.

So the index is computed here, from the same precipitation and reference evapotranspiration
series Component B already needs, using Vicente-Serrano's published method: aggregate the
climatic water balance over k months, fit a three-parameter log-logistic distribution by
probability weighted moments, and take the standard normal quantile of the fitted
probability. This is the same decision that was taken for the fire weather codes and for the
same reason — the equations are published and deterministic, and no maintained Python
implementation exists — and it comes with the same obligation: it is checked against the
published product over the years they share, and the difference is quantified rather than
asserted to be small. See D-015 and, for the precedent, D-010.

**Two things that make this a weaker product than SPEIbase, stated rather than buried.** The
reference distribution is fitted on forty years rather than SPEIbase's hundred and twenty,
because that is what the archive fetch can reach in reasonable time; a shorter reference
makes the tails less stable, which is exactly where a drought index is read. And the water
balance uses reference evapotranspiration where SPEIbase v2.11 uses Penman-Monteith
potential evapotranspiration — closely related, not identical.

**The sign.** SPEI runs negative for drought. The index is oriented so positive is the
direction associated with more severe fire, so `SIGN` is negative and the component reports
the negated blend. A cell reading +2 here is in severe drought.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pyarrow as pa
from scipy.special import gamma as gamma_function
from scipy.stats import norm

from ..archive import MethodRecord
from ..spine import Spine

log = logging.getLogger(__name__)

#: The accumulation windows blended, in months. One month is the season in front of you,
#: three is the summer, twelve is whether the year that fed the fuels was a dry one. The
#: drought literature reports these three more often than any other set.
TIMESCALES: tuple[int, ...] = (1, 3, 12)

#: Equal weights across the timescales. There is no evidence in this build for preferring
#: one, and inventing a weighting to look sophisticated would be inventing a finding.
WEIGHTS: dict[int, float] = {1: 1 / 3, 3: 1 / 3, 12: 1 / 3}

#: Negative: SPEI runs negative for drought, and the index runs positive for hazard.
SIGN = -1.0

STRUCTURE_OF_THE_SIGN = (
    "Positive means drier than this node's own distribution for this calendar month — the "
    "negated SPEI — which is the direction associated with more severe fire."
)

#: Below this many reference seasons a three-parameter distribution cannot be fitted. Thirty
#: is already thin for the tails of a fitted distribution; fewer is not a fit, it is a shape
#: drawn through a handful of points.
MINIMUM_FIT_SEASONS = 30

DROUGHT_METHOD = MethodRecord(
    method_id="eii_component_e_spei_v1",
    name="Standardised precipitation evapotranspiration index, multi-scale blend",
    citation=(
        "Vicente-Serrano, S.M., Beguería, S. and López-Moreno, J.I. (2010). A multiscalar "
        "drought index sensitive to global warming: the Standardized Precipitation "
        "Evapotranspiration Index. Journal of Climate 23:1696-1718, "
        "doi:10.1175/2009JCLI2909.1. Beguería, S. et al. (2014). SPEI revisited. "
        "International Journal of Climatology 34:3001-3023, doi:10.1002/joc.3887."
    ),
    version="1.0",
    formula=(
        "D = P - ET0 aggregated over k months; three-parameter log-logistic fitted to the "
        "same calendar month across the reference years by probability weighted moments; "
        "SPEI = Phi^-1(F(D)). e_score = -mean(SPEI_1, SPEI_3, SPEI_12)."
    ),
    notes=(
        "Computed rather than read from SPEIbase, whose record ends in December 2022 while "
        "the study runs to 2024 and the case study is an August 2023 fire (D-015). Checked "
        "against SPEIbase over the 2015-2022 overlap; the agreement is reported in the "
        "divergence record rather than assumed. Two known weaknesses against the published "
        "product: the reference is forty years rather than one hundred and twenty, which "
        "makes the tails less stable, and the balance uses FAO-56 reference "
        "evapotranspiration where SPEIbase uses Penman-Monteith potential "
        "evapotranspiration. A node with fewer than thirty reference seasons is not fitted "
        "and comes back missing. The index is monthly and therefore lags: the value reported "
        "for an as-of date is the last calendar month complete at that date, so an as-of of "
        "14 August 2023 carries July 2023's drought. A partial month is refused rather than "
        "summed short, because a half-summed August reads as a dry August."
    ),
)


def monthly_balance(table: pa.Table, *, n_points: int) -> tuple[list[date], np.ndarray]:
    """Daily P and ET0 summed into calendar months. Rows are nodes, columns months.

    A month missing any of its days comes back NaN rather than being summed short, because a
    partial month reads as a dry one and drought is exactly the thing being measured.
    """
    days = np.asarray(table.column("date")).astype("datetime64[D]")
    point = np.asarray(table.column("point"), dtype="int64")
    balance = np.asarray(table.column("precipitation_mm"), dtype="float64") - np.asarray(
        table.column("et0_mm"), dtype="float64"
    )

    stamps = days.astype("datetime64[M]")
    months, month_of = np.unique(stamps, return_inverse=True)
    n_months = len(months)

    present = np.isfinite(balance)
    flat = point * n_months + month_of
    totals = np.bincount(flat[present], weights=balance[present], minlength=n_points * n_months)
    counted = np.bincount(flat[present], minlength=n_points * n_months)

    # How many days each month should have, so a short month can be told from a gap.
    length = (
        (months + np.timedelta64(1, "M")).astype("datetime64[D]") - months.astype("datetime64[D]")
    ).astype(int)
    expected = np.tile(length, (n_points, 1))

    summed = totals.reshape(n_points, n_months)
    complete = counted.reshape(n_points, n_months) == expected
    return [value.astype(object) for value in months], np.where(complete, summed, np.nan)


def accumulate(balance: np.ndarray, timescale: int) -> np.ndarray:
    """Rolling `timescale`-month sums. The first `timescale - 1` columns come back NaN.

    A window with any month missing is missing, for the same reason a short month is: the
    sum of what was reported is not the balance of the period.
    """
    if timescale < 1:
        raise ValueError("a timescale is a whole number of months")
    n_points, n_months = balance.shape
    out = np.full((n_points, n_months), np.nan)
    for end in range(timescale - 1, n_months):
        window = balance[:, end - timescale + 1 : end + 1]
        complete = np.isfinite(window).all(axis=1)
        out[complete, end] = window[complete].sum(axis=1)
    return out


def log_logistic_parameters(sample: np.ndarray) -> tuple[float, float, float]:
    """Scale, shape and origin by probability weighted moments. NaN where unfittable.

    The unbiased plotting position (i - 0.35) / n is the one Vicente-Serrano's paper
    specifies, and it matters: the usual (i - 0.5) / n shifts the fitted tails enough to
    move an extreme SPEI by a tenth.

    The shape parameter has to exceed one for the moments of the fitted distribution to
    exist at all — Gamma(1 - 1/beta) diverges otherwise — so a sample that produces beta at
    or below one is refused rather than transformed through a distribution with no mean.
    """
    values = np.sort(np.asarray(sample, dtype="float64"))
    values = values[np.isfinite(values)]
    n = values.size
    if n < MINIMUM_FIT_SEASONS:
        return (np.nan, np.nan, np.nan)

    frequency = (np.arange(1, n + 1) - 0.35) / n
    w0 = float(np.mean(values))
    w1 = float(np.mean((1.0 - frequency) * values))
    w2 = float(np.mean((1.0 - frequency) ** 2 * values))

    denominator = 6.0 * w1 - w0 - 6.0 * w2
    if denominator == 0.0:
        return (np.nan, np.nan, np.nan)

    beta = (2.0 * w1 - w0) / denominator
    if not np.isfinite(beta) or beta <= 1.0:
        return (np.nan, np.nan, np.nan)

    reciprocal = float(gamma_function(1.0 + 1.0 / beta) * gamma_function(1.0 - 1.0 / beta))
    if not np.isfinite(reciprocal) or reciprocal == 0.0:
        return (np.nan, np.nan, np.nan)

    alpha = (w0 - 2.0 * w1) * beta / reciprocal
    if not np.isfinite(alpha) or alpha <= 0.0:
        return (np.nan, np.nan, np.nan)

    origin = w0 - alpha * reciprocal
    return (alpha, beta, origin)


def spei_from_reference(current: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Place each node's current accumulation in its own fitted distribution.

    `reference` is one row per node and one column per reference season, always the same
    calendar month: SPEI is defined per month of the year, and pooling months would compare
    an August balance against a distribution that includes every April.

    A value at or below the fitted origin sits outside the log-logistic's support, where the
    probability is zero and the quantile is negative infinity. Rather than emit that, the
    probability is clamped to the most extreme the sample size can resolve — one half of one
    observation — so the answer reads "at least this extreme" rather than "infinitely so".
    """
    current = np.asarray(current, dtype="float64")
    reference = np.asarray(reference, dtype="float64")
    out = np.full(current.shape, np.nan)

    for node in range(current.size):
        if not np.isfinite(current[node]):
            continue
        sample = reference[node][np.isfinite(reference[node])]
        alpha, beta, origin = log_logistic_parameters(sample)
        if not np.isfinite(alpha):
            continue

        edge = 1.0 / (2.0 * sample.size)
        above = current[node] - origin
        probability = edge if above <= 0.0 else 1.0 / (1.0 + (alpha / above) ** beta)
        out[node] = float(norm.ppf(np.clip(probability, edge, 1.0 - edge)))

    return out


def latest_complete_month(as_of: date) -> date:
    """The most recent month wholly inside a record that ends on `as_of`.

    SPEI is a monthly index. Asked for it on 14 August, the honest answer is July's, because
    August is fourteen days old and `monthly_balance` refuses a short month for good reason:
    a partial month sums less rain and less demand than a whole one, and a half-summed August
    reads as a dry August. The refusal was working. What was wrong was asking it for the
    month containing the as-of date, which for any as-of that is not a month end guarantees
    the answer is missing — and Component E came back empty for every node with nothing in
    the output saying why.

    So the target is the month before, unless the as-of date is that month's last day. The
    consequence has to travel with the component: on 14 August 2023, the drought term
    describes July 2023. That is a lag, it is inherent to a monthly index rather than a
    limitation of this build, and every published SPEI product has it.
    """
    if as_of.month == 12:
        first_of_next = date(as_of.year + 1, 1, 1)
    else:
        first_of_next = date(as_of.year, as_of.month + 1, 1)
    if (first_of_next - as_of).days == 1:
        return date(as_of.year, as_of.month, 1)
    if as_of.month == 1:
        return date(as_of.year - 1, 12, 1)
    return date(as_of.year, as_of.month - 1, 1)


def spei_at(
    table: pa.Table, *, n_points: int, as_of: date, timescales: tuple[int, ...] = TIMESCALES
) -> dict[int, np.ndarray]:
    """SPEI at every node for the last month complete at `as_of`, at each timescale."""
    months, balance = monthly_balance(table, n_points=n_points)
    target = latest_complete_month(as_of)
    if target not in months:
        log.warning("no SPEI for %s: the balance carries no such month", target)
        return {scale: np.full(n_points, np.nan) for scale in timescales}

    index = months.index(target)
    same_month = [
        position
        for position, month in enumerate(months)
        if month.month == target.month and month.year < target.year
    ]

    results: dict[int, np.ndarray] = {}
    for scale in timescales:
        accumulated = accumulate(balance, scale)
        results[scale] = spei_from_reference(accumulated[:, index], accumulated[:, same_month])
        fitted = int(np.isfinite(results[scale]).sum())
        log.info("SPEI-%d at %s: %d of %d nodes fitted", scale, target, fitted, n_points)
        if fitted < n_points:
            # Which refusal it was. A node with no current accumulation is a data gap; a node
            # whose reference sample the distribution cannot describe is a method limit, and
            # the two want different responses from whoever reads the log.
            missing_now = int((~np.isfinite(accumulated[:, index])).sum())
            log.info(
                "SPEI-%d at %s: %d node(s) had no accumulation to place, %d had a reference "
                "the log-logistic could not be fitted to (see D-019)",
                scale,
                target,
                missing_now,
                n_points - fitted - missing_now,
            )
    return results


def component_e(
    spine: Spine, *, spei_by_scale: dict[int, np.ndarray], flags: np.ndarray | None = None
) -> pa.Table:
    """Per-cell drought departure: the negated multi-scale SPEI blend.

    Each timescale is persisted beside the blend. A one-month SPEI and a twelve-month SPEI
    disagreeing is itself information — a wet month inside a dry year — and a consumer that
    wants to act on that has to be able to see it.
    """
    n_cells = spine.n_cells
    parts = np.vstack(
        [_checked(f"spei_{scale}", spei_by_scale[scale], n_cells) for scale in TIMESCALES]
    )
    mask = np.zeros(n_cells, dtype="int64") if flags is None else _checked("flags", flags, n_cells)

    weights = np.array([WEIGHTS[scale] for scale in TIMESCALES], dtype="float64").reshape(-1, 1)
    available = np.isfinite(parts)
    contributing = available.sum(axis=0)
    weight_present = np.where(available, weights, 0.0).sum(axis=0)
    scored = contributing > 0

    with np.errstate(invalid="ignore"):
        blend = np.where(
            scored,
            np.nansum(parts * weights, axis=0) / np.maximum(weight_present, 1e-12),
            np.nan,
        )
        e_score = SIGN * blend
        # The three timescales are nested windows over one series, so they are strongly
        # correlated and the doubt falls with the count rather than its square root.
        spread = np.where(
            scored,
            np.where(mask > 0, 2.0, 1.0) / np.maximum(contributing, 1).astype("float64"),
            np.nan,
        )

    return pa.table(
        {
            "h3": spine.cells.column("h3"),
            "spei_1": pa.array(parts[0], pa.float32()),
            "spei_3": pa.array(parts[1], pa.float32()),
            "spei_12": pa.array(parts[2], pa.float32()),
            "e_score": pa.array(e_score, pa.float32()),
            "contributing_variables": pa.array(contributing.astype("uint8"), pa.uint8()),
            "uncertainty": pa.array(spread, pa.float32()),
            "flags": pa.array(["" for _ in range(n_cells)], pa.string()),
        }
    )


def _checked(name: str, values: np.ndarray, n_cells: int) -> np.ndarray:
    array = np.asarray(values, dtype="float64")
    if array.shape != (n_cells,):
        raise ValueError(f"{name} has shape {array.shape}, expected ({n_cells},)")
    return array
