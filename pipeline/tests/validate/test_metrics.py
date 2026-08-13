"""Metrics and the paired bootstrap.

Two properties matter more than the rest. A model compared against itself must produce an
interval containing zero — if it does not, the bootstrap manufactures significance and the
gate is meaningless. And a model with a real advantage must produce an interval that
excludes zero, or the gate can never be passed by anything.
"""

from __future__ import annotations

import numpy as np
import pytest

from gaia_pipeline.validate.metrics import (
    calibration_curve,
    delta_with_ci,
    evaluate,
    pooled,
    wilson_interval,
)

RNG = np.random.default_rng(11)


@pytest.fixture(scope="module")
def signal():
    """Labels with a genuine signal, plus a good and a poor predictor of them."""
    n = 4000
    latent = RNG.normal(size=n)
    y = (latent + RNG.normal(scale=0.6, size=n) > 1.0).astype(int)

    strong = 1.0 / (1.0 + np.exp(-(latent - 1.0)))

    # Genuinely worse, not merely rescaled. AUC is rank-based, so a monotone transform of
    # `latent` would score identically no matter how flat it looked.
    blurred = latent + RNG.normal(scale=1.5, size=n)
    weak = 1.0 / (1.0 + np.exp(-(blurred - 1.0)))
    return y, strong, weak


class TestEvaluate:
    def test_a_perfect_predictor_scores_perfectly(self) -> None:
        y = np.array([0, 0, 1, 1])
        result = evaluate(y, np.array([0.0, 0.0, 1.0, 1.0]))
        assert result.auc_roc == 1.0
        assert result.auc_pr == 1.0
        assert result.brier == 0.0

    def test_a_constant_predictor_scores_prevalence_on_auc_pr(self) -> None:
        """AUC-PR's floor is the positive rate, which is why it is the honest metric here."""
        y = np.array([0, 0, 0, 1])
        result = evaluate(y, np.full(4, 0.25))
        assert result.auc_pr == pytest.approx(0.25, abs=1e-9)
        assert result.prevalence == 0.25

    def test_an_inverted_predictor_scores_below_chance(self) -> None:
        y = np.array([0, 0, 1, 1])
        assert evaluate(y, np.array([1.0, 1.0, 0.0, 0.0])).auc_roc == 0.0

    def test_a_single_class_test_set_is_refused(self) -> None:
        """Scoring it would produce a number; the number would mean nothing."""
        with pytest.raises(ValueError, match="one class"):
            evaluate(np.zeros(10, dtype=int), np.full(10, 0.3))

    def test_an_empty_test_set_is_refused(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            evaluate(np.array([], dtype=int), np.array([]))

    def test_mismatched_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError, match="same length"):
            evaluate(np.array([0, 1]), np.array([0.5]))


class TestCalibration:
    def test_a_well_calibrated_model_sits_on_the_diagonal(self) -> None:
        prob = RNG.uniform(size=20000)
        y = (RNG.uniform(size=20000) < prob).astype(int)
        assert calibration_curve(y, prob).max_gap < 0.05

    def test_an_overconfident_model_does_not(self) -> None:
        prob = RNG.uniform(size=20000)
        y = (RNG.uniform(size=20000) < prob * 0.4).astype(int)
        assert calibration_curve(y, prob).max_gap > 0.2

    def test_empty_bins_are_kept(self) -> None:
        """A model that never predicts above 0.4 is saying something worth seeing."""
        prob = RNG.uniform(0.0, 0.4, size=500)
        y = (RNG.uniform(size=500) < prob).astype(int)
        curve = calibration_curve(y, prob, bins=10)

        assert len(curve.count) == 10
        assert curve.count[-1] == 0
        assert np.isnan(curve.observed[-1])


class TestHowMuchOfTheModelTheGapDescribes:
    """`max_gap` is a maximum, so on its own it says nothing about how many cells it covers.

    This is not a hypothetical complaint. The candidate model's headline calibration gap of
    0.532 came from a bin holding 23 of 3,835 cells, while the baseline it was being
    compared against had its worst bin over 108. Two numbers computed on populations five
    times apart were being read as if they were comparable.
    """

    def test_the_worst_bin_reports_how_many_cells_stood_behind_it(self) -> None:
        """One badly placed cell can set `max_gap`; the count is what says so."""
        prob = np.concatenate([np.full(999, 0.05), np.array([0.95])])
        y = np.zeros(1000, dtype=int)
        curve = calibration_curve(y, prob)

        assert curve.max_gap == pytest.approx(0.95, abs=1e-9)
        assert curve.max_gap_count == 1

    def test_a_single_stray_cell_barely_moves_the_expected_gap(self) -> None:
        """Weighting by population is what makes the two models comparable."""
        prob = np.concatenate([np.full(999, 0.05), np.array([0.95])])
        y = np.zeros(1000, dtype=int)
        curve = calibration_curve(y, prob)

        assert curve.expected_gap == pytest.approx((999 * 0.05 + 1 * 0.95) / 1000, abs=1e-9)
        assert curve.expected_gap < 0.06

    def test_a_well_calibrated_model_has_a_small_expected_gap(self) -> None:
        prob = RNG.uniform(size=20000)
        y = (RNG.uniform(size=20000) < prob).astype(int)
        assert calibration_curve(y, prob).expected_gap < 0.02

    def test_a_model_wrong_everywhere_cannot_hide_behind_the_weighting(self) -> None:
        """The expected gap is not a way of making real miscalibration look small."""
        prob = RNG.uniform(size=20000)
        y = (RNG.uniform(size=20000) < prob * 0.4).astype(int)
        assert calibration_curve(y, prob).expected_gap > 0.15

    def test_an_empty_curve_reports_nothing_rather_than_zero(self) -> None:
        curve = calibration_curve(np.array([], dtype=int), np.array([]))

        assert curve.max_gap_count == 0
        assert np.isnan(curve.max_gap)
        assert np.isnan(curve.expected_gap)


class TestWilsonInterval:
    def test_it_matches_the_published_value(self) -> None:
        low, high = wilson_interval(0.5, 100)
        assert low == pytest.approx(0.4038, abs=5e-4)
        assert high == pytest.approx(0.5962, abs=5e-4)

    def test_a_bin_that_observed_nothing_still_gets_a_width(self) -> None:
        """The normal approximation gives zero width here, which is the useless answer."""
        low, high = wilson_interval(0.0, 10)
        assert low == 0.0
        assert high > 0.25

    def test_it_stays_inside_zero_and_one(self) -> None:
        assert wilson_interval(1.0, 3) == (pytest.approx(0.4385, abs=5e-4), 1.0)

    def test_an_empty_bin_reports_nothing_rather_than_zero(self) -> None:
        assert all(np.isnan(bound) for bound in wilson_interval(0.0, 0))

    def test_a_small_bin_is_wide_enough_to_be_honest(self) -> None:
        """23 cells observing 5 events cannot rule out much, and must say so."""
        low, high = wilson_interval(5 / 23, 23)
        assert low < 0.10
        assert high > 0.40
        assert high < 0.75


class TestPairedBootstrap:
    def test_a_model_against_itself_cannot_show_an_advantage(self, signal) -> None:
        y, strong, _ = signal
        delta = delta_with_ci(y, strong, strong, n_bootstrap=500)

        assert delta.point == pytest.approx(0.0, abs=1e-12)
        assert not delta.excludes_zero
        assert delta.low <= 0.0 <= delta.high

    def test_a_real_advantage_is_detected(self, signal) -> None:
        y, strong, weak = signal
        delta = delta_with_ci(y, strong, weak, n_bootstrap=500)

        assert delta.point > 0.0
        assert delta.excludes_zero
        assert delta.low > 0.0

    def test_the_sign_says_who_won(self, signal) -> None:
        y, strong, weak = signal
        assert delta_with_ci(y, weak, strong, n_bootstrap=500).point < 0.0

    def test_brier_is_oriented_so_positive_still_means_better(self, signal) -> None:
        """Lower Brier is better, so the difference is flipped to keep one reading."""
        y, strong, weak = signal
        delta = delta_with_ci(y, strong, weak, metric="brier", n_bootstrap=500)
        assert delta.point > 0.0

    def test_pure_noise_does_not_produce_significance(self) -> None:
        """The test that stops the gate rewarding a coin flip."""
        y = RNG.integers(0, 2, size=3000)
        a = RNG.uniform(size=3000)
        b = RNG.uniform(size=3000)

        delta = delta_with_ci(y, a, b, n_bootstrap=800, seed=3)
        assert not delta.excludes_zero

    def test_it_is_deterministic_for_a_seed(self, signal) -> None:
        y, strong, weak = signal
        first = delta_with_ci(y, strong, weak, n_bootstrap=300, seed=5)
        second = delta_with_ci(y, strong, weak, n_bootstrap=300, seed=5)
        assert (first.low, first.high) == (second.low, second.high)

    def test_an_unknown_metric_is_refused(self, signal) -> None:
        y, strong, weak = signal
        with pytest.raises(ValueError, match="unknown metric"):
            delta_with_ci(y, strong, weak, metric="accuracy")

    def test_the_interval_is_reported_in_the_string_form(self, signal) -> None:
        y, strong, weak = signal
        assert "95% CI" in str(delta_with_ci(y, strong, weak, n_bootstrap=200))


class TestPooling:
    def test_spread_across_folds_survives_pooling(self) -> None:
        """Two models with the same mean and different spread are not the same model."""
        y = np.array([0, 0, 1, 1])
        steady = [evaluate(y, np.array([0.1, 0.2, 0.8, 0.9])) for _ in range(4)]
        erratic = [
            evaluate(y, np.array([0.1, 0.2, 0.8, 0.9])),
            evaluate(y, np.array([0.9, 0.8, 0.2, 0.1])),
        ]

        assert pooled(steady)["auc_roc_sd"] == 0.0
        assert pooled(erratic)["auc_roc_sd"] > 0.0

    def test_pooling_nothing_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no folds"):
            pooled([])
