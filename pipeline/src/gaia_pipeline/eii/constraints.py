"""The mechanistic constraint layer: three things fire is not allowed to do.

A fitted model can be right about the data and wrong about the world. It can learn that the
Drought Code falls where severity rises, because in this sample the worst fires happened to
land in a year the codes lagged. It can rank ponderosa pine above standing grass, because
the grass cells in the training set were near roads. Neither is a bug in the fitting; both
are statements a fire scientist would reject on sight, and neither shows up in AUC.

So the model is checked against mechanism, and the checks come in two kinds.

**Statements about the model.** Monotonicity and CFFDRS consistency either hold for a fitted
model or they do not. No per-cell adjustment can repair a model that runs backwards in the
Drought Code, and pretending otherwise by clamping every cell would hide the finding. These
produce outcomes, and an outcome that does not hold is a reason not to serve the model.

**Statements about a cell.** The water-balance rule is the one that changes a number. A cell
sitting in intact riparian ground, predicted in the top decile of severity, with no
overriding weather signal, is being asked to burn in a way wet ground does not burn. That
value is clamped back to the edge of the plausible envelope, marked low confidence, and the
rule that fired is recorded. It is not deleted: the cell stays among the more severe ground,
because the model had a reason and mechanism only bounds it.

This extends v0.1's idiom rather than inventing a second one. There, rejection meant the
number is wrong and flagging meant the number may be right and you should know what is odd
about it. Clamping is the third move: the number is outside what mechanism allows, so it is
pulled to the boundary and marked as having been pulled. Nothing implausible is emitted
silently and nothing is silently removed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from .sources import fbp

log = logging.getLogger(__name__)

HIGH_CONFIDENCE = "high"
LOW_CONFIDENCE = "low"

#: Which way each feature must push predicted severity, all else equal. Positive means more
#: of it cannot lower the prediction. These are the four the specification names, and they
#: are the four the fire behaviour literature is least equivocal about.
MONOTONE: dict[str, int] = {
    "dc": +1,
    "bui": +1,
    "vpd_kpa": +1,
    "soil_shallow": -1,
    "soil_deep": -1,
}

#: How many points the partial dependence is evaluated at. Nine deciles rather than the full
#: range: the ends of a feature's range are where the training data is thinnest and where a
#: boosted tree's extrapolation is a step function rather than a trend.
PARTIAL_DEPENDENCE_POINTS = 9

#: How much a partial-dependence curve may wander against its expected direction before the
#: rule is judged to have failed. A boosted tree is piecewise constant and will always show
#: small reversals between steps; a rule with no tolerance would fail every real model and
#: therefore mean nothing.
MONOTONE_TOLERANCE = 0.05

#: Above this share of the cell inside an intact corridor, the water-balance rule applies.
#: It is the extent already weighted by condition, so 0.5 is a cell half-covered by a
#: corridor in good condition, or wholly covered by one in indifferent condition.
INTACT_THRESHOLD = 0.5

#: The decile the rule protects. A cell can be severe on wet ground; what mechanism refuses
#: is for it to be among the most severe ground in the run.
TOP_DECILE = 0.90

#: Weather extreme enough to override the wet ground. Under a heat dome, riparian corridors
#: do burn — 2021 in this same valley is the proof — so the rule yields rather than insisting.
OVERRIDING_WEATHER = 0.90

#: Below this many distinct fuel types there is no ordering to check.
MINIMUM_FUEL_TYPES = 3


@dataclass(frozen=True)
class RuleOutcome:
    """One rule, whether it held, and what it saw."""

    rule: str
    held: bool
    detail: str
    affected: int = 0


@dataclass(frozen=True)
class Clamped:
    """A per-cell rule's effect: the adjusted values, the confidence, and the finding."""

    value: np.ndarray
    confidence: np.ndarray
    outcome: RuleOutcome


@dataclass(frozen=True)
class ConstraintReport:
    """Everything the layer did, ready to be written to `constraint_event`."""

    value: np.ndarray
    confidence: np.ndarray
    flags: list[str]
    outcomes: list[RuleOutcome] = field(default_factory=list)

    @property
    def holds(self) -> bool:
        return all(outcome.held for outcome in self.outcomes)


def partial_dependence(
    predict: Callable[[np.ndarray], np.ndarray],
    matrix: np.ndarray,
    *,
    column: int,
    points: int = PARTIAL_DEPENDENCE_POINTS,
) -> tuple[np.ndarray, np.ndarray]:
    """Mean prediction as one column is swept across its own deciles, others held as they are.

    The average over the real rows rather than over a synthetic grid, which is what makes it
    a statement about this landscape: sweeping every feature independently would ask the
    model about combinations of weather and fuel that do not occur here.
    """
    values = np.asarray(matrix, dtype="float64")
    observed = values[:, column]
    finite = observed[np.isfinite(observed)]
    if finite.size == 0:
        return np.array([]), np.array([])

    grid = np.quantile(finite, np.linspace(0.1, 0.9, points))
    grid = np.unique(grid)

    response = np.empty(grid.size)
    for position, level in enumerate(grid):
        probe = values.copy()
        probe[:, column] = level
        response[position] = float(np.mean(predict(probe)))
    return grid, response


def check_monotonicity(
    predict: Callable[[np.ndarray], np.ndarray],
    matrix: np.ndarray,
    *,
    columns: list[str],
) -> list[RuleOutcome]:
    """Does the model move the way mechanism says, in each of the monotone features?

    A feature the model ignores produces a flat curve, and a flat curve holds: being
    uninformative is not the same as being backwards, and failing a model for not using a
    variable would be a rule about feature selection wearing a rule about physics.
    """
    outcomes: list[RuleOutcome] = []
    for position, name in enumerate(columns):
        if name not in MONOTONE:
            continue

        grid, response = partial_dependence(predict, matrix, column=position)
        if grid.size < 2:
            outcomes.append(
                RuleOutcome(f"monotonicity:{name}", True, "no spread in this feature to sweep")
            )
            continue

        expected = MONOTONE[name]
        step = np.diff(response) * expected
        against = float(-step[step < 0].sum())
        span = float(np.abs(response).max() - np.abs(response).min()) or 1.0
        held = against <= MONOTONE_TOLERANCE * max(span, np.ptp(response), 1e-9)

        direction = "increasing" if expected > 0 else "decreasing"
        moved = "increases" if response[-1] > response[0] else "decreases"
        detail = (
            f"partial dependence should be {direction} in {name}; it {moved} overall "
            f"from {response[0]:.4f} to {response[-1]:.4f}, with {against:.4f} of movement "
            "against the expected direction"
        )
        outcomes.append(RuleOutcome(f"monotonicity:{name}", held, detail))

    return outcomes


def check_cffdrs(predicted: np.ndarray, fuel_codes: np.ndarray) -> RuleOutcome:
    """Does mean predicted severity rank fuel types the way FBP ranks their spread?

    Per fuel type rather than per cell, because rate of spread is a property of the type and
    the cells inside one type differ by everything else. Non-fuel, water and unclassified
    have no rate of spread at all and are dropped rather than ranked at zero, which would
    place a lake below a leafless aspen stand instead of outside the ordering.

    Spearman rather than Pearson: the claim is about ordering, not about the shape of the
    relationship, and FBP's own spread values are not on the severity scale.
    """
    predicted = np.asarray(predicted, dtype="float64")
    codes = np.asarray(fuel_codes, dtype="float64")
    spread = fbp.rate_of_spread_ordering(codes)

    usable = np.isfinite(predicted) & np.isfinite(spread)
    dropped = int((~np.isfinite(spread) & np.isfinite(codes)).sum())

    types = np.unique(codes[usable])
    if types.size < MINIMUM_FUEL_TYPES:
        return RuleOutcome(
            "cffdrs",
            True,
            f"too few fuel types present to establish an ordering: {types.size}",
            dropped,
        )

    mean_predicted = np.array([predicted[usable & (codes == code)].mean() for code in types])
    reference = np.array([fbp.head_fire_rate_of_spread(int(code)) for code in types])

    correlation = _spearman(mean_predicted, reference)
    held = bool(np.isfinite(correlation) and correlation >= 0.0)
    return RuleOutcome(
        "cffdrs",
        held,
        f"rank correlation between mean predicted severity and FBP head fire rate of spread "
        f"across {types.size} fuel types: {correlation:+.3f}",
        dropped,
    )


def _spearman(left: np.ndarray, right: np.ndarray) -> float:
    if left.size < 2:
        return float("nan")
    ranked_left = np.argsort(np.argsort(left)).astype("float64")
    ranked_right = np.argsort(np.argsort(right)).astype("float64")
    if ranked_left.std() == 0.0 or ranked_right.std() == 0.0:
        return float("nan")
    return float(np.corrcoef(ranked_left, ranked_right)[0, 1])


def apply_water_balance(
    predicted: np.ndarray,
    riparian_intactness: np.ndarray,
    weather: np.ndarray,
    *,
    intact_threshold: float = INTACT_THRESHOLD,
    top_decile: float = TOP_DECILE,
    overriding: float = OVERRIDING_WEATHER,
) -> Clamped:
    """Pull intact riparian ground out of the top decile unless the weather overrides it.

    The three conditions are all necessary. Intact ground can be severe; the top decile can
    contain wet ground; and under extreme weather both happen at once, which is 2021 in this
    valley and is not something to constrain away. It is the conjunction — wet, top decile,
    ordinary weather — that mechanism refuses.

    A cell with no riparian measurement is not clamped. Missing intactness is not evidence
    of an intact corridor, and clamping on it would turn a gap in provincial mapping into a
    reduction in predicted risk.
    """
    values = np.asarray(predicted, dtype="float64").copy()
    intactness = np.asarray(riparian_intactness, dtype="float64")
    signal = np.asarray(weather, dtype="float64")

    scored = np.isfinite(values)
    if not scored.any():
        return Clamped(
            values,
            np.full(values.shape, HIGH_CONFIDENCE, dtype=object),
            RuleOutcome("water_balance", True, "nothing scored"),
        )

    envelope = float(np.quantile(values[scored], top_decile))

    # Strictly above the threshold, not merely at it. Where the weather does not vary
    # across the run there is no cell whose weather overrides any other's, and a
    # non-strict test would read a flat field as though every cell were extreme — which
    # would switch the rule off entirely and silently. A cell with no weather behind it
    # does not override either: absence is not an extreme.
    extreme = (
        float(np.quantile(signal[np.isfinite(signal)], overriding))
        if np.isfinite(signal).any()
        else np.inf
    )
    overridden = np.isfinite(signal) & (signal > extreme)

    violating = (
        scored
        & np.isfinite(intactness)
        & (intactness >= intact_threshold)
        & (values > envelope)
        & ~overridden
    )

    confidence = np.full(values.shape, HIGH_CONFIDENCE, dtype=object)
    if violating.any():
        values[violating] = envelope
        confidence[violating] = LOW_CONFIDENCE

    count = int(violating.sum())
    outcome = RuleOutcome(
        "water_balance",
        count == 0,
        f"{count} cells in intact riparian context were predicted above the "
        f"{top_decile:.0%} envelope of {envelope:.4f} without an overriding weather signal; "
        "each was clamped to the envelope and marked low confidence",
        count,
    )
    return Clamped(values, confidence, outcome)


def apply(
    *,
    predicted: np.ndarray,
    fuel_codes: np.ndarray,
    riparian_intactness: np.ndarray,
    weather: np.ndarray,
    predict: Callable[[np.ndarray], np.ndarray] | None = None,
    matrix: np.ndarray | None = None,
    columns: list[str] | None = None,
) -> ConstraintReport:
    """Run every rule that the inputs allow, and report what each one found.

    The monotonicity check needs the fitted model itself, so it only runs when one is
    supplied. Its absence is not silently equivalent to it passing: the report simply has no
    monotonicity outcome, and a consumer counting outcomes can tell.
    """
    outcomes: list[RuleOutcome] = []

    if predict is not None and matrix is not None and columns is not None:
        outcomes.extend(check_monotonicity(predict, matrix, columns=columns))

    outcomes.append(check_cffdrs(predicted, fuel_codes))

    clamped = apply_water_balance(predicted, riparian_intactness, weather)
    outcomes.append(clamped.outcome)

    changed = ~np.isclose(clamped.value, np.asarray(predicted, dtype="float64"), equal_nan=True)
    flags = ["water_balance_clamp" if flag else "" for flag in changed]

    for outcome in outcomes:
        if not outcome.held:
            log.warning("constraint %s did not hold: %s", outcome.rule, outcome.detail)

    return ConstraintReport(
        value=clamped.value, confidence=clamped.confidence, flags=flags, outcomes=outcomes
    )
