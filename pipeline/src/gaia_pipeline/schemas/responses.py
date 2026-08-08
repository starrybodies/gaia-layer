"""Top-level request and response shapes for the five agent-facing tools.

These are the models exported to JSON Schema and compiled to Zod, so they are the contract
the MCP server and the REST API are both held to. Neither service may invent a field.
"""

from __future__ import annotations

import datetime as _dt
from datetime import date, datetime
from typing import Literal

from pydantic import Field

from .common import (
    BBox,
    Confidence,
    DateRange,
    Geometry,
    IndicatorFamily,
    IndicatorId,
    Strict,
)
from .envelope import (
    CLAIM_ID_PATTERN,
    NumericEnvelope,
    RejectedValue,
    SubstrateEnvelope,
    TrendEnvelope,
    ValidationReport,
)
from .provenance import Method, ProvenanceChain

# --------------------------------------------------------------------------- shared


class ResolvedGeometry(Strict):
    """The geometry a response describes, after snapping to the analysis grid."""

    geometry_hash: str = Field(min_length=8)
    bbox: BBox
    area_km2: float = Field(gt=0.0)
    analysis_crs: str = Field(description="CRS the indicators were computed in.")
    grid_resolution_m: float = Field(gt=0.0)
    aoi_id: str | None = Field(
        default=None, description="Set when the geometry matched a configured AOI."
    )


# --------------------------------------------------------------------------- requests


class EcologicalStateRequest(Strict):
    geometry: Geometry
    date_range: DateRange
    indicators: list[IndicatorId] | None = Field(
        default=None, description="Restrict the response. Omit for everything available."
    )


class SubstrateScoreRequest(Strict):
    geometry: Geometry
    # Annotated via the module alias: a field named ``date`` would shadow the ``date`` type
    # in the class namespace and Pydantic cannot resolve the annotation.
    date: _dt.date = Field(description="Date to score. Resolves to the month containing it.")


class ProvenanceRequest(Strict):
    claim_id: str = Field(pattern=CLAIM_ID_PATTERN)


class ComparePeriodsRequest(Strict):
    geometry: Geometry
    period_a: DateRange
    period_b: DateRange
    indicators: list[IndicatorId] | None = None


class CoverageRequest(Strict):
    aoi_id: str | None = None


# --------------------------------------------------------------------------- responses


class EcologicalStateResponse(Strict):
    """Validated ecological state for a geometry over a period."""

    aoi: ResolvedGeometry
    period: DateRange
    indicators: list[NumericEnvelope] = Field(
        description="One envelope per available indicator, period-aggregated."
    )
    trends: list[TrendEnvelope] = Field(
        default_factory=list, description="Per-indicator trend across the period."
    )
    rejected: list[RejectedValue] = Field(
        default_factory=list,
        description=(
            "Indicators that were computed but failed validation. Reported so their "
            "absence is visible rather than silent."
        ),
    )
    summary: str = Field(
        description=(
            "Plain-language summary rendered from the validated numbers by a deterministic "
            "template. Never model-generated — see docs/whitepaper.md, lesson 2."
        )
    )
    generated_at: datetime


class SubstrateScoreResponse(Strict):
    aoi: ResolvedGeometry
    period: DateRange
    score: SubstrateEnvelope
    missing_indicators: list[IndicatorId] = Field(
        default_factory=list,
        description="Indicators the scheme wanted but could not obtain for this period.",
    )
    generated_at: datetime


class SourceRecord(Strict):
    """A distinct source observation underlying a claim."""

    source: str
    dataset_id: str
    asset_id: str
    acquired_at: datetime | None = None
    access_route: str | None = None
    url: str | None = None
    spatial_ref: str


class ProvenanceResponse(Strict):
    """Full trace of a previously served claim back to source observations."""

    claim_id: str = Field(pattern=CLAIM_ID_PATTERN)
    claim_kind: Literal["numeric", "trend", "substrate_score"]
    indicator: IndicatorId | None = None
    value_repr: str = Field(description="The value as originally served, rendered as text.")
    unit: str
    confidence: Confidence
    validation: ValidationReport
    method: Method
    provenance: ProvenanceChain
    sources: list[SourceRecord] = Field(
        min_length=1, description="Deduplicated source observations behind the claim."
    )
    served_at: datetime = Field(description="When this claim was first emitted.")
    generated_at: datetime


class IndicatorComparison(Strict):
    indicator: IndicatorId
    period_a: NumericEnvelope
    period_b: NumericEnvelope
    delta: float = Field(description="period_b minus period_a, in the indicator's unit.")
    percent_change: float | None = Field(
        default=None,
        description="Omitted when the period A value is at or near zero.",
    )
    significant: bool
    significance_method: str = Field(
        description="How significance was decided, e.g. 'welch_t_test_alpha_0.05'."
    )
    p_value: float | None = Field(default=None, ge=0.0, le=1.0)
    interpretation: str


class ComparePeriodsResponse(Strict):
    aoi: ResolvedGeometry
    period_a: DateRange
    period_b: DateRange
    comparisons: list[IndicatorComparison]
    summary: str = Field(description="Deterministic template over the comparisons above.")
    generated_at: datetime


class IndicatorCoverage(Strict):
    indicator: IndicatorId
    family: IndicatorFamily
    unit: str
    first_period_start: date
    last_period_end: date
    period_count: int = Field(ge=0)
    mean_confidence: Confidence
    validated_count: int = Field(ge=0)
    flagged_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    source: str


class AoiCoverage(Strict):
    aoi_id: str
    name: str
    bbox: BBox
    area_km2: float = Field(gt=0.0)
    analysis_crs: str
    grid_resolution_m: float = Field(gt=0.0)
    indicators: list[IndicatorCoverage]
    last_ingested_at: datetime | None = None


class CoverageResponse(Strict):
    aois: list[AoiCoverage]
    pipeline_version: str
    algorithm_version: str
    generated_at: datetime


class ErrorResponse(Strict):
    """The only other shape a tool may return. Errors are structured, never prose blobs."""

    error: str = Field(min_length=1, description="Stable machine-readable code.")
    message: str = Field(min_length=1)
    detail: str | None = None
    retryable: bool = False
    generated_at: datetime
