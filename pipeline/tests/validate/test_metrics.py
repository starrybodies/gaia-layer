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
