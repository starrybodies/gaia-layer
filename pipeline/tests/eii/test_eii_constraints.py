"""The mechanistic constraint layer, and the difference between flagging and clamping.

The v0.1 engine already draws the line this one inherits: rejection says the number is
wrong, flagging says the number may be right and you should know what is odd about it. The
v0.2 rules add a third move — clamping — which says the number is outside what mechanism
allows, so it is pulled back to the edge of what mechanism allows and marked as having been
pulled. Nothing implausible is emitted silently, and nothing is silently deleted either.

Two of the three rules are statements about the model rather than about a cell. Monotonicity
and CFFDRS consistency either hold for a fitted model or they do not, and no per-cell clamp
can repair them; what they produce is a finding, and a model that fails them should not be
served at all. The water-balance rule is the per-cell one, and it is the only one that
changes a number.
"""

from __future__ import annotations

import numpy as np

from gaia_pipeline.eii import constraints
from gaia_pipeline.eii.sources import fbp


class TestMonotonicity:
    def test_a_model_that_rises_with_dryness_holds(self) -> None:
        matrix = np.column_stack([np.linspace(0.0, 600.0, 200), np.linspace(0.5, 0.1, 200)])

        def predict(x: np.ndarray) -> np.ndarray:
            return 1.0 / (1.0 + np.exp(-(x[:, 0] / 200.0 - 1.5)))

        outcomes = constraints.check_monotonicity(predict, matrix, columns=["dc", "soil_shallow"])

        assert {outcome.rule for outcome in outcomes} == {
            "monotonicity:dc",
            "monotonicity:soil_shallow",
        }
        assert next(o for o in outcomes if o.rule == "monotonicity:dc").held

    def test_a_model_that_falls_with_the_drought_code_does_not(self) -> None:
        """Backwards is the failure this rule exists to catch, and it must not be silent."""
        matrix = np.column_stack([np.linspace(0.0, 600.0, 200), np.linspace(0.5, 0.1, 200)])

        def predict(x: np.ndarray) -> np.ndarray:
            return 1.0 - 1.0 / (1.0 + np.exp(-(x[:, 0] / 200.0 - 1.5)))

        outcomes = constraints.check_monotonicity(predict, matrix, columns=["dc", "soil_shallow"])
        drought = next(o for o in outcomes if o.rule == "monotonicity:dc")

        assert not drought.held
        assert "decreas" in drought.detail

    def test_soil_moisture_is_expected_to_run_the_other_way(self) -> None:
        """Wetter soil must not raise predicted severity, which is the opposite direction."""
        matrix = np.column_stack([np.full(200, 300.0), np.linspace(0.05, 0.5, 200)])

        def predict(x: np.ndarray) -> np.ndarray:
            return 0.9 - x[:, 1]

        outcomes = constraints.check_monotonicity(predict, matrix, columns=["dc", "soil_shallow"])
        soil = next(o for o in outcomes if o.rule == "monotonicity:soil_shallow")

        assert soil.held

    def test_a_column_the_model_does_not_carry_is_not_invented(self) -> None:
        matrix = np.column_stack([np.linspace(0.0, 600.0, 50)])
        outcomes = constraints.check_monotonicity(lambda x: x[:, 0] / 600.0, matrix, columns=["dc"])

        assert len(outcomes) == 1
        assert outcomes[0].rule == "monotonicity:dc"

    def test_a_flat_model_holds_rather_than_failing(self) -> None:
        """A feature the model ignores is uninformative, not a violation of mechanism."""
        matrix = np.column_stack([np.linspace(0.0, 600.0, 100)])
        outcomes = constraints.check_monotonicity(
            lambda x: np.full(x.shape[0], 0.3), matrix, columns=["dc"]
        )

        assert outcomes[0].held


class TestCffdrsConsistency:
    def test_a_prediction_ordered_like_the_published_spread_holds(self) -> None:
        codes = np.array([7.0, 7.0, 2.0, 2.0, 31.0, 31.0, 11.0, 11.0])
        spread = fbp.rate_of_spread_ordering(codes)
        predicted = spread / spread.max()

        outcome = constraints.check_cffdrs(predicted, codes)

        assert outcome.held
        assert "rank correlation" in outcome.detail

    def test_a_prediction_that_inverts_the_ordering_does_not(self) -> None:
        codes = np.array([7.0, 7.0, 2.0, 2.0, 31.0, 31.0, 11.0, 11.0])
        spread = fbp.rate_of_spread_ordering(codes)
        predicted = 1.0 - spread / spread.max()

        assert not constraints.check_cffdrs(predicted, codes).held

    def test_non_fuel_is_dropped_rather_than_ranked_at_zero(self) -> None:
        """Water has no rate of spread; ranking it below aspen would be a made-up ordering."""
        codes = np.array([101.0, 102.0, 7.0, 7.0, 2.0, 2.0, 31.0, 31.0])
        spread = fbp.rate_of_spread_ordering(codes)
        predicted = np.where(np.isfinite(spread), spread / np.nanmax(spread), 0.99)

        outcome = constraints.check_cffdrs(predicted, codes)

        assert outcome.held
        assert outcome.affected == 2

    def test_too_few_fuel_types_cannot_establish_an_ordering(self) -> None:
        codes = np.array([7.0, 7.0, 7.0])
        outcome = constraints.check_cffdrs(np.array([0.2, 0.3, 0.4]), codes)

        assert outcome.held is False or "too few" in outcome.detail


class TestWaterBalanceSanity:
    def _setup(self):
        predicted = np.linspace(0.0, 1.0, 100)
        intactness = np.zeros(100)
        intactness[-5:] = 1.0  # the five most severe cells are intact riparian ground
        weather = np.zeros(100)
        return predicted, intactness, weather

    def test_an_intact_corridor_is_pulled_out_of_the_top_decile(self) -> None:
        predicted, intactness, weather = self._setup()

        result = constraints.apply_water_balance(predicted, intactness, weather)

        assert (result.value[-5:] < predicted[-5:]).all()
        assert result.outcome.affected == 5
        assert not result.outcome.held

    def test_the_clamp_stops_at_the_envelope_rather_than_zeroing(self) -> None:
        """Clamping is not deletion: the cell is still among the more severe, just not top."""
        predicted, intactness, weather = self._setup()

        result = constraints.apply_water_balance(predicted, intactness, weather)

        assert (result.value[-5:] > 0.5).all()

    def test_an_overriding_weather_signal_leaves_the_prediction_alone(self) -> None:
        """Mechanism says wet ground resists fire, not that it cannot burn under a heat dome."""
        predicted, intactness, _ = self._setup()
        weather = np.zeros(100)
        weather[-5:] = 100.0

        result = constraints.apply_water_balance(predicted, intactness, weather)

        assert np.allclose(result.value, predicted)
        assert result.outcome.held

    def test_a_clamped_cell_is_marked_low_confidence(self) -> None:
        predicted, intactness, weather = self._setup()

        result = constraints.apply_water_balance(predicted, intactness, weather)

        assert (result.confidence[-5:] == constraints.LOW_CONFIDENCE).all()
        assert (result.confidence[:50] == constraints.HIGH_CONFIDENCE).all()

    def test_degraded_ground_in_the_top_decile_is_left_alone(self) -> None:
        predicted = np.linspace(0.0, 1.0, 100)
        result = constraints.apply_water_balance(predicted, np.zeros(100), np.zeros(100))

        assert np.allclose(result.value, predicted)
        assert result.outcome.held

    def test_cells_with_no_riparian_measurement_are_not_clamped(self) -> None:
        """Missing intactness is not evidence of an intact corridor."""
        predicted = np.linspace(0.0, 1.0, 100)
        result = constraints.apply_water_balance(predicted, np.full(100, np.nan), np.zeros(100))

        assert np.allclose(result.value, predicted)


class TestTheReport:
    def test_every_rule_that_fired_is_named(self) -> None:
        predicted = np.linspace(0.0, 1.0, 100)
        intactness = np.zeros(100)
        intactness[-5:] = 1.0
        codes = np.tile([7.0, 2.0, 31.0, 11.0], 25)

        report = constraints.apply(
            predicted=predicted,
            fuel_codes=codes,
            riparian_intactness=intactness,
            weather=np.zeros(100),
        )

        assert any(not outcome.held for outcome in report.outcomes)
        assert {outcome.rule for outcome in report.outcomes} >= {"cffdrs", "water_balance"}

    def test_nothing_implausible_is_emitted_without_a_flag(self) -> None:
        predicted = np.linspace(0.0, 1.0, 100)
        intactness = np.zeros(100)
        intactness[-5:] = 1.0

        report = constraints.apply(
            predicted=predicted,
            fuel_codes=np.tile([7.0, 2.0, 31.0, 11.0], 25),
            riparian_intactness=intactness,
            weather=np.zeros(100),
        )

        changed = ~np.isclose(report.value, predicted)
        assert (report.confidence[changed] == constraints.LOW_CONFIDENCE).all()
        assert all(report.flags[position] for position in np.flatnonzero(changed))

    def test_a_clean_run_changes_nothing_and_says_so(self) -> None:
        predicted = np.linspace(0.0, 1.0, 100)

        report = constraints.apply(
            predicted=predicted,
            fuel_codes=np.tile([7.0, 2.0, 31.0, 11.0], 25),
            riparian_intactness=np.zeros(100),
            weather=np.zeros(100),
        )

        assert np.allclose(report.value, predicted)
        assert (report.confidence == constraints.HIGH_CONFIDENCE).all()
