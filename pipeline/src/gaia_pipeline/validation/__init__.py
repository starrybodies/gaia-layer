"""Layer 2 — validation.

Nothing reaches the service layer without passing through :func:`validate_value`. The
function takes a candidate measurement and everything known about how it was produced, and
returns a verdict: served as validated, served with flags attached, or rejected and never
served at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from ..schemas.common import DateRange, IndicatorId, Severity
from ..schemas.envelope import ValidationReport
from . import confidence as confidence_module
from .constraints import CONSTRAINT_IDS, check_bounds, check_coherence, check_temporal

__all__ = ["ValidationContext", "ValidationReport", "validate_value"]


@dataclass(frozen=True)
class ValidationContext:
    """Everything the engine needs to judge one candidate value.

    Assembled by the ingestion pipeline. The engine reads nothing from the outside world —
    it is a pure function of this input, which is what makes it testable by property.
    """

    indicator: IndicatorId
    value: float
    period: DateRange

    #: Prior periods for this indicator and geometry, ascending by date. Used for rate
    #: checks. Entries at or after the period being validated are ignored.
    history: Sequence[tuple[date, float]] = field(default_factory=tuple)

    #: Other indicators measured over the same geometry and period, for coherence checks.
    covariates: Mapping[IndicatorId, float] = field(default_factory=dict)

    observation_count: int = 0
    cloud_fraction: float | None = None
    revisit_gap_days: float | None = None
    spatial_coverage: float = 0.0


def validate_value(context: ValidationContext) -> ValidationReport:
    """Judge one candidate value.

    Bounds run first and short-circuit: if the number is not a number, or lies outside what
    the quantity can physically be, there is nothing meaningful to say about its trend or
    its coherence with anything else.
    """
    flags = check_bounds(context.indicator, context.value)

    fatal = any(f.severity is Severity.ERROR for f in flags)
    if not fatal:
        flags = [
            *flags,
            *check_temporal(
                context.indicator,
                context.value,
                context.history,
                context.period.end,
            ),
            *check_coherence(context.indicator, context.value, context.covariates),
        ]

    if fatal:
        # A rejected value gets no confidence, because confidence in a number that will
        # never be served is a category error.
        _, basis = confidence_module.score(
            observation_count=context.observation_count,
            cloud_fraction=context.cloud_fraction,
            revisit_gap_days=context.revisit_gap_days,
            spatial_coverage=context.spatial_coverage,
        )
        return ValidationReport(
            status="rejected",
            flags=flags,
            constraints_checked=list(CONSTRAINT_IDS),
            confidence=0.0,
            confidence_basis=basis,
        )

    value_confidence, basis = confidence_module.score(
        observation_count=context.observation_count,
        cloud_fraction=context.cloud_fraction,
        revisit_gap_days=context.revisit_gap_days,
        spatial_coverage=context.spatial_coverage,
        flags=flags,
    )

    return ValidationReport(
        status="flagged" if flags else "validated",
        flags=flags,
        constraints_checked=list(CONSTRAINT_IDS),
        confidence=value_confidence,
        confidence_basis=basis,
    )
