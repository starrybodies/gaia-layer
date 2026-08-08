"""Building provenance chains.

A chain is assembled as the value is produced, not reconstructed afterwards. Reconstruction
would mean the chain describes what we believe happened; building it in place means it
describes what did.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import rasterio

from .schemas.provenance import ProvenanceStep, StepKind
from .version import ALGORITHM_VERSION, PIPELINE_VERSION

RASTERIO_VERSION = f"rasterio {rasterio.__version__}"


class ChainBuilder:
    """Accumulates provenance steps in order, keeping indices contiguous."""

    def __init__(self, spatial_ref: str, resolution_m: float | None = None) -> None:
        self._steps: list[ProvenanceStep] = []
        self._spatial_ref = spatial_ref
        self._resolution_m = resolution_m

    def observation(
        self,
        *,
        description: str,
        source: str,
        dataset_id: str,
        access_route: str,
        asset_ids: list[str],
        acquired_at: datetime | None,
        spatial_ref: str | None = None,
        resolution_m: float | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> ChainBuilder:
        return self._add(
            StepKind.OBSERVATION,
            description=description,
            source=source,
            dataset_id=dataset_id,
            access_route=access_route,
            asset_ids=asset_ids,
            acquired_at=acquired_at,
            spatial_ref=spatial_ref,
            resolution_m=resolution_m,
            parameters=parameters,
        )

    def processing(
        self,
        *,
        description: str,
        software: str | None = RASTERIO_VERSION,
        parameters: dict[str, Any] | None = None,
        asset_ids: list[str] | None = None,
        spatial_ref: str | None = None,
        resolution_m: float | None = None,
    ) -> ChainBuilder:
        return self._add(
            StepKind.PROCESSING,
            description=description,
            software=software,
            parameters=parameters,
            asset_ids=asset_ids or [],
            spatial_ref=spatial_ref,
            resolution_m=resolution_m,
        )

    def validation(
        self,
        *,
        description: str,
        parameters: dict[str, Any] | None = None,
    ) -> ChainBuilder:
        return self._add(
            StepKind.VALIDATION,
            description=description,
            software=f"gaia-pipeline {PIPELINE_VERSION}",
            parameters=parameters,
        )

    def _add(self, kind: StepKind, **fields: Any) -> ChainBuilder:
        self._steps.append(
            ProvenanceStep(
                index=len(self._steps),
                kind=kind,
                description=fields["description"],
                source=fields.get("source"),
                dataset_id=fields.get("dataset_id"),
                access_route=fields.get("access_route"),
                asset_ids=fields.get("asset_ids") or [],
                acquired_at=fields.get("acquired_at"),
                processed_at=datetime.now(UTC),
                pipeline_version=PIPELINE_VERSION,
                algorithm_version=ALGORITHM_VERSION,
                software=fields.get("software"),
                spatial_ref=fields.get("spatial_ref") or self._spatial_ref,
                resolution_m=fields.get("resolution_m") or self._resolution_m,
                parameters=fields.get("parameters") or {},
            )
        )
        return self

    def build(self) -> list[ProvenanceStep]:
        return list(self._steps)
