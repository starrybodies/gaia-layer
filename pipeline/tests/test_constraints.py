"""Tests for the constraint engine.

Written before the engine. The build prompt calls validation the differentiator, and a
differentiator that is not pinned down by tests is a claim rather than a feature.

Three constraint classes get covered here, one describe block each:
  1. hard physical bounds
  2. temporal consistency
  3. cross-variable coherence

plus the confidence score and the invariants that must hold for any input at all.
"""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gaia_pipeline.schemas.common import DateRange, IndicatorId
from gaia_pipeline.validation import ValidationContext, validate_value


def ctx(
    indicator: IndicatorId,
    value: float,
    **overrides: object,
) -> ValidationContext:
    """A context with plausible defaults, so each test states only what it is about."""
    base: dict[str, object] = {
        "indicator": indicator,
        "value": value,
        "period": DateRange(start=date(2025, 8, 1), end=date(2025, 8, 31)),
        "history": [],
        "covariates": {},
        "observation_count": 3,
        "cloud_fraction": 0.05,
        "revisit_gap_days": 10.0,
        "spatial_coverage": 0.95,
    }
    base.update(overrides)
    return ValidationContext(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------- 1. hard bounds


class TestPhysicalBounds:
    @pytest.mark.parametrize("indicator", [IndicatorId.NDVI, IndicatorId.NDMI, IndicatorId.NBR])
    @pytest.mark.parametrize("value", [-1.0, -0.5, 0.0, 0.5, 1.0])
    def test_normalised_indices_never_reject_inside_minus_one_to_one(
        self, indicator: IndicatorId, value: float
    ) -> None:
        """Arithmetic guarantees this range, so nothing inside it is a defect.

        The extremes can still be *flagged* — an NDVI of -1.0 over land is not something
        vegetation does — but flagged means served, and that distinction is the point.
        """
        assert validate_value(ctx(indicator, value)).status != "rejected"

    @pytest.mark.parametrize(
        ("indicator", "value"),
        [
            (IndicatorId.NDVI, 0.72),
            (IndicatorId.NDVI, 0.15),
            (IndicatorId.NDMI, 0.24),
            (IndicatorId.NDMI, -0.35),
            (IndicatorId.NBR, 0.55),
            (IndicatorId.NBR, -0.20),
        ],
    )
    def test_ordinary_values_validate_cleanly(self, indicator: IndicatorId, value: float) -> None:
        report = validate_value(ctx(indicator, value))
        assert report.status == "validated"
        assert report.flags == []

    @pytest.mark.parametrize(
        ("indicator", "value"),
        [(IndicatorId.NDVI, -0.95), (IndicatorId.NDMI, 0.95), (IndicatorId.NBR, -0.98)],
    )
    def test_extremes_are_flagged_but_still_served(
        self, indicator: IndicatorId, value: float
    ) -> None:
        report = validate_value(ctx(indicator, value))
        assert report.status == "flagged"
        assert any(f.code == "outside_plausible_range" for f in report.flags)

    @pytest.mark.parametrize("indicator", [IndicatorId.NDVI, IndicatorId.NDMI, IndicatorId.NBR])
    @pytest.mark.parametrize("value", [-1.0001, 1.0001, 2.0, -7.0, 1e6])
    def test_normalised_indices_reject_outside_minus_one_to_one(
        self, indicator: IndicatorId, value: float
    ) -> None:
        report = validate_value(ctx(indicator, value))
        assert report.status == "rejected"
        assert any(f.code == "out_of_physical_bounds" for f in report.flags)

    def test_negative_vapour_pressure_deficit_is_rejected(self) -> None:
        report = validate_value(ctx(IndicatorId.VPD_KPA, -0.1))
        assert report.status == "rejected"

    def test_implausibly_high_vapour_pressure_deficit_is_flagged_not_rejected(self) -> None:
        # Physically possible, ecologically extreme for coastal British Columbia.
        report = validate_value(ctx(IndicatorId.VPD_KPA, 7.5))
        assert report.status == "flagged"
        assert any(f.code == "outside_plausible_range" for f in report.flags)

    def test_negative_precipitation_is_rejected(self) -> None:
        assert validate_value(ctx(IndicatorId.PRECIP_30D_MM, -1.0)).status == "rejected"

    def test_soil_moisture_above_saturation_is_rejected(self) -> None:
        report = validate_value(ctx(IndicatorId.SOIL_MOISTURE_0_7CM, 1.2))
        assert report.status == "rejected"

    def test_soil_moisture_within_range_passes(self) -> None:
        assert validate_value(ctx(IndicatorId.SOIL_MOISTURE_0_7CM, 0.28)).status == "validated"

    @pytest.mark.parametrize(
        ("indicator", "bad"),
        [
            (IndicatorId.SLOPE_DEG, 91.0),
            (IndicatorId.SLOPE_DEG, -1.0),
            (IndicatorId.ASPECT_DEG, 361.0),
            (IndicatorId.ASPECT_DEG, -0.5),
            (IndicatorId.ELEVATION_M, -600.0),
            (IndicatorId.DAYS_SINCE_RAIN, -1.0),
            (IndicatorId.TEMP_MAX_C, 71.0),
        ],
    )
    def test_terrain_and_climate_bounds(self, indicator: IndicatorId, bad: float) -> None:
        assert validate_value(ctx(indicator, bad)).status == "rejected"

    def test_nan_is_rejected(self) -> None:
        report = validate_value(ctx(IndicatorId.NDVI, float("nan")))
        assert report.status == "rejected"
        assert any(f.code == "not_a_number" for f in report.flags)

    def test_infinity_is_rejected(self) -> None:
        assert validate_value(ctx(IndicatorId.NDVI, float("inf"))).status == "rejected"


# ------------------------------------------------------------- 2. temporal consistency


class TestTemporalConsistency:
    def test_gradual_change_passes(self) -> None:
        history = [
            (date(2025, 5, 31), 0.30),
            (date(2025, 6, 30), 0.28),
            (date(2025, 7, 31), 0.25),
        ]
        assert validate_value(ctx(IndicatorId.NDMI, 0.22, history=history)).status == "validated"

    def test_implausibly_fast_recovery_is_flagged(self) -> None:
        """The case the build prompt names: NDMI recovering faster than biology allows.

        Canopy moisture can collapse in a month — fire, harvest, windthrow. It cannot climb
        back by the same amount in a month, because the leaves have to regrow first.
        """
        history = [
            (date(2025, 6, 30), 0.30),
            (date(2025, 7, 31), -0.20),  # disturbance
        ]
        report = validate_value(ctx(IndicatorId.NDMI, 0.28, history=history))
        assert report.status == "flagged"
        assert any(f.code == "implausible_recovery_rate" for f in report.flags)

    def test_abrupt_loss_is_allowed(self) -> None:
        """Loss is not symmetric with gain. A stand really can burn in a week."""
        history = [
            (date(2025, 6, 30), 0.32),
            (date(2025, 7, 31), 0.30),
        ]
        report = validate_value(ctx(IndicatorId.NDMI, -0.25, history=history))
        assert report.status == "validated"

    def test_extreme_loss_beyond_any_disturbance_is_flagged(self) -> None:
        history = [(date(2025, 7, 31), 0.90)]
        report = validate_value(ctx(IndicatorId.NDVI, -0.90, history=history))
        assert any(f.code == "implausible_change_rate" for f in report.flags)

    def test_rate_is_measured_per_month_not_per_step(self) -> None:
        """A jump across a six-month gap is not the same as a jump across one month."""
        far = [(date(2025, 2, 28), -0.20)]
        near = [(date(2025, 7, 31), -0.20)]
        assert validate_value(ctx(IndicatorId.NDMI, 0.28, history=far)).status == "validated"
        assert validate_value(ctx(IndicatorId.NDMI, 0.28, history=near)).status == "flagged"

    def test_empty_history_cannot_violate_a_rate(self) -> None:
        report = validate_value(ctx(IndicatorId.NDMI, 0.9, history=[]))
        assert not any("rate" in f.code for f in report.flags)

    def test_history_after_the_period_is_ignored(self) -> None:
        future = [(date(2026, 1, 31), -0.9)]
        report = validate_value(ctx(IndicatorId.NDMI, 0.28, history=future))
        assert not any("rate" in f.code for f in report.flags)

    def test_terrain_is_exempt_from_rate_checks(self) -> None:
        """Slope does not change month to month, and a change would be a re-survey."""
        history = [(date(2025, 7, 31), 5.0)]
        report = validate_value(ctx(IndicatorId.SLOPE_DEG, 30.0, history=history))
        assert not any("rate" in f.code for f in report.flags)


# --------------------------------------------------------- 3. cross-variable coherence


class TestCrossVariableCoherence:
    def test_wet_canopy_under_extreme_drought_is_flagged(self) -> None:
        """The exact case the build prompt names."""
        report = validate_value(
            ctx(
                IndicatorId.NDMI,
                0.35,
                covariates={
                    IndicatorId.VPD_KPA: 2.8,
                    IndicatorId.PRECIP_30D_MM: 0.0,
                },
            )
        )
        assert report.status == "flagged"
        assert any(f.code == "moisture_atmosphere_incoherent" for f in report.flags)

    def test_wet_canopy_with_recent_rain_is_fine(self) -> None:
        report = validate_value(
            ctx(
                IndicatorId.NDMI,
                0.35,
                covariates={
                    IndicatorId.VPD_KPA: 2.8,
                    IndicatorId.PRECIP_30D_MM: 60.0,
                },
            )
        )
        assert report.status == "validated"

    def test_dry_canopy_under_drought_is_coherent(self) -> None:
        report = validate_value(
            ctx(
                IndicatorId.NDMI,
                -0.10,
                covariates={
                    IndicatorId.VPD_KPA: 2.8,
                    IndicatorId.PRECIP_30D_MM: 0.0,
                },
            )
        )
        assert report.status == "validated"

    def test_saturated_soil_with_no_rain_and_high_demand_is_flagged(self) -> None:
        report = validate_value(
            ctx(
                IndicatorId.SOIL_MOISTURE_0_7CM,
                0.42,
                covariates={
                    IndicatorId.VPD_KPA: 3.0,
                    IndicatorId.PRECIP_30D_MM: 1.0,
                },
            )
        )
        assert any(f.code == "soil_atmosphere_incoherent" for f in report.flags)

    def test_missing_covariates_produce_no_coherence_flag(self) -> None:
        """Absence of evidence is not evidence of incoherence."""
        report = validate_value(ctx(IndicatorId.NDMI, 0.35, covariates={}))
        assert not any("incoherent" in f.code for f in report.flags)

    def test_greenness_and_burn_ratio_divergence_is_flagged(self) -> None:
        report = validate_value(ctx(IndicatorId.NDVI, 0.80, covariates={IndicatorId.NBR: -0.60}))
        assert any(f.code == "spectral_divergence" for f in report.flags)


# ------------------------------------------------------------------------ confidence


class TestConfidence:
    def test_ideal_observation_scores_high(self) -> None:
        report = validate_value(
            ctx(
                IndicatorId.NDVI,
                0.6,
                observation_count=6,
                cloud_fraction=0.0,
                revisit_gap_days=5.0,
                spatial_coverage=1.0,
            )
        )
        assert report.confidence > 0.9

    def test_single_cloudy_partial_observation_scores_low(self) -> None:
        report = validate_value(
            ctx(
                IndicatorId.NDVI,
                0.6,
                observation_count=1,
                cloud_fraction=0.85,
                revisit_gap_days=70.0,
                spatial_coverage=0.25,
            )
        )
        assert report.confidence < 0.35

    def test_more_observations_never_lower_confidence(self) -> None:
        a = validate_value(ctx(IndicatorId.NDVI, 0.6, observation_count=2)).confidence
        b = validate_value(ctx(IndicatorId.NDVI, 0.6, observation_count=5)).confidence
        assert b >= a

    def test_cloud_monotonically_reduces_confidence(self) -> None:
        clear = validate_value(ctx(IndicatorId.NDVI, 0.6, cloud_fraction=0.0)).confidence
        murky = validate_value(ctx(IndicatorId.NDVI, 0.6, cloud_fraction=0.6)).confidence
        assert murky < clear

    def test_flags_reduce_confidence(self) -> None:
        clean = validate_value(ctx(IndicatorId.VPD_KPA, 1.2)).confidence
        flagged = validate_value(ctx(IndicatorId.VPD_KPA, 7.5)).confidence
        assert flagged < clean

    def test_component_weights_sum_to_one(self) -> None:
        basis = validate_value(ctx(IndicatorId.NDVI, 0.6)).confidence_basis
        assert abs(sum(c.weight for c in basis.components) - 1.0) < 1e-9

    def test_rejected_values_report_zero_confidence(self) -> None:
        assert validate_value(ctx(IndicatorId.NDVI, 5.0)).confidence == 0.0


# ------------------------------------------------------------------------ invariants


class TestInvariants:
    """Properties that must hold for every input the engine can be handed."""

    @given(
        value=st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False),
        observations=st.integers(min_value=0, max_value=60),
        cloud=st.floats(min_value=0.0, max_value=1.0),
        gap=st.floats(min_value=0.0, max_value=400.0),
        coverage=st.floats(min_value=0.0, max_value=1.0),
        indicator=st.sampled_from(list(IndicatorId)),
    )
    @settings(max_examples=400, deadline=None)
    def test_confidence_always_within_unit_interval(
        self,
        value: float,
        observations: int,
        cloud: float,
        gap: float,
        coverage: float,
        indicator: IndicatorId,
    ) -> None:
        report = validate_value(
            ctx(
                indicator,
                value,
                observation_count=observations,
                cloud_fraction=cloud,
                revisit_gap_days=gap,
                spatial_coverage=coverage,
            )
        )
        assert 0.0 <= report.confidence <= 1.0

    @given(
        value=st.floats(allow_nan=True, allow_infinity=True, width=32),
        indicator=st.sampled_from(list(IndicatorId)),
    )
    @settings(max_examples=400, deadline=None)
    def test_engine_never_raises_and_always_decides(
        self, value: float, indicator: IndicatorId
    ) -> None:
        report = validate_value(ctx(indicator, value))
        assert report.status in {"validated", "flagged", "rejected"}
        assert len(report.constraints_checked) > 0

    @given(
        value=st.floats(allow_nan=True, allow_infinity=True, width=32),
        indicator=st.sampled_from(list(IndicatorId)),
    )
    @settings(max_examples=400, deadline=None)
    def test_status_and_flags_never_disagree(self, value: float, indicator: IndicatorId) -> None:
        report = validate_value(ctx(indicator, value))
        errors = [f for f in report.flags if f.severity.value == "error"]
        if report.status == "rejected":
            assert errors, "a rejection must carry at least one error-severity flag"
        else:
            assert not errors, "an error-severity flag must reject"
        if report.status == "validated":
            assert not report.flags
        if report.status == "flagged":
            assert report.flags

    @given(
        value=st.floats(min_value=-1.0, max_value=1.0, allow_nan=False),
        indicator=st.sampled_from([IndicatorId.NDVI, IndicatorId.NDMI, IndicatorId.NBR]),
    )
    @settings(max_examples=200, deadline=None)
    def test_in_range_indices_are_never_rejected_on_bounds_alone(
        self, value: float, indicator: IndicatorId
    ) -> None:
        report = validate_value(ctx(indicator, value))
        assert not any(f.code == "out_of_physical_bounds" for f in report.flags)

    @given(value=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False))
    @settings(max_examples=200, deadline=None)
    def test_a_rejected_value_is_never_reported_as_servable(self, value: float) -> None:
        report = validate_value(ctx(IndicatorId.NDVI, value))
        if report.status == "rejected":
            assert report.confidence == 0.0
