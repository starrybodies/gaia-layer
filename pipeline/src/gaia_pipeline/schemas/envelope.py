"""The envelope — the only shape in which this layer emits a number.

Every field here exists to make one thing impossible: returning a quantity without the
context needed to judge it. There is no code path that produces a bare float. If you find
yourself wanting one, that is the bug.

Note what is *absent* from :class:`EnvelopeBase`: a ``rejected`` validation status. The
status field is typed to :class:`~.common.ServedStatus`, which has two members. A rejected
value is represented by :class:`RejectedValue`, which has no ``value`` field at all. The
rule "rejected values are never served as answers" is therefore not a policy anyone has to
remember — it is a type error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator
from ulid import ULID

from .common import (
    Confidence,
    DateRange,
    IndicatorId,
    ServedStatus,
    Severity,
    Strict,
)
from .provenance import Method, ProvenanceChain, StepKind

CLAIM_ID_PATTERN = r"^clm_[0-9A-HJKMNP-TV-Z]{26}$"


def new_claim_id() -> str:
    """Mint an identifier for a claim. Lexicographically sortable by mint time."""
    return f"clm_{ULID()}"


class ValidationFlag(Strict):
    """A constraint the value did not satisfy.

    Flags travel with the value rather than replacing it. A consumer that ignores flags
    gets a number; a consumer that reads them gets a number and the reason to discount it.
    """

    code: str = Field(min_length=1, description="Stable machine-readable flag code.")
    constraint: str = Field(
        min_length=1, description="Identifier of the constraint that produced this flag."
    )
    severity: Severity
    message: str = Field(min_length=1, description="Plain-language explanation.")
    observed: float | None = Field(default=None, description="The value that tripped the check.")
    expected: str | None = Field(
        default=None, description="What the constraint required, in plain language."
    )
    confidence_penalty: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Multiplicative reduction this flag applied to the confidence score.",
    )


class ConfidenceComponent(Strict):
    """One named input to the confidence score, kept separate so the score decomposes."""

    name: str = Field(min_length=1)
    value: Confidence = Field(description="Component score, 1.0 being ideal.")
    weight: float = Field(gt=0.0, le=1.0)
    description: str = Field(min_length=1)


class ConfidenceBasis(Strict):
    """How the confidence score was arrived at.

    v0.1 keeps this deliberately simple, as the build prompt directs: composite pixel
    count, cloud fraction, and sensor revisit gap. The structure admits more components
    later without changing the envelope shape.
    """

    observation_count: int = Field(
        ge=0, description="Number of source observations composited into this value."
    )
    cloud_fraction: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Mean cloud fraction across contributing scenes."
    )
    revisit_gap_days: float | None = Field(
        default=None, ge=0.0, description="Longest gap between contributing observations."
    )
    spatial_coverage: float = Field(
        ge=0.0, le=1.0, description="Fraction of the geometry with a valid observation."
    )
    components: list[ConfidenceComponent] = Field(min_length=1)
    aggregation: str = Field(
        default="weighted_arithmetic_mean",
        description="How components combine into the score.",
    )

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> ConfidenceBasis:
        total = sum(c.weight for c in self.components)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"confidence component weights must sum to 1.0, got {total:.6f}")
        return self


class SpatialStats(Strict):
    """Distribution of the indicator across the geometry.

    The envelope's scalar ``value`` is an aggregate over an area. Serving the aggregate
    without its spread would hide the case where half a parcel is saturated and half is
    tinder-dry, which is exactly the case an underwriter needs to see.
    """

    mean: float
    median: float
    std: float = Field(ge=0.0)
    p10: float
    p90: float
    minimum: float
    maximum: float
    valid_pixels: int = Field(ge=0)
    total_pixels: int = Field(gt=0)

    @model_validator(mode="after")
    def _coherent(self) -> SpatialStats:
        if self.valid_pixels > self.total_pixels:
            raise ValueError("valid_pixels cannot exceed total_pixels")
        if self.minimum > self.maximum:
            raise ValueError("minimum cannot exceed maximum")
        if not (self.minimum <= self.median <= self.maximum):
            raise ValueError("median must lie within [minimum, maximum]")
        return self


class EnvelopeBase(Strict):
    """Fields every served value carries, regardless of what the value is."""

    claim_id: str = Field(
        pattern=CLAIM_ID_PATTERN,
        description="Identifier for this exact claim. Pass to get_provenance to trace it.",
    )
    unit: str = Field(min_length=1, description="Unit of the value. 'index' for unitless ratios.")
    confidence: Confidence
    confidence_basis: ConfidenceBasis
    validation_status: ServedStatus
    flags: list[ValidationFlag] = Field(default_factory=list)
    provenance: ProvenanceChain
    method: Method
    geometry_hash: str = Field(
        min_length=8, description="Stable hash of the geometry this value describes."
    )
    period: DateRange = Field(description="Period the value describes.")
    generated_at: datetime

    @model_validator(mode="after")
    def _status_matches_flags(self) -> EnvelopeBase:
        has_error = any(f.severity is Severity.ERROR for f in self.flags)
        if has_error:
            raise ValueError(
                "an error-severity flag means the value was rejected; construct a "
                "RejectedValue instead of an envelope"
            )
        if self.flags and self.validation_status is ServedStatus.VALIDATED:
            raise ValueError("a value carrying flags cannot be reported as validated")
        if not self.flags and self.validation_status is ServedStatus.FLAGGED:
            raise ValueError("a value reported as flagged must carry at least one flag")
        return self

    @model_validator(mode="after")
    def _chain_is_well_formed(self) -> EnvelopeBase:
        kinds = [s.kind for s in self.provenance]
        if StepKind.OBSERVATION not in kinds:
            raise ValueError("provenance chain must contain at least one observation step")
        if kinds[-1] is not StepKind.VALIDATION:
            raise ValueError("provenance chain must terminate in a validation step")
        if [s.index for s in self.provenance] != list(range(len(self.provenance))):
            raise ValueError("provenance step indices must be contiguous from 0")
        return self


class NumericEnvelope(EnvelopeBase):
    """A single scalar indicator value for a geometry over a period."""

    kind: Literal["numeric"] = "numeric"
    indicator: IndicatorId
    value: float
    spatial_stats: SpatialStats | None = None


class Trend(Strict):
    """Direction and strength of change in an indicator over a period.

    ``significant`` is the field that matters. A slope without a significance test invites
    the reader to see a trend in noise, which is the quantitative failure mode lesson 2
    exists to prevent.
    """

    direction: Literal["increasing", "decreasing", "stable"]
    slope_per_month: float = Field(description="Ordinary least squares slope, units per month.")
    r_squared: float = Field(ge=0.0, le=1.0)
    p_value: float = Field(ge=0.0, le=1.0)
    significant: bool = Field(
        description="True when p < 0.05 and at least 4 observations contributed."
    )
    n_observations: int = Field(ge=0)
    first: float = Field(description="Fitted value at the start of the period.")
    last: float = Field(description="Fitted value at the end of the period.")

    @model_validator(mode="after")
    def _significance_requires_evidence(self) -> Trend:
        if self.significant and self.n_observations < 4:
            raise ValueError("a trend cannot be significant on fewer than 4 observations")
        if self.significant and self.p_value >= 0.05:
            raise ValueError("a trend marked significant must have p < 0.05")
        return self


class TrendEnvelope(EnvelopeBase):
    """A trend in one indicator over a period."""

    kind: Literal["trend"] = "trend"
    indicator: IndicatorId
    value: Trend


class SubstrateComponent(Strict):
    """One indicator's contribution to the wildfire substrate score.

    Every field is present so the score can be reconstructed by hand from its parts. A
    score a land manager cannot decompose is a score they cannot act on.
    """

    indicator: IndicatorId
    raw: NumericEnvelope = Field(description="The underlying measured value, envelope intact.")
    normalized: float = Field(
        ge=0.0,
        le=1.0,
        description="Value rescaled so 1.0 is the most fire-prone substrate condition.",
    )
    normalization: str = Field(
        min_length=1, description="The rescaling applied, stated so it can be reversed."
    )
    weight: float = Field(gt=0.0, le=1.0)
    contribution: float = Field(
        ge=0.0, le=100.0, description="normalized x weight x 100, the points this adds."
    )
    rationale: str = Field(
        min_length=1, description="Why this indicator belongs in a wildfire substrate score."
    )

    @model_validator(mode="after")
    def _contribution_is_consistent(self) -> SubstrateComponent:
        expected = self.normalized * self.weight * 100.0
        if abs(expected - self.contribution) > 1e-6:
            raise ValueError(
                f"contribution {self.contribution} does not equal "
                f"normalized x weight x 100 = {expected}"
            )
        return self


class SubstrateScore(Strict):
    """Composite wildfire substrate condition, 0-100, higher meaning more predisposed.

    This is a substrate score, not a fire risk score. It says nothing about ignition
    probability or fire weather on a given day. It describes the condition of the ground
    that a fire, once started, would arrive at.
    """

    score: float = Field(ge=0.0, le=100.0)
    band: Literal["low", "moderate", "elevated", "high", "extreme"]
    components: list[SubstrateComponent] = Field(min_length=1)
    weighting_scheme: str = Field(
        min_length=1, description="Named, versioned weighting used, e.g. 'gaia-wildfire-v1'."
    )
    interpretation: str = Field(min_length=1)
    caveats: list[str] = Field(
        default_factory=list,
        description="What this score does not account for. Stated, not buried.",
    )

    @model_validator(mode="after")
    def _score_equals_its_parts(self) -> SubstrateScore:
        total_weight = sum(c.weight for c in self.components)
        if abs(total_weight - 1.0) > 1e-6:
            raise ValueError(f"component weights must sum to 1.0, got {total_weight:.6f}")
        summed = sum(c.contribution for c in self.components)
        if abs(summed - self.score) > 1e-4:
            raise ValueError(
                f"score {self.score:.4f} does not equal the sum of its component "
                f"contributions {summed:.4f}"
            )
        return self


class SubstrateEnvelope(EnvelopeBase):
    """The substrate score, served under the same guarantees as any other number."""

    kind: Literal["substrate_score"] = "substrate_score"
    value: SubstrateScore


class RejectedValue(Strict):
    """A value the constraint engine refused.

    Deliberately has no ``value`` field. A rejected measurement is reported as an absence
    with a reason, never as a number the caller might use by accident.
    """

    claim_id: str = Field(pattern=CLAIM_ID_PATTERN)
    indicator: IndicatorId | None = None
    validation_status: Literal["rejected"] = "rejected"
    reason: str = Field(min_length=1)
    flags: list[ValidationFlag] = Field(min_length=1)
    provenance: ProvenanceChain
    geometry_hash: str = Field(min_length=8)
    period: DateRange
    generated_at: datetime


class ValidationReport(Strict):
    """The constraint engine's verdict on one candidate value, before it becomes a claim."""

    status: Literal["validated", "flagged", "rejected"]
    flags: list[ValidationFlag] = Field(default_factory=list)
    constraints_checked: list[str] = Field(min_length=1)
    confidence: Confidence
    confidence_basis: ConfidenceBasis
