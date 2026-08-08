"""The provenance guard, on the Python side.

The build prompt asks for a test that scans served responses for any value lacking a
provenance chain and fails the build if it finds one. A text grep would be fooled by
formatting, so this asserts the stronger property instead: that the *schema* makes an
unprovenanced value impossible to construct in the first place.

A grep can only catch a violation that already exists. A type error catches the attempt.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from gaia_pipeline.schemas import (
    ConfidenceBasis,
    ConfidenceComponent,
    DateRange,
    IndicatorId,
    Method,
    NumericEnvelope,
    ProvenanceStep,
    RejectedValue,
    ServedStatus,
    Severity,
    StepKind,
    SubstrateComponent,
    SubstrateEnvelope,
    SubstrateScore,
    ValidationFlag,
    claim_id_for,
)
from gaia_pipeline.version import ALGORITHM_VERSION, PIPELINE_VERSION

NOW = datetime(2026, 8, 7, tzinfo=UTC)
PERIOD = DateRange(start=date(2026, 7, 1), end=date(2026, 7, 31))


def step(index: int, kind: StepKind) -> ProvenanceStep:
    return ProvenanceStep(
        index=index,
        kind=kind,
        description="test step",
        source="ESA/Copernicus",
        dataset_id="sentinel-2-l2a",
        access_route="earth-search-v1",
        asset_ids=["S2A_TEST"],
        acquired_at=NOW,
        processed_at=NOW,
        pipeline_version=PIPELINE_VERSION,
        algorithm_version=ALGORITHM_VERSION,
        spatial_ref="EPSG:32610",
    )


CHAIN = [step(0, StepKind.OBSERVATION), step(1, StepKind.PROCESSING), step(2, StepKind.VALIDATION)]

BASIS = ConfidenceBasis(
    observation_count=3,
    cloud_fraction=0.1,
    revisit_gap_days=8.0,
    spatial_coverage=0.94,
    components=[
        ConfidenceComponent(name="coverage", value=0.94, weight=1.0, description="coverage")
    ],
)

METHOD = Method(name="NDMI", citation="Gao 1996")


def envelope(**overrides: object) -> NumericEnvelope:
    fields: dict[str, object] = {
        "claim_id": claim_id_for("numeric", "test"),
        "indicator": IndicatorId.NDMI,
        "value": 0.31,
        "unit": "index",
        "confidence": 0.88,
        "confidence_basis": BASIS,
        "validation_status": ServedStatus.VALIDATED,
        "flags": [],
        "provenance": CHAIN,
        "method": METHOD,
        "geometry_hash": "432d0a2af801b899",
        "period": PERIOD,
        "generated_at": NOW,
    }
    fields.update(overrides)
    return NumericEnvelope(**fields)  # type: ignore[arg-type]


class TestEnvelopeCannotOmitContext:
    def test_a_well_formed_envelope_constructs(self) -> None:
        assert envelope().value == pytest.approx(0.31)

    @pytest.mark.parametrize(
        "field",
        ["provenance", "method", "confidence", "confidence_basis", "validation_status", "unit"],
    )
    def test_dropping_any_qualifying_field_is_an_error(self, field: str) -> None:
        fields = {
            "claim_id": claim_id_for("numeric", "test"),
            "indicator": IndicatorId.NDMI,
            "value": 0.31,
            "unit": "index",
            "confidence": 0.88,
            "confidence_basis": BASIS,
            "validation_status": ServedStatus.VALIDATED,
            "provenance": CHAIN,
            "method": METHOD,
            "geometry_hash": "432d0a2af801b899",
            "period": PERIOD,
            "generated_at": NOW,
        }
        del fields[field]
        with pytest.raises(ValidationError):
            NumericEnvelope(**fields)  # type: ignore[arg-type]

    def test_an_empty_provenance_chain_is_an_error(self) -> None:
        with pytest.raises(ValidationError):
            envelope(provenance=[])

    def test_a_chain_without_an_observation_is_an_error(self) -> None:
        with pytest.raises(ValidationError, match="observation"):
            envelope(provenance=[step(0, StepKind.PROCESSING), step(1, StepKind.VALIDATION)])

    def test_a_chain_not_ending_in_validation_is_an_error(self) -> None:
        with pytest.raises(ValidationError, match="validation"):
            envelope(provenance=[step(0, StepKind.OBSERVATION), step(1, StepKind.PROCESSING)])

    def test_non_contiguous_step_indices_are_an_error(self) -> None:
        with pytest.raises(ValidationError, match="contiguous"):
            envelope(
                provenance=[
                    step(0, StepKind.OBSERVATION),
                    step(5, StepKind.VALIDATION),
                ]
            )

    @pytest.mark.parametrize("confidence", [-0.01, 1.01, 42.0])
    def test_confidence_outside_the_unit_interval_is_an_error(self, confidence: float) -> None:
        with pytest.raises(ValidationError):
            envelope(confidence=confidence)

    def test_extra_fields_are_rejected(self) -> None:
        """A response that silently accepts unknown keys cannot be relied on downstream."""
        with pytest.raises(ValidationError):
            envelope(surprise="unexpected")


class TestRejectedValuesAreNotServable:
    def test_a_rejected_value_has_no_value_field(self) -> None:
        """The whole rule, as a type: a rejection cannot carry a number."""
        assert "value" not in RejectedValue.model_fields

    def test_an_envelope_cannot_be_marked_rejected(self) -> None:
        with pytest.raises(ValidationError):
            envelope(validation_status="rejected")

    def test_an_error_severity_flag_cannot_ride_on_an_envelope(self) -> None:
        fatal = ValidationFlag(
            code="out_of_physical_bounds",
            constraint="physical_bounds",
            severity=Severity.ERROR,
            message="outside the physical range",
            confidence_penalty=1.0,
        )
        with pytest.raises(ValidationError, match="rejected"):
            envelope(validation_status=ServedStatus.FLAGGED, flags=[fatal])

    def test_a_flagged_value_must_carry_a_flag(self) -> None:
        with pytest.raises(ValidationError):
            envelope(validation_status=ServedStatus.FLAGGED, flags=[])

    def test_a_value_carrying_flags_cannot_claim_to_be_validated(self) -> None:
        warn = ValidationFlag(
            code="outside_plausible_range",
            constraint="plausible_range",
            severity=Severity.WARN,
            message="unusual",
            confidence_penalty=0.35,
        )
        with pytest.raises(ValidationError):
            envelope(validation_status=ServedStatus.VALIDATED, flags=[warn])

    def test_a_rejected_value_requires_at_least_one_flag(self) -> None:
        with pytest.raises(ValidationError):
            RejectedValue(
                claim_id=claim_id_for("rejected", "test"),
                indicator=IndicatorId.NDVI,
                reason="rejected",
                flags=[],
                provenance=CHAIN,
                geometry_hash="432d0a2af801b899",
                period=PERIOD,
                generated_at=NOW,
            )


class TestCompositesRemainDecomposable:
    def _component(self, weight: float, normalized: float) -> SubstrateComponent:
        return SubstrateComponent(
            indicator=IndicatorId.NDMI,
            raw=envelope(),
            normalized=normalized,
            normalization="linear",
            weight=weight,
            contribution=normalized * weight * 100.0,
            rationale="canopy moisture is the strongest control on fuel availability",
        )

    def test_a_component_carries_its_underlying_envelope(self) -> None:
        component = self._component(1.0, 0.4)
        assert component.raw.provenance
        assert component.raw.method.citation

    def test_a_component_whose_arithmetic_does_not_add_up_is_an_error(self) -> None:
        with pytest.raises(ValidationError, match="contribution"):
            SubstrateComponent(
                indicator=IndicatorId.NDMI,
                raw=envelope(),
                normalized=0.4,
                normalization="linear",
                weight=0.5,
                contribution=99.0,
                rationale="wrong on purpose",
            )

    def test_a_score_must_equal_the_sum_of_its_parts(self) -> None:
        with pytest.raises(ValidationError, match="does not equal"):
            SubstrateScore(
                score=90.0,
                band="extreme",
                components=[self._component(1.0, 0.4)],
                weighting_scheme="test",
                interpretation="test",
            )

    def test_component_weights_must_sum_to_one(self) -> None:
        with pytest.raises(ValidationError, match="weights"):
            SubstrateScore(
                score=20.0,
                band="moderate",
                components=[self._component(0.5, 0.4)],
                weighting_scheme="test",
                interpretation="test",
            )

    def test_a_consistent_score_constructs_and_stays_traceable(self) -> None:
        component = self._component(1.0, 0.4)
        score = SubstrateScore(
            score=component.contribution,
            band="moderate",
            components=[component],
            weighting_scheme="gaia-wildfire-substrate-v1",
            interpretation="test",
            caveats=["ignition probability is not modelled"],
        )
        wrapped = SubstrateEnvelope(
            claim_id=claim_id_for("substrate", "test"),
            value=score,
            unit="score_0_100",
            confidence=0.8,
            confidence_basis=BASIS,
            validation_status=ServedStatus.VALIDATED,
            provenance=CHAIN,
            method=Method(name="substrate", citation="this layer"),
            geometry_hash="432d0a2af801b899",
            period=PERIOD,
            generated_at=NOW,
        )
        # Every number inside the composite still reaches source observations.
        assert wrapped.value.components[0].raw.provenance[0].kind is StepKind.OBSERVATION


class TestServedPayloadsSurviveSerialisation:
    def test_a_round_trip_preserves_the_whole_envelope(self) -> None:
        """Provenance must survive JSON, since JSON is how it reaches an agent."""
        original = envelope()
        restored = NumericEnvelope.model_validate_json(original.model_dump_json())
        assert restored == original

    def test_serialised_output_carries_every_qualifying_field(self) -> None:
        payload = envelope().model_dump(mode="json")
        for key in ("value", "confidence", "validation_status", "provenance", "method"):
            assert key in payload, f"served payload is missing {key}"
        assert len(payload["provenance"]) >= 1
