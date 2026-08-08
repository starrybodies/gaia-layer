"""Confidence scoring.

The build prompt asks for something simple to begin with: composite pixel count, cloud
fraction and sensor revisit gap, combined into a score in [0, 1]. That is what this is. It
is deliberately legible — each component is a named number with a stated weight, and the
score is their weighted mean, so anyone can see why a value scored what it did rather than
taking a number on faith.

Flags then reduce the score multiplicatively. A value that tripped a coherence check is
still served, but it is served knowing less is riding on it.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..schemas.envelope import ConfidenceBasis, ConfidenceComponent, ValidationFlag

# Weights. Spatial coverage carries the most because a value describing a quarter of the
# area is the failure that misleads most quietly: it looks like a normal answer.
WEIGHT_COVERAGE = 0.35
WEIGHT_OBSERVATIONS = 0.25
WEIGHT_CLOUD = 0.25
WEIGHT_REVISIT = 0.15

# Six clear observations in a month is about the best Sentinel-2 offers at this latitude
# with two satellites; scoring saturates there rather than rewarding more.
OBSERVATIONS_FOR_FULL_MARKS = 6

# Sentinel-2's nominal revisit at this latitude is about five days with both satellites.
# A gap of a month or more means the composite rests on one moment in time.
IDEAL_REVISIT_DAYS = 5.0
POOR_REVISIT_DAYS = 45.0


def _clamp(value: float) -> float:
    if value != value:  # NaN
        return 0.0
    return max(0.0, min(1.0, value))


def score(
    *,
    observation_count: int,
    cloud_fraction: float | None,
    revisit_gap_days: float | None,
    spatial_coverage: float,
    flags: Sequence[ValidationFlag] = (),
) -> tuple[float, ConfidenceBasis]:
    """Return the confidence score and the basis that explains it."""

    coverage_component = _clamp(spatial_coverage)

    observations_component = _clamp(max(0, observation_count) / OBSERVATIONS_FOR_FULL_MARKS)

    # Absent cloud information is treated as moderately unfavourable rather than ideal.
    # Assuming the best about data you do not have is how optimistic numbers get served.
    cloud_component = 0.5 if cloud_fraction is None else _clamp(1.0 - cloud_fraction)

    if revisit_gap_days is None:
        revisit_component = 0.5
    elif revisit_gap_days <= IDEAL_REVISIT_DAYS:
        revisit_component = 1.0
    elif revisit_gap_days >= POOR_REVISIT_DAYS:
        revisit_component = 0.0
    else:
        span = POOR_REVISIT_DAYS - IDEAL_REVISIT_DAYS
        revisit_component = _clamp(1.0 - (revisit_gap_days - IDEAL_REVISIT_DAYS) / span)

    components = [
        ConfidenceComponent(
            name="spatial_coverage",
            value=coverage_component,
            weight=WEIGHT_COVERAGE,
            description=(
                "Fraction of the area's land pixels with a valid observation in this period."
            ),
        ),
        ConfidenceComponent(
            name="observation_count",
            value=observations_component,
            weight=WEIGHT_OBSERVATIONS,
            description=(f"Clear scenes composited, saturating at {OBSERVATIONS_FOR_FULL_MARKS}."),
        ),
        ConfidenceComponent(
            name="clear_sky",
            value=cloud_component,
            weight=WEIGHT_CLOUD,
            description="One minus the mean cloud fraction across contributing scenes.",
        ),
        ConfidenceComponent(
            name="revisit_regularity",
            value=revisit_component,
            weight=WEIGHT_REVISIT,
            description=(
                f"Longest gap between contributing observations, ideal at "
                f"{IDEAL_REVISIT_DAYS:.0f} days and worthless by {POOR_REVISIT_DAYS:.0f}."
            ),
        ),
    ]

    base = sum(c.value * c.weight for c in components)

    penalty = 1.0
    for flag in flags:
        penalty *= max(0.0, 1.0 - flag.confidence_penalty)

    basis = ConfidenceBasis(
        observation_count=max(0, observation_count),
        cloud_fraction=None if cloud_fraction is None else _clamp(cloud_fraction),
        revisit_gap_days=None if revisit_gap_days is None else max(0.0, revisit_gap_days),
        spatial_coverage=coverage_component,
        components=components,
        aggregation=(
            "weighted_arithmetic_mean, then multiplied by (1 - penalty) for each flag raised"
        ),
    )

    return _clamp(base * penalty), basis
