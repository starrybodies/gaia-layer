"""Component D: fuel moisture, as a departure rather than a level.

The Drought Code at 400 is a hard summer in the Okanagan and an unremarkable one in the
Ponderosa Pine zone at the bottom of the valley, which runs 400 most Augusts. A hazard layer
built on the level would say the valley floor is always in drought and would be right about
the climate and useless about the year. So Component D is the same shape as Component B: how
far this season's codes sit from the same node's own distribution for the same date.

The sign is the trap. Water balance and soil moisture fall as things dry out; the drought
codes climb. Both have to come out of the standardisation oriented the same way, and the
tests below pin that in both directions rather than trusting the arithmetic to be obvious.
"""

from __future__ import annotations

from datetime import date, timedelta

import h3
import numpy as np
import pyarrow as pa
import pytest

from gaia_pipeline.eii.area import H3_RES
from gaia_pipeline.eii.components import moisture
from gaia_pipeline.eii.spine import Spine


def _weather(per_year: dict[int, tuple[float, float]], *, years: range) -> pa.Table:
    """A noon series where each year has constant weather: (temperature, rainfall)."""
    points, days, temp, rh, wind, rain = [], [], [], [], [], []
    for year in years:
        hot, wet = per_year.get(year, (20.0, 2.0))
        day = date(year, 3, 1)
        while day <= date(year, 9, 30):
            points.append(0)
            days.append(day)
            temp.append(hot)
            rh.append(45.0)
            wind.append(10.0)
            rain.append(wet)
            day += timedelta(days=1)
    return pa.table(
        {
            "point": pa.array(points, pa.int32()),
            "date": pa.array(days, pa.date32()),
            "temp_c": pa.array(temp, pa.float32()),
            "rh_pct": pa.array(rh, pa.float32()),
            "wind_kmh": pa.array(wind, pa.float32()),
            "rain_mm": pa.array(rain, pa.float32()),
        }
    )


@pytest.fixture(scope="module")
def spine(tmp_path_factory: pytest.TempPathFactory) -> Spine:
    centre = h3.latlng_to_cell(49.9, -119.5, H3_RES)
    return Spine.for_cells(sorted(h3.grid_disk(centre, 1)), tmp_path_factory.mktemp("moisture"))


class TestSeasonalCodes:
    def test_it_returns_the_codes_on_the_as_of_date(self) -> None:
        table = _weather({}, years=range(2015, 2025))
        codes = moisture.seasonal_codes(table, n_points=1, as_of=date(2024, 8, 1))

        assert set(codes) == {"dc", "bui", "vpd_kpa"}
        assert all(value.shape == (1,) for value in codes.values())
        assert np.isfinite(codes["dc"]).all()

    def test_a_hot_dry_season_carries_a_higher_drought_code(self) -> None:
        hot = moisture.seasonal_codes(
            _weather({2024: (32.0, 0.0)}, years=range(2015, 2025)),
            n_points=1,
            as_of=date(2024, 8, 1),
        )
        mild = moisture.seasonal_codes(
            _weather({2024: (14.0, 6.0)}, years=range(2015, 2025)),
            n_points=1,
            as_of=date(2024, 8, 1),
        )

        assert hot["dc"][0] > mild["dc"][0]

    def test_a_season_the_series_does_not_reach_comes_back_missing(self) -> None:
        table = _weather({}, years=range(2015, 2025))
        codes = moisture.seasonal_codes(table, n_points=1, as_of=date(2030, 8, 1))

        assert np.isnan(codes["dc"][0])
        assert not (codes["dc"] == 0.0).any()


class TestOrientation:
    def test_a_high_drought_code_against_low_years_scores_positive(self) -> None:
        """The codes climb as things dry, the opposite of soil moisture. Both read positive."""
        reference = np.array([[200.0, 220.0, 190.0, 210.0, 205.0, 215.0]])
        z = moisture.code_anomaly(np.array([600.0]), reference)

        assert z[0] > 1.0

    def test_a_low_drought_code_against_high_years_scores_negative(self) -> None:
        reference = np.array([[500.0, 520.0, 490.0, 510.0, 505.0, 515.0]])
        z = moisture.code_anomaly(np.array([200.0]), reference)

        assert z[0] < -1.0

    def test_a_flat_reference_refuses(self) -> None:
        reference = np.array([[300.0] * 6])
        assert np.isnan(moisture.code_anomaly(np.array([600.0]), reference)[0])


class TestComponentD:
    def test_it_combines_the_three_on_one_scale(self, spine) -> None:
        n = spine.n_cells
        table = moisture.component_d(
            spine,
            dc_z=np.full(n, 2.0),
            bui_z=np.full(n, 1.0),
            vpd_z=np.full(n, 0.0),
        )

        assert set(table.column_names) >= {
            "h3",
            "z_drought_code",
            "z_buildup_index",
            "z_vpd",
            "d_score",
            "contributing_variables",
            "uncertainty",
            "flags",
        }
        assert np.allclose(np.asarray(table.column("d_score")), 1.0)

    def test_a_cell_with_nothing_behind_it_scores_nothing(self, spine) -> None:
        n = spine.n_cells
        table = moisture.component_d(
            spine,
            dc_z=np.full(n, np.nan),
            bui_z=np.full(n, np.nan),
            vpd_z=np.full(n, np.nan),
        )

        score = np.asarray(table.column("d_score"))
        assert np.isnan(score).all()
        assert not (score == 0.0).any()

    def test_the_buildup_index_is_not_double_counted_with_the_drought_code(self) -> None:
        """BUI is derived from DC, so the weights have to say they are not independent."""
        assert moisture.WEIGHTS["buildup_index"] < moisture.WEIGHTS["vpd"]
        assert sum(moisture.WEIGHTS.values()) == pytest.approx(1.0)

    def test_the_sign_is_stated(self) -> None:
        assert set(moisture.SIGN) == {"drought_code", "buildup_index", "vpd"}
        assert all(value == 1.0 for value in moisture.SIGN.values())
        assert "drier" in moisture.STRUCTURE_OF_THE_SIGN
