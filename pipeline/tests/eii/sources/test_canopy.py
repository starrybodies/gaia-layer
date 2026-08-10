"""Canopy height.

The mosaic keeps measurements and flags in the same byte, and the flag for water is 101.
Nothing in this archive is allowed to record a 101 m tree, so most of what follows is about
that boundary: that a flagged pixel is refused, that refusing it costs coverage rather than
accuracy, and that a cell left with nothing behind it comes back missing.

The fixture is a real 800 x 560 pixel window of the mosaic over Kelowna, cut from the URL the
module reads in production. It straddles Okanagan Lake, so it carries both sides of the
boundary in their real proportions. No test touches the network: `MOSAIC_URL` is pointed at
the fixture instead, which is the same code path because rasterio does not care whether a
dataset arrives over HTTP or off disk.
"""

from __future__ import annotations

from pathlib import Path

import h3
import numpy as np
import pytest
import rasterio
from rasterio.enums import Resampling

from gaia_pipeline.eii.area import H3_RES
from gaia_pipeline.eii.sources import canopy
from gaia_pipeline.eii.spine import Spine
from gaia_pipeline.raster import read_window

FIXTURE = Path(__file__).parent.parent / "fixtures" / "canopy" / "forest_height_2019_kelowna.tif"

GLAD_URL = "https://glad.umd.edu/Potapov/Forest_height_2019/Forest_height_2019_NAM.tif"

#: Open water in Okanagan Lake, roughly a kilometre off the Kelowna shore. The res-8 cell
#: around it holds no land at all, which is the case a mean has to refuse.
LAKE = (49.87, -119.52)

#: Forested slope above the west side of the lake, north of West Kelowna.
FOREST = (49.93, -119.57)

FLAGS = (101.0, 102.0, 103.0)


@pytest.fixture(scope="module")
def lake_spine(tmp_path_factory) -> Spine:
    """Cells across the lake and both of its shores."""
    centre = h3.latlng_to_cell(*LAKE, H3_RES)
    return Spine.for_cells(sorted(h3.grid_disk(centre, 3)), tmp_path_factory.mktemp("lake"))


@pytest.fixture(scope="module")
def forest_spine(tmp_path_factory) -> Spine:
    centre = h3.latlng_to_cell(*FOREST, H3_RES)
    return Spine.for_cells(sorted(h3.grid_disk(centre, 1)), tmp_path_factory.mktemp("forest"))


@pytest.fixture()
def local_mosaic(monkeypatch) -> None:
    monkeypatch.setattr(canopy, "MOSAIC_URL", str(FIXTURE))


class TestFixture:
    def test_the_fixture_carries_both_measurements_and_flags(self) -> None:
        """Otherwise every assertion below about refusing flags would pass by default."""
        with rasterio.open(FIXTURE) as src:
            values = set(np.unique(src.read(1)).tolist())

        assert 101 in values
        assert values & {value for value in range(3, 61)}
        assert not values - set(range(0, 61)) - {101, 102, 103}


class TestFetch:
    def test_the_read_lands_on_the_requested_grid(self, lake_spine, local_mosaic) -> None:
        heights, _ = canopy.fetch(lake_spine)

        assert heights.shape == lake_spine.grid.shape
        assert heights.dtype == np.float32

    def test_no_flag_value_survives_as_a_height(self, lake_spine, local_mosaic) -> None:
        heights, _ = canopy.fetch(lake_spine)
        measured = heights[np.isfinite(heights)]

        assert measured.size > 0
        assert measured.max() <= canopy.MAX_HEIGHT_M
        assert not np.isin(measured, FLAGS).any()

    def test_averaging_alone_would_have_leaked_a_flag(self, lake_spine, local_mosaic) -> None:
        """The reason for the second read, stated as a test rather than a comment."""
        naive = read_window(
            str(FIXTURE), lake_spine.grid, resampling=Resampling.average, dtype="float32"
        )
        heights, _ = canopy.fetch(lake_spine)

        assert naive.max() > canopy.MAX_HEIGHT_M
        assert np.nanmax(heights) <= canopy.MAX_HEIGHT_M

    def test_water_is_missing_rather_than_a_height_of_zero(self, lake_spine, local_mosaic) -> None:
        """A lake is not a hectare of very short trees, and must not average like one."""
        heights, _ = canopy.fetch(lake_spine)

        assert np.isnan(heights).mean() > 0.1

    def test_a_forested_window_reads_as_forest(self, forest_spine, local_mosaic) -> None:
        heights, _ = canopy.fetch(forest_spine)
        measured = heights[np.isfinite(heights)]

        assert np.isfinite(heights).mean() > 0.9
        assert 5.0 < measured.mean() < 30.0
        assert measured.max() < 50.0


class TestCellHeights:
    def test_a_cell_with_no_valid_pixels_is_missing_with_a_fraction_of_zero(
        self, lake_spine, local_mosaic
    ) -> None:
        means, fraction, _ = canopy.cell_heights(lake_spine)
        row = lake_spine.cells.column("h3").to_pylist().index(h3.latlng_to_cell(*LAKE, H3_RES))

        assert np.isnan(means[row])
        assert fraction[row] == 0.0

    def test_every_cell_is_a_plausible_height_or_nothing(self, lake_spine, local_mosaic) -> None:
        means, fraction, _ = canopy.cell_heights(lake_spine)
        present = np.isfinite(means)

        assert (means[present] >= 0.0).all()
        assert (means[present] <= canopy.MAX_HEIGHT_M).all()
        assert (fraction[present] > 0.0).all()
        assert (fraction[~present] == 0.0).all()

    def test_a_forested_cell_carries_a_full_hex_of_evidence(
        self, forest_spine, local_mosaic
    ) -> None:
        means, fraction, _ = canopy.cell_heights(forest_spine)

        assert np.isfinite(means).all()
        assert (fraction > 0.9).all()
        assert 5.0 < means.mean() < 30.0


class TestProvenance:
    def test_the_module_reads_the_published_glad_mosaic(self) -> None:
        assert canopy.MOSAIC_URL == GLAD_URL

    def test_the_source_record_says_how_the_data_was_reached(
        self, forest_spine, local_mosaic
    ) -> None:
        _, source = canopy.fetch(forest_spine)

        assert source.dataset == "GLAD forest height"
        assert source.version == "2019"
        assert source.access_route == "https-mosaic"
        assert source.native_resolution_m == 30.0
        assert source.native_timestep == "single epoch (2019)"
        assert "Potapov" in source.citation

    def test_the_method_carries_its_paper(self) -> None:
        assert canopy.CANOPY_METHOD.doi == "10.1016/j.rse.2020.112165"
        assert "Potapov" in canopy.CANOPY_METHOD.citation
