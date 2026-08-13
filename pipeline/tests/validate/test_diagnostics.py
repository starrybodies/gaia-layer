"""The diagnostic, whose job is to be able to return an unwelcome answer.

Every test here is a way the module could flatter the model instead. A permutation
importance that shuffles across folds measures spatial autocorrelation and reports it as
signal. A per-stratum table that drops the slices it cannot score reports excellent
performance on the fires nobody checked. A leave-one-fire-out that quietly falls back to
random splits reports within-fire interpolation as generalisation.

The fixture is synthetic and its signal is planted, so the diagnostic has a known right
answer to find: one feature carries everything and the rest are noise.
"""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from gaia_pipeline.validate import diagnostics
from gaia_pipeline.validate.splits import spatial_folds

RNG = np.random.default_rng(3)


def _as_probability(values: np.ndarray) -> np.ndarray:
    """Squash a z-scale score into [0, 1]; the metrics reject anything outside it."""
    return 1.0 / (1.0 + np.exp(-np.asarray(values, dtype="float64")))


def _table(n: int = 900) -> tuple[pa.Table, np.ndarray, list]:
    """A table where `a_score` carries the signal and the weather columns are noise."""
    signal = RNG.normal(size=n)
    labels = (signal + RNG.normal(scale=0.55, size=n) > 1.0).astype(int)

    x = RNG.uniform(0, 60_000, size=n)
    y = RNG.uniform(0, 60_000, size=n)
    folds = spatial_folds(x, y, n_folds=4, buffer_km=1.0, seed=0)

    fires = np.array([f"fire-{index % 6}" for index in range(n)])
    years = np.array([2018 + (index % 5) for index in range(n)])

    table = pa.table(
        {
            "h3": pa.array([f"cell-{i:05d}" for i in range(n)], pa.string()),
            "fire_id": pa.array(fires, pa.string()),
            "fire_year": pa.array(years, pa.int32()),
            "high_severity": pa.array(labels, pa.int8()),
            "ffmc": pa.array(RNG.normal(size=n), pa.float32()),
            "dmc": pa.array(RNG.normal(size=n), pa.float32()),
            "dc": pa.array(RNG.normal(size=n), pa.float32()),
            "isi": pa.array(RNG.normal(size=n), pa.float32()),
            "bui": pa.array(RNG.normal(size=n), pa.float32()),
            "fwi": pa.array(RNG.normal(size=n), pa.float32()),
            "vpd_kpa": pa.array(RNG.normal(size=n), pa.float32()),
            "fbp_fuel_type": pa.array(RNG.choice([2.0, 7.0, 31.0], size=n), pa.float32()),
            "elevation_m": pa.array(RNG.normal(size=n), pa.float32()),
            "slope_deg": pa.array(RNG.normal(size=n), pa.float32()),
            "aspect_deg": pa.array(RNG.uniform(0, 360, size=n), pa.float32()),
            "heat_load": pa.array(RNG.normal(size=n), pa.float32()),
            "z_canopy_height": pa.array(RNG.normal(size=n), pa.float32()),
            "z_crown_closure": pa.array(RNG.normal(size=n), pa.float32()),
            "z_stand_age": pa.array(RNG.normal(size=n), pa.float32()),
            "a_score": pa.array(signal, pa.float32()),
        }
    )
    return table, labels, folds


@pytest.fixture(scope="module")
def planted():
    return _table()


class TestPermutationImportance:
    def test_it_finds_the_feature_that_carries_the_signal(self, planted) -> None:
        table, labels, folds = planted
        columns = ["a_score", "ffmc", "dc"]
        matrix = np.column_stack(
            [np.asarray(table.column(name), dtype="float64") for name in columns]
        )

        effects = diagnostics.permutation_importance(
            matrix, labels, folds, columns, repeats=3, seed=0
        )

        assert effects[0].name == "a_score"
        assert effects[0].auc_pr_drop > 0.05

    def test_a_noise_feature_scores_far_below_the_real_one(self, planted) -> None:
        """Permutation importance measures what the model *uses*, not what carries signal.

        A boosted tree on nine hundred rows genuinely uses a pure-noise column, and shuffling
        it genuinely changes the predictions — here a noise column scores about 0.025 against
        the planted feature's 0.58. So the assertion that means something is the ratio, not
        an absolute floor. Separating use from signal is the ablation's job, not this one's.
        """
        table, labels, folds = planted
        columns = ["a_score", "ffmc", "dc"]
        matrix = np.column_stack(
            [np.asarray(table.column(name), dtype="float64") for name in columns]
        )

        effects = diagnostics.permutation_importance(
            matrix, labels, folds, columns, repeats=3, seed=0
        )
        by_name = {effect.name: effect.auc_pr_drop for effect in effects}

        assert by_name["a_score"] > 10.0 * max(by_name["ffmc"], by_name["dc"], 1e-9)

    def test_the_drop_carries_its_own_noise(self, planted) -> None:
        """A single shuffle is noisy; without a spread there is no way to tell."""
        table, labels, folds = planted
        columns = ["a_score", "ffmc"]
        matrix = np.column_stack(
            [np.asarray(table.column(name), dtype="float64") for name in columns]
        )

        effects = diagnostics.permutation_importance(
            matrix, labels, folds, columns, repeats=3, seed=0
        )

        assert all(effect.auc_pr_drop_sd >= 0.0 for effect in effects)


class TestGroupAblation:
    def test_removing_the_group_that_carries_the_signal_costs_the_most(self, planted) -> None:
        table, labels, folds = planted

        effects = diagnostics.group_ablation(table, labels, folds, seed=0)

        assert effects[0].name == "structure"
        assert effects[0].auc_pr_drop > 0.0

    def test_every_group_is_reported_even_when_it_costs_nothing(self, planted) -> None:
        """A group worth nothing is a finding, and hiding it makes the rest look inevitable."""
        table, labels, folds = planted

        names = {effect.name for effect in diagnostics.group_ablation(table, labels, folds)}

        assert names == {"weather", "fuel", "terrain", "structure"}


class TestStrata:
    def test_a_stratum_with_too_few_positives_is_named_not_dropped(self, planted) -> None:
        table, labels, _ = planted
        probability = RNG.uniform(size=labels.size)

        rows = diagnostics.by_stratum(
            table, labels, probability, probability, column="fire_id", minimum_positives=10_000
        )

        assert rows
        assert all(not row.scorable for row in rows)
        assert all("below the floor" in row.reason for row in rows)
        assert all(row.auc_pr is None for row in rows)

    def test_a_scorable_stratum_reports_lift_against_the_baseline(self, planted) -> None:
        table, labels, _ = planted
        good = _as_probability(table.column("a_score"))
        poor = RNG.uniform(size=labels.size)

        rows = diagnostics.by_stratum(
            table, labels, good, poor, column="fire_year", minimum_positives=5
        )
        scorable = [row for row in rows if row.scorable]

        assert scorable
        assert all(row.lift is not None for row in scorable)
        assert np.mean([row.lift for row in scorable]) > 0

    def test_the_counts_add_up_to_the_table(self, planted) -> None:
        table, labels, _ = planted
        probability = RNG.uniform(size=labels.size)

        rows = diagnostics.by_stratum(table, labels, probability, probability, column="fire_id")

        assert sum(row.n for row in rows) == table.num_rows


class TestLeaveOneFireOut:
    def test_it_holds_out_whole_fires(self, planted) -> None:
        table, labels, _ = planted

        answers = diagnostics.leave_one_fire_out(table, labels, seed=0)

        assert "delta" in answers
        assert answers["delta"] == pytest.approx(
            answers["candidate_with_component_a"] - answers["baseline_3_fwi_fbp"]
        )

    def test_the_planted_signal_survives_the_strict_split(self, planted) -> None:
        """If it did not, what the blocked folds measured was within-fire interpolation."""
        table, labels, _ = planted

        assert diagnostics.leave_one_fire_out(table, labels, seed=0)["delta"] > 0.0


class TestMisses:
    def test_it_compares_missed_severe_cells_against_caught_ones(self, planted) -> None:
        table, labels, _ = planted
        probability = _as_probability(table.column("a_score"))

        summary = diagnostics.characterise_misses(table, labels, probability)

        assert summary["hit"] + summary["missed"] == summary["severe_cells"]
        assert 0.0 <= summary["recall"] <= 1.0
        assert summary["most_different"]

    def test_it_says_which_features_separate_the_two(self, planted) -> None:
        table, labels, _ = planted
        probability = _as_probability(table.column("a_score"))

        summary = diagnostics.characterise_misses(table, labels, probability)

        assert "a_score" in summary["differences"]
        assert abs(summary["differences"]["a_score"]["standardised_gap"]) > 0.5


class TestTheWholeRun:
    def test_it_reports_the_unscorable_share_rather_than_implying_coverage(self, planted) -> None:
        table, labels, folds = planted

        result = diagnostics.run_diagnostics(table, labels, folds, seed=0)

        assert result.n_cells == table.num_rows
        assert result.auc_pr_candidate > result.auc_pr_baseline
        assert result.groups
        assert result.features
        assert result.to_dict()["strata"]
