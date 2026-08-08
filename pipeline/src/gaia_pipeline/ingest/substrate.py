"""Compute and store wildfire substrate scores.

Runs after the indicators exist, reading them back out of the lake rather than recomputing
anything. The score is a function of validated values only: a rejected indicator is treated
as absent, not as zero, because zero is a measurement and absence is not.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime

from ..config import AreaOfInterest, geometry_hash
from ..provenance import ChainBuilder
from ..schemas.envelope import ConfidenceBasis, ConfidenceComponent
from ..store import lake
from ..substrate import (
    CAVEATS,
    COMPONENTS,
    MINIMUM_WEIGHT_PRESENT,
    SUBSTRATE_METHOD,
    WEIGHTING_SCHEME,
    band_for,
    interpretation_for,
)
from ..version import ALGORITHM_VERSION, PIPELINE_VERSION
from .sentinel2 import months_between

log = logging.getLogger(__name__)


def _score_id(aoi_id: str, ghash: str, start: date, end: date) -> str:
    canonical = "|".join(
        [aoi_id, ghash, start.isoformat(), end.isoformat(), WEIGHTING_SCHEME, ALGORITHM_VERSION]
    )
    return "sub_" + hashlib.sha256(canonical.encode()).hexdigest()[:24]


def ingest(aoi: AreaOfInterest, start: date, end: date, *, force: bool = False) -> int:
    """One substrate score per month, with its full decomposition."""
    ghash = geometry_hash(aoi.geometry)
    conn = lake.connect()
    run_id = lake.start_run(
        conn,
        aoi_id=aoi.aoi_id,
        command="ingest substrate",
        parameters={"scheme": WEIGHTING_SCHEME, "start": start, "end": end},
    )

    written = 0
    try:
        for period in months_between(start, end):
            if not force:
                existing = conn.execute(
                    "SELECT 1 FROM substrate_score WHERE score_id = ?",
                    [_score_id(aoi.aoi_id, ghash, period[0], period[1])],
                ).fetchone()
                if existing is not None:
                    continue

            rows = conn.execute(
                """
                SELECT indicator, value, confidence, unit, method_json, provenance_json,
                       validation_status, flags_json, valid_pixels, total_pixels,
                       confidence_basis_json, mean, median, std, p10, p90, minimum, maximum,
                       period_start, period_end
                FROM indicator_value
                WHERE geometry_hash = ?
                  AND value IS NOT NULL AND validation_status <> 'rejected'
                  AND ((period_start = ? AND period_end = ?) OR period_start <= ?)
                """,
                [ghash, period[0], period[1], date(2000, 1, 2)],
            ).fetchall()

            by_indicator = {r[0]: r for r in rows}

            components: list[dict[str, object]] = []
            missing: list[str] = []
            weight_present = 0.0
            confidences: list[float] = []

            for spec in COMPONENTS:
                row = by_indicator.get(spec.indicator.value)
                if row is None:
                    missing.append(spec.indicator.value)
                    continue
                weight_present += spec.weight
                confidences.append(float(row[2]))

            if weight_present < MINIMUM_WEIGHT_PRESENT:
                log.warning(
                    "%s: only %.0f%% of the scheme's weight is available, skipping",
                    period[0].strftime("%Y-%m"),
                    100 * weight_present,
                )
                continue

            # Weights are renormalised across the components actually present, so the score
            # stays on a 0-100 scale. Which components were absent is returned alongside it,
            # because a score built from six inputs is not the same claim as one built from
            # seven.
            score = 0.0
            for spec in COMPONENTS:
                row = by_indicator.get(spec.indicator.value)
                if row is None:
                    continue
                value = float(row[1])
                normalised = spec.normalise(value)
                weight = spec.weight / weight_present
                contribution = normalised * weight * 100.0
                score += contribution

                components.append(
                    {
                        "indicator": spec.indicator.value,
                        "raw": {
                            "kind": "numeric",
                            "indicator": spec.indicator.value,
                            "value": value,
                            "unit": row[3],
                            "confidence": float(row[2]),
                            "confidence_basis": json.loads(row[10]),
                            "validation_status": row[6],
                            "flags": json.loads(row[7]),
                            "provenance": json.loads(row[5]),
                            "method": json.loads(row[4]),
                            "geometry_hash": ghash,
                            "period": {
                                "start": str(row[18]),
                                "end": str(row[19]),
                            },
                            "spatial_stats": {
                                "mean": float(row[11]),
                                "median": float(row[12]),
                                "std": float(row[13]),
                                "p10": float(row[14]),
                                "p90": float(row[15]),
                                "minimum": float(row[16]),
                                "maximum": float(row[17]),
                                "valid_pixels": int(row[8]),
                                "total_pixels": int(row[9]),
                            },
                            "generated_at": datetime.now().astimezone().isoformat(),
                        },
                        "normalized": normalised,
                        "normalization": spec.normalisation_description(),
                        "weight": weight,
                        "contribution": contribution,
                        "rationale": spec.rationale,
                    }
                )

            components.sort(key=lambda c: float(c["contribution"]), reverse=True)  # type: ignore[arg-type]
            top = str(components[0]["indicator"]) if components else "no component"
            band = band_for(score)

            # A composite is no more trustworthy than the weakest input under it. Taking a
            # mean here would let one confident terrain layer paper over a month of cloud.
            confidence = min(confidences) if confidences else 0.0

            basis = ConfidenceBasis(
                observation_count=len(components),
                spatial_coverage=weight_present,
                components=[
                    ConfidenceComponent(
                        name="weakest_input",
                        value=confidence,
                        weight=1.0,
                        description=(
                            "Lowest confidence among the contributing indicators. A composite "
                            "is no stronger than the weakest measurement inside it."
                        ),
                    )
                ],
                aggregation="minimum across contributing indicator confidences",
            )

            chain = ChainBuilder(aoi.analysis_crs, aoi.grid_resolution_m)
            chain.observation(
                description=(
                    f"{len(components)} validated indicator values for "
                    f"{period[0].strftime('%Y-%m')}, each with its own provenance chain "
                    "returned inside the score's component decomposition."
                ),
                source="Gaia layer",
                dataset_id="indicator_value",
                access_route="local-lake",
                asset_ids=[str(c["indicator"]) for c in components],
                acquired_at=None,
            )
            chain.processing(
                description=(
                    "Each indicator rescaled to [0, 1] against stated anchors for the "
                    "Coastal Douglas-fir zone, where 1 is the most fire-prone condition."
                ),
                software=f"gaia-pipeline {PIPELINE_VERSION}",
                parameters={
                    "anchors": {
                        spec.indicator.value: {"benign": spec.benign, "severe": spec.severe}
                        for spec in COMPONENTS
                    }
                },
            )
            chain.processing(
                description=(
                    f"Weighted sum under scheme {WEIGHTING_SCHEME}, weights renormalised "
                    f"across the {len(components)} components present "
                    f"({weight_present:.0%} of the scheme's total weight)."
                ),
                software=f"gaia-pipeline {PIPELINE_VERSION}",
                parameters={
                    "scheme": WEIGHTING_SCHEME,
                    "weight_present": weight_present,
                    "missing_indicators": missing,
                    "formula": SUBSTRATE_METHOD.formula,
                },
            )
            chain.validation(
                description=(
                    "Every contributing value had already passed the constraint engine; "
                    "rejected indicators were treated as absent rather than as zero."
                ),
                parameters={
                    "minimum_weight_present": MINIMUM_WEIGHT_PRESENT,
                    "components_used": len(components),
                },
            )

            conn.execute(
                """
                INSERT INTO substrate_score (
                    score_id, run_id, aoi_id, geometry_hash, period_start, period_end,
                    score, band, weighting_scheme, components_json, missing_indicators,
                    interpretation, caveats_json, validation_status, confidence,
                    confidence_basis_json, flags_json, method_json, provenance_json,
                    pipeline_version, algorithm_version, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (score_id) DO UPDATE SET
                    score = excluded.score, band = excluded.band,
                    components_json = excluded.components_json,
                    missing_indicators = excluded.missing_indicators,
                    interpretation = excluded.interpretation,
                    confidence = excluded.confidence,
                    provenance_json = excluded.provenance_json,
                    computed_at = excluded.computed_at
                """,
                [
                    _score_id(aoi.aoi_id, ghash, period[0], period[1]),
                    run_id,
                    aoi.aoi_id,
                    ghash,
                    period[0],
                    period[1],
                    score,
                    band,
                    WEIGHTING_SCHEME,
                    json.dumps(components),
                    json.dumps(missing),
                    interpretation_for(score, band, top),
                    json.dumps(list(CAVEATS)),
                    "validated",
                    confidence,
                    basis.model_dump_json(),
                    json.dumps([]),
                    SUBSTRATE_METHOD.model_dump_json(),
                    json.dumps([s.model_dump(mode="json") for s in chain.build()]),
                    PIPELINE_VERSION,
                    ALGORITHM_VERSION,
                    datetime.now().astimezone(),
                ],
            )
            written += 1
            log.info("%s: substrate score %.1f (%s)", period[0].strftime("%Y-%m"), score, band)

        lake.finish_run(conn, run_id, status="ok")
        return written
    except Exception as exc:
        lake.finish_run(conn, run_id, status="failed", error=str(exc))
        raise
    finally:
        conn.close()
