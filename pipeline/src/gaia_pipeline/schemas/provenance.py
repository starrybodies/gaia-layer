"""Provenance: the chain from a served number back to the observations behind it.

Lesson 2 of the whitepaper — never let a language model be the system of record for a
quantitative claim — is only enforceable if every number carries the record of how it came
to exist. That record is this module.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, JsonValue

from .common import Strict


class StepKind(StrEnum):
    """What a provenance step represents.

    A well-formed chain starts with at least one ``observation`` and ends with a
    ``validation``. Everything between is ``processing``.
    """

    OBSERVATION = "observation"
    PROCESSING = "processing"
    VALIDATION = "validation"


class ProvenanceStep(Strict):
    """One link in the chain.

    The six fields the build prompt calls non-negotiable for an ingested record — source,
    dataset id, acquisition time, processing time, pipeline version, spatial reference —
    are all here. They are optional only where the step kind makes them meaningless: a
    validation step has no acquisition timestamp of its own, it inherits the one carried by
    the observation steps beneath it.
    """

    index: int = Field(ge=0, description="Position in the chain, 0 first.")
    kind: StepKind
    description: str = Field(
        min_length=1, description="Plain-language account of what this step did."
    )

    source: str | None = Field(
        default=None, description="Originating organisation, e.g. 'ESA/Copernicus'."
    )
    dataset_id: str | None = Field(
        default=None, description="Dataset identifier, e.g. 'sentinel-2-l2a'."
    )
    access_route: str | None = Field(
        default=None,
        description=(
            "How the data was reached, e.g. 'earth-search-v1' or 'open-meteo-archive'. "
            "Distinguishes the dataset from the intermediary that served it."
        ),
    )
    asset_ids: list[str] = Field(
        default_factory=list,
        description="Scene, granule or asset identifiers consumed by this step.",
    )

    acquired_at: datetime | None = Field(
        default=None, description="When the underlying observation was made."
    )
    processed_at: datetime = Field(description="When this step ran.")

    pipeline_version: str
    algorithm_version: str
    software: str | None = Field(
        default=None, description="Library and version doing the work, e.g. 'rasterio 1.4.3'."
    )

    spatial_ref: str = Field(
        description="CRS of this step's output, as an authority code, e.g. 'EPSG:32610'."
    )
    resolution_m: float | None = Field(default=None, gt=0.0)

    parameters: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Every parameter that affects the numeric output of this step.",
    )


ProvenanceChain = Annotated[
    list[ProvenanceStep],
    Field(
        min_length=1,
        description=(
            "Ordered chain from source observation to validated output. Never empty — a "
            "value without provenance cannot be constructed."
        ),
    ),
]


class Method(Strict):
    """The published method a value was computed by, so a consumer can check the maths."""

    name: str = Field(min_length=1)
    citation: str = Field(
        min_length=1, description="Full bibliographic citation for the method."
    )
    formula: str | None = None
    doi: str | None = None
    url: str | None = None
    notes: str | None = None
