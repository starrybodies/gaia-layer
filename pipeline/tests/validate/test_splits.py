"""Cross-validation splits.

The gate this project turns on is a difference in AUC-PR between two models. If the folds
leak, both models score higher, the difference is measured on inflated numbers, and the
result is worthless in a way that no amount of downstream care recovers. So the splitter
gets tested harder than the model does.
"""

from __future__ import annotations

import numpy as np
import pytest

from gaia_pipeline.validate.splits import (
    Fold,
    leakage_report,
    spatial_blocks,
    spatial_folds,
    temporal_holdout,
)

RNG = np.random.default_rng(20260809)


@pytest.fixture(scope="module")
def grid_cells():
    """A 60 km square of cells on a 500 m lattice, in projected metres."""
    step = 500.0
    axis = np.arange(0.0, 60_000.0, step)
    x, y = np.meshgrid(axis, axis)
    return x.reshape(-1) + 1_500_000.0, y.reshape(-1) + 500_000.0


class TestBlocking:
    def test_neighbouring_cells_share_a_block(self, grid_cells) -> None:
        x, y = grid_cells
        blocks = spatial_blocks(x, y, block_size_m=20_000.0)
        assert blocks[0] == blocks[1]

    def test_distant_cells_do_not(self, grid_cells) -> None:
        x, y = grid_cells
        blocks = spatial_blocks(x, y, block_size_m=20_000.0)
        far = np.flatnonzero((x > x.min() + 45_000) & (y > y.min() + 45_000))
        assert blocks[0] != blocks[far[0]]

    def test_block_count_follows_the_block_size(self, grid_cells) -> None:
        x, y = grid_cells
        coarse = np.unique(spatial_blocks(x, y, block_size_m=20_000.0)).size
        fine = np.unique(spatial_blocks(x, y, block_size_m=5_000.0)).size
        assert fine > coarse


class TestSpatialFolds:
    def test_every_cell_is_tested_exactly_once(self, grid_cells) -> None:
        x, y = grid_cells
        folds = spatial_folds(x, y, n_folds=5, seed=0)

        tested = np.concatenate([fold.test for fold in folds])
        assert np.array_equal(np.sort(tested), np.arange(x.size))

    def test_no_training_cell_sits_inside_the_buffer(self, grid_cells) -> None:
        """The property the whole exercise depends on, measured rather than assumed."""
        x, y = grid_cells
        folds = spatial_folds(x, y, n_folds=5, buffer_km=3.0, seed=0)

        report = leakage_report(x, y, folds, buffer_km=3.0)
        assert report["holds"] == 1.0
        assert report["minimum_train_test_distance_m"] >= 3000.0

    def test_a_wider_buffer_costs_more_training_cells(self, grid_cells) -> None:
        x, y = grid_cells
        narrow = spatial_folds(x, y, n_folds=5, buffer_km=1.0, seed=0)
        wide = spatial_folds(x, y, n_folds=5, buffer_km=5.0, seed=0)

        assert sum(f.excluded_by_buffer for f in wide) > sum(f.excluded_by_buffer for f in narrow)

    def test_the_cost_of_the_buffer_is_reported(self, grid_cells) -> None:
        """A silent 20% data loss is the kind of thing that should not be silent."""
        x, y = grid_cells
        folds = spatial_folds(x, y, n_folds=5, buffer_km=3.0, seed=0)
        assert all(fold.excluded_by_buffer > 0 for fold in folds)

    def test_folds_are_roughly_equal(self, grid_cells) -> None:
        x, y = grid_cells
        folds = spatial_folds(x, y, n_folds=5, seed=0)
        sizes = np.array([fold.test.size for fold in folds], dtype=float)
        assert sizes.std() / sizes.mean() < 0.35

    def test_the_split_is_deterministic(self, grid_cells) -> None:
        x, y = grid_cells
        first = spatial_folds(x, y, n_folds=5, seed=7)
        second = spatial_folds(x, y, n_folds=5, seed=7)
        assert all(np.array_equal(a.test, b.test) for a, b in zip(first, second, strict=True))

    def test_a_different_seed_gives_a_different_split(self, grid_cells) -> None:
        x, y = grid_cells
        first = spatial_folds(x, y, n_folds=5, seed=1)
        second = spatial_folds(x, y, n_folds=5, seed=2)
        assert not all(np.array_equal(a.test, b.test) for a, b in zip(first, second, strict=True))

    def test_random_splitting_would_leak_and_this_does_not(self, grid_cells) -> None:
        """The comparison that justifies the whole apparatus.

        A random 80/20 split of the same cells leaves training cells adjacent to test
        cells — 500 m apart on this lattice. That is the leakage the published AUC of 0.99
        is made of.
        """
        x, y = grid_cells
        indices = RNG.permutation(x.size)
        cut = int(0.8 * x.size)
        random_fold = Fold(train=indices[:cut], test=indices[cut:], excluded_by_buffer=0)

        random_report = leakage_report(x, y, [random_fold], buffer_km=3.0)
        blocked_report = leakage_report(x, y, spatial_folds(x, y, seed=0), buffer_km=3.0)

        assert random_report["minimum_train_test_distance_m"] < 1000.0
        assert blocked_report["minimum_train_test_distance_m"] >= 3000.0

    def test_too_few_folds_is_refused(self, grid_cells) -> None:
        x, y = grid_cells
        with pytest.raises(ValueError, match="at least two folds"):
            spatial_folds(x, y, n_folds=1)

    def test_mismatched_coordinates_are_refused(self) -> None:
        with pytest.raises(ValueError, match="same length"):
            spatial_folds(np.zeros(5), np.zeros(4))


class TestTemporalHoldout:
    def test_it_trains_on_the_past_and_tests_on_the_named_years(self) -> None:
        years = np.array([2015, 2018, 2020, 2021, 2022, 2023])
        fold = temporal_holdout(years, train_max_year=2020, test_years=(2021, 2023))

        assert set(years[fold.train]) == {2015, 2018, 2020}
        assert set(years[fold.test]) == {2021, 2023}

    def test_a_year_between_the_two_is_in_neither(self) -> None:
        """2022 is held out of both sides rather than quietly joining the training set."""
        years = np.array([2015, 2021, 2022, 2023])
        fold = temporal_holdout(years, train_max_year=2020, test_years=(2021, 2023))
        assert 2022 not in set(years[fold.train]) | set(years[fold.test])

    def test_an_empty_side_is_refused(self) -> None:
        with pytest.raises(ValueError, match="empty side"):
            temporal_holdout(np.array([2021, 2023]), train_max_year=2020)
