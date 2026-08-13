"""Component B: the water balance, and what a departure has to be measured against.

An anomaly is a claim about a distribution, so the tests here are mostly about the
distribution. A dry season must score dry against a wet reference and ordinary against a
reference of equally dry seasons — the same number of millimetres, two different answers —
because that is the whole difference between a water balance and a rainfall total.

The rest pins the sign, which is the part that is easy to get backwards and impossible to
notice afterwards: positive means drier than this place's own normal, which is the direction
associated with more severe fire.
"""

from __future__ import annotations

from datetime import date, timedelta

import h3
import numpy as np
import pyarrow as pa
import pytest

from gaia_pipeline.eii.area import H3_RES
from gaia_pipeline.eii.components import water
from gaia_pipeline.eii.spine import Spine


def _series(per_year: dict[int, float], *, start: int, end: int, node: int = 0) -> pa.Table:
    """A daily P-and-ET0 series where each year has a constant daily rainfall.

    Years not named in `per_year` cycle through 1.0 to 3.0 mm a day, so the reference
    distribution has spread. A reference with no spread cannot standardise anything, which
    is its own test below rather than an accident of the fixture.
    """
    points, days, rain, demand = [], [], [], []
    for year in range(start, end + 1):
        default = 1.0 + 0.5 * float((year - start) % 5)
        day = date(year, 1, 1)
        while day.year == year:
            points.append(node)
            days.append(day)
            rain.append(max(per_year.get(year, default), 0.0))
            demand.append(0.0)
            day += timedelta(days=1)
    return pa.table(
        {
            "point": pa.array(points, pa.int32()),
            "date": pa.array(days, pa.date32()),
            "precipitation_mm": pa.array(rain, pa.float32()),
            "et0_mm": pa.array(demand, pa.float32()),
        }
    )


@pytest.fixture(scope="module")
def spine(tmp_path_factory: pytest.TempPathFactory) -> Spine:
    centre = h3.latlng_to_cell(49.9, -119.5, H3_RES)
    return Spine.for_cells(sorted(h3.grid_disk(centre, 1)), tmp_path_factory.mktemp("water"))


class TestDeficitAnomaly:
    def test_a_dry_year_against_wet_years_scores_dry(self) -> None:
        table = _series({2024: 0.2}, start=2015, end=2024)
        z = water.deficit_anomaly(table, n_points=1, as_of=date(2024, 8, 1), window_days=90)

        assert z.shape == (1,)
        assert z[0] > 1.0

    def test_the_same_millimetres_against_a_dry_reference_score_ordinary(self) -> None:
        """This is the point of the component. Absolute rainfall says neither of these.

        The same 0.2 mm a day that scored above 1.0 against a wet reference scores ordinary
        against a reference of equally dry seasons.
        """
        dry = {year: 0.15 + 0.05 * float((year - 2015) % 5) for year in range(2015, 2024)}
        table = _series({**dry, 2024: 0.2}, start=2015, end=2024)
        z = water.deficit_anomaly(table, n_points=1, as_of=date(2024, 8, 1), window_days=90)

        assert abs(float(z[0])) < 1.0

    def test_a_wet_year_scores_negative(self) -> None:
        table = _series({2024: 8.0}, start=2015, end=2024)
        z = water.deficit_anomaly(table, n_points=1, as_of=date(2024, 8, 1), window_days=90)

        assert z[0] < -1.0

    def test_a_reference_with_no_spread_refuses_rather_than_dividing_by_zero(self) -> None:
        flat = {year: 1.0 for year in range(2015, 2025)}
        table = _series(flat, start=2015, end=2024)
        z = water.deficit_anomaly(table, n_points=1, as_of=date(2024, 8, 1), window_days=90)

        assert np.isnan(z[0])

    def test_too_few_reference_years_come_back_missing(self) -> None:
        """Three seasons cannot describe a distribution, and must not pretend to."""
        table = _series({2024: 0.2}, start=2022, end=2024)
        z = water.deficit_anomaly(table, n_points=1, as_of=date(2024, 8, 1), window_days=90)

        assert np.isnan(z[0])

    def test_a_window_the_series_does_not_reach_comes_back_missing(self) -> None:
        table = _series({2024: 1.0}, start=2015, end=2024)
        z = water.deficit_anomaly(table, n_points=1, as_of=date(2030, 8, 1), window_days=90)

        assert np.isnan(z[0])


class TestMoistureAnomaly:
    def test_a_dry_column_against_wet_years_scores_positive(self) -> None:
        reference = np.array([[0.30, 0.31, 0.29, 0.32, 0.30]])
        current = np.array([0.20])

        z = water.moisture_anomaly(current, reference)

        assert z[0] > 1.0

    def test_a_wet_column_scores_negative(self) -> None:
        reference = np.array([[0.20, 0.21, 0.19, 0.22, 0.20]])
        current = np.array([0.30])

        assert water.moisture_anomaly(current, reference)[0] < -1.0

    def test_missing_reference_years_are_dropped_not_counted_as_dry(self) -> None:
        """A year ERA5-Land did not report is not a year the soil was at zero."""
        reference = np.array([[0.30, np.nan, 0.29, np.nan, 0.31, 0.30, 0.28, 0.32]])
        current = np.array([0.30])

        z = water.moisture_anomaly(current, reference)

        assert np.isfinite(z[0])
        assert abs(float(z[0])) < 1.0

    def test_too_few_surviving_seasons_come_back_missing(self) -> None:
        reference = np.array([[0.30, np.nan, 0.29, np.nan, np.nan]])
        current = np.array([0.30])

        assert np.isnan(water.moisture_anomaly(current, reference)[0])


class TestComponentB:
    def test_it_scores_every_cell_and_names_its_parts(self, spine) -> None:
        n = spine.n_cells
        table = water.component_b(
            spine,
            deficit_z=np.full(n, 1.5, dtype="float64"),
            soil_shallow_z=np.full(n, 0.5, dtype="float64"),
            soil_deep_z=np.full(n, 1.0, dtype="float64"),
        )

        assert table.num_rows == n
        assert set(table.column_names) >= {
            "h3",
            "z_water_deficit",
            "z_soil_shallow",
            "z_soil_deep",
            "b_score",
            "contributing_variables",
            "uncertainty",
            "flags",
        }
        assert np.allclose(np.asarray(table.column("b_score")), 1.0)

    def test_a_cell_with_one_part_is_scored_on_the_same_scale_as_one_with_three(
        self, spine
    ) -> None:
        """A mean, not a sum, so `contributing_variables` is what tells them apart."""
        n = spine.n_cells
        table = water.component_b(
            spine,
            deficit_z=np.full(n, 2.0, dtype="float64"),
            soil_shallow_z=np.full(n, np.nan, dtype="float64"),
            soil_deep_z=np.full(n, np.nan, dtype="float64"),
        )

        assert np.allclose(np.asarray(table.column("b_score")), 2.0)
        assert (np.asarray(table.column("contributing_variables")) == 1).all()

    def test_a_cell_with_nothing_behind_it_scores_nothing(self, spine) -> None:
        n = spine.n_cells
        table = water.component_b(
            spine,
            deficit_z=np.full(n, np.nan),
            soil_shallow_z=np.full(n, np.nan),
            soil_deep_z=np.full(n, np.nan),
        )

        score = np.asarray(table.column("b_score"))
        assert np.isnan(score).all()
        assert not (score == 0.0).any()

    def test_a_thin_measurement_carries_wider_uncertainty(self, spine) -> None:
        n = spine.n_cells
        one = water.component_b(
            spine,
            deficit_z=np.full(n, 1.0),
            soil_shallow_z=np.full(n, np.nan),
            soil_deep_z=np.full(n, np.nan),
        )
        three = water.component_b(
            spine,
            deficit_z=np.full(n, 1.0),
            soil_shallow_z=np.full(n, 1.0),
            soil_deep_z=np.full(n, 1.0),
        )

        assert np.asarray(one.column("uncertainty"))[0] > np.asarray(three.column("uncertainty"))[0]

    def test_the_sign_is_stated_and_points_at_dryness(self) -> None:
        """Positive is the fire-severe direction, for every component in the index."""
        assert set(water.SIGN) == {"water_deficit", "soil_shallow", "soil_deep"}
        assert all(value == 1.0 for value in water.SIGN.values())
        assert "drier" in water.STRUCTURE_OF_THE_SIGN
