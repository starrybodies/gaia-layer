"""The H3 spine.

Every raster this build touches is aggregated through one integer index array, so a fault
here is a fault in every layer at once. These tests are about conservation and refusal:
nothing may be invented, nothing counted twice, and a cell with no evidence behind it must
come back missing rather than zero.
"""

from __future__ import annotations

import h3
import numpy as np
import pytest

from gaia_pipeline.eii.area import H3_PARENT_RES, H3_RES, STUDY_AREA
from gaia_pipeline.eii.spine import Spine, build_cells

# A single res-8 hex over West Kelowna, used to build a toy spine that is fast to rasterise.
TOY_CENTRE = (49.863, -119.583)


@pytest.fixture(scope="module")
def cells():
    return build_cells(STUDY_AREA)


@pytest.fixture(scope="module")
def toy_spine(tmp_path_factory):
    """A spine over one res-8 cell and its immediate neighbours."""
    centre = h3.latlng_to_cell(*TOY_CENTRE, H3_RES)
    ring = sorted(h3.grid_disk(centre, 1))
    return Spine.for_cells(ring, cache_dir=tmp_path_factory.mktemp("toy"))


class TestCellTable:
    def test_every_cell_is_at_the_index_resolution(self, cells) -> None:
        assert set(cells.column("res").to_pylist()) == {H3_RES}

    def test_parent_is_the_portfolio_aggregation_cell(self, cells) -> None:
        for child, parent in zip(
            cells.column("h3").to_pylist()[:200],
            cells.column("parent_h3").to_pylist()[:200],
            strict=True,
        ):
            assert parent == h3.cell_to_parent(child, H3_PARENT_RES)

    def test_cell_count_matches_the_area_divided_by_the_cell_size(self, cells) -> None:
        """Roughly 0.737 km2 per res-8 hex, so the count follows from the polygon's area."""
        expected = 28_000 / 0.737
        assert 0.75 * expected < cells.num_rows < 1.25 * expected

    def test_no_duplicate_cells(self, cells) -> None:
        ids = cells.column("h3").to_pylist()
        assert len(ids) == len(set(ids))

    def test_centroids_fall_inside_the_study_bounding_box(self, cells) -> None:
        bbox = STUDY_AREA.bbox()
        lat = np.asarray(cells.column("lat"))
        lon = np.asarray(cells.column("lon"))
        assert lat.min() >= bbox.south - 0.01
        assert lat.max() <= bbox.north + 0.01
        assert lon.min() >= bbox.west - 0.01
        assert lon.max() <= bbox.east + 0.01


class TestIndexRaster:
    def test_a_pixel_belongs_to_at_most_one_cell(self, toy_spine) -> None:
        """Hexes tile without overlap, and the rasterisation must not undo that."""
        counts = np.bincount(toy_spine.index[toy_spine.index >= 0], minlength=toy_spine.n_cells)
        assert counts.sum() == int((toy_spine.index >= 0).sum())

    def test_every_cell_gets_pixels(self, toy_spine) -> None:
        counts = np.bincount(toy_spine.index[toy_spine.index >= 0], minlength=toy_spine.n_cells)
        assert counts.min() > 0

    def test_pixel_counts_are_within_a_few_percent_of_equal(self, toy_spine) -> None:
        """Equal-area projection plus equal-area cells: the counts should barely vary."""
        counts = np.bincount(
            toy_spine.index[toy_spine.index >= 0], minlength=toy_spine.n_cells
        ).astype(float)
        assert counts.std() / counts.mean() < 0.05

    def test_the_cache_round_trips(self, toy_spine, tmp_path) -> None:
        reloaded = Spine.for_cells(
            toy_spine.cells.column("h3").to_pylist(), cache_dir=toy_spine.cache_dir
        )
        assert np.array_equal(reloaded.index, toy_spine.index)


class TestAggregators:
    def test_mean_of_a_constant_is_that_constant(self, toy_spine) -> None:
        values = np.full(toy_spine.grid.shape, 7.5, dtype="float32")
        means, fraction = toy_spine.mean(values)
        assert np.allclose(means, 7.5)
        assert np.allclose(fraction, 1.0)

    def test_masked_pixels_are_excluded_and_lower_the_fraction(self, toy_spine) -> None:
        values = np.full(toy_spine.grid.shape, 4.0, dtype="float32")
        mask = np.zeros(toy_spine.grid.shape, dtype=bool)
        mask[::2, :] = True  # every other row carries data

        means, fraction = toy_spine.mean(values, mask)

        assert np.allclose(means, 4.0)
        assert np.all(fraction < 0.6)
        assert np.all(fraction > 0.4)

    def test_nan_pixels_do_not_poison_the_mean(self, toy_spine) -> None:
        values = np.full(toy_spine.grid.shape, 2.0, dtype="float32")
        values[0, :] = np.nan
        means, _ = toy_spine.mean(values)
        assert np.allclose(means[np.isfinite(means)], 2.0)

    def test_a_cell_with_no_valid_pixels_is_missing_not_zero(self, toy_spine) -> None:
        values = np.full(toy_spine.grid.shape, np.nan, dtype="float32")
        means, fraction = toy_spine.mean(values)
        assert np.all(np.isnan(means))
        assert np.all(fraction == 0.0)

    def test_majority_returns_the_modal_class(self, toy_spine) -> None:
        values = np.full(toy_spine.grid.shape, 10.0, dtype="float32")
        values[:, : toy_spine.grid.width // 4] = 50.0  # a minority of built-up

        classes, share = toy_spine.majority(values)

        assert set(np.unique(classes[np.isfinite(classes)]).tolist()) <= {10.0, 50.0}
        assert np.all(share[np.isfinite(classes)] > 0.0)

    def test_majority_never_invents_an_absent_class(self, toy_spine) -> None:
        """The mean of 10 and 50 is 30, a real class code and the wrong answer."""
        values = np.where(np.indices(toy_spine.grid.shape)[1] % 2 == 0, 10.0, 50.0).astype(
            "float32"
        )
        classes, _ = toy_spine.majority(values)
        assert not np.isin(classes[np.isfinite(classes)], [30.0]).any()

    def test_fraction_counts_flagged_pixels_against_cell_area(self, toy_spine) -> None:
        flag = np.zeros(toy_spine.grid.shape, dtype=bool)
        flag[:, : toy_spine.grid.width // 2] = True

        share = toy_spine.fraction(flag)

        assert share.max() > 0.9
        assert share.min() < 0.1
