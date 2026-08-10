"""Tests for the two layer primitives added after the eleven-layer map.

Both encode a claim about the physical world rather than about the code:

  1. **Heat load.** Southwest is the hot aspect, not south, and flat ground has no aspect
     at all. An implementation that got the folding wrong would still return plausible
     numbers, so the ordering is what has to be pinned.
  2. **Majority aggregation.** A categorical layer must never be averaged. The mean of
     grassland and built-up is a real class code and a wrong answer, which is exactly the
     kind of error that survives review.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gaia_pipeline.cells import CellGrid
from gaia_pipeline.grid import AnalysisGrid
from gaia_pipeline.indices.terrain import heat_load_index
from gaia_pipeline.schemas.common import IndicatorId
from gaia_pipeline.validation.constraints import BOUNDS

MID_LATITUDE = 48.0


def hli(slope: float, aspect: float, latitude: float = MID_LATITUDE) -> float:
    """Heat load for a single facet, so a test reads as one statement about terrain."""
    return float(
        heat_load_index(
            np.array([[slope]], dtype="float32"),
            np.array([[aspect]], dtype="float32"),
            latitude,
        )[0, 0]
    )


class TestHeatLoad:
    def test_southwest_is_the_hottest_aspect(self) -> None:
        """Afternoon sun falls on already-warm ground, so the peak is southwest, not south."""
        southwest = hli(30.0, 225.0)
        for aspect in (0.0, 90.0, 135.0, 180.0, 270.0, 315.0):
            assert southwest > hli(30.0, aspect)

    def test_northeast_is_the_coolest_aspect(self) -> None:
        northeast = hli(30.0, 45.0)
        for aspect in (135.0, 180.0, 225.0, 270.0, 315.0):
            assert northeast < hli(30.0, aspect)

    def test_folding_is_symmetric_about_the_northeast_southwest_axis(self) -> None:
        """Southeast and west sit the same distance from the axis and score the same."""
        assert hli(25.0, 135.0) == pytest.approx(hli(25.0, 315.0), abs=1e-6)

    def test_flat_ground_ignores_aspect(self) -> None:
        """The aspect terms carry sin(slope). On a horizontal plane they vanish."""
        level = hli(0.0, 225.0)
        assert level == pytest.approx(hli(0.0, 45.0), abs=1e-6)
        assert level == pytest.approx(hli(0.0, float("nan")), abs=1e-6)

    def test_steeper_widens_the_spread_between_aspects(self) -> None:
        """Aspect matters more on a wall than on a gentle rise."""
        gentle = hli(5.0, 225.0) - hli(5.0, 45.0)
        steep = hli(45.0, 225.0) - hli(45.0, 45.0)
        assert steep > gentle > 0.0

    def test_higher_latitude_is_cooler_on_flat_ground(self) -> None:
        assert hli(0.0, 180.0, latitude=60.0) < hli(0.0, 180.0, latitude=30.0)

    def test_missing_slope_stays_missing(self) -> None:
        """A cell with no elevation model behind it must not acquire a heat load."""
        out = heat_load_index(
            np.array([[np.nan, 10.0]], dtype="float32"),
            np.array([[225.0, 225.0]], dtype="float32"),
            MID_LATITUDE,
        )
        assert np.isnan(out[0, 0])
        assert np.isfinite(out[0, 1])

    @settings(max_examples=200, deadline=None)
    @given(
        slope=st.floats(0.0, 90.0),
        aspect=st.floats(0.0, 360.0),
        latitude=st.floats(-70.0, 70.0),
    )
    def test_stays_inside_the_validated_envelope(
        self, slope: float, aspect: float, latitude: float
    ) -> None:
        """Whatever terrain it is handed, the result must satisfy its own hard bounds."""
        bounds = BOUNDS[IndicatorId.HEAT_LOAD]
        value = hli(slope, aspect, latitude)
        assert bounds.hard_min <= value <= bounds.hard_max


def cell_grid(block: int = 2, cells: int = 2) -> CellGrid:
    """A grid of `cells` x `cells` cells, each `block` x `block` pixels."""
    size = block * cells
    resolution = 500.0 / block
    grid = AnalysisGrid(
        crs="EPSG:32610",
        resolution_m=resolution,
        width=size,
        height=size,
        left=0.0,
        bottom=0.0,
        right=size * resolution,
        top=size * resolution,
    )
    return CellGrid.of(grid)


class TestMajority:
    def test_returns_the_commonest_class_not_the_mean(self) -> None:
        """Three grassland pixels and one built-up pixel are grassland, not shrubland."""
        cells = cell_grid()
        values = np.array(
            [
                [30.0, 30.0, 50.0, 50.0],
                [30.0, 50.0, 50.0, 50.0],
                [10.0, 10.0, 40.0, 90.0],
                [10.0, 20.0, 40.0, 40.0],
            ],
            dtype="float32",
        )
        classes, share = cells.majority(values, np.ones_like(values, dtype=bool))

        assert classes.tolist() == [[30.0, 50.0], [10.0, 40.0]]
        assert share.tolist() == [[0.75, 1.0], [0.75, 0.75]]

    def test_never_invents_a_class_that_is_not_present(self) -> None:
        cells = cell_grid()
        values = np.full((4, 4), 30.0, dtype="float32")
        values[0, 0] = 50.0
        classes, _ = cells.majority(values, np.ones_like(values, dtype=bool))
        assert set(np.unique(classes).tolist()) <= {30.0, 50.0}

    def test_masked_pixels_do_not_vote(self) -> None:
        """Sea and cloud are absent, not a class. Excluding them can flip the winner."""
        cells = cell_grid(cells=1)
        values = np.array([[50.0, 50.0], [30.0, 30.0]], dtype="float32")
        mask = np.array([[False, False], [True, True]])
        classes, share = cells.majority(values, mask)

        assert classes[0, 0] == 30.0
        # Half the cell carried data, and the share says so rather than claiming certainty.
        assert share[0, 0] == pytest.approx(0.5)

    def test_a_cell_with_no_data_is_missing_rather_than_zero(self) -> None:
        cells = cell_grid(cells=1)
        values = np.full((2, 2), np.nan, dtype="float32")
        classes, share = cells.majority(values, np.ones((2, 2), dtype=bool))

        assert np.isnan(classes[0, 0])
        assert share[0, 0] == 0.0
