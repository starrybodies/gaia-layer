"""Component E: SPEI computed here, checked against the SPEI somebody else published.

Reimplementing a standard is a thing to be nervous about, and the answer is the same one the
fire weather codes got: run it against the published product and report the difference as a
number. SPEIbase v2.11 and this module share the years 2015 to 2022 over four corners of the
study area, which is 384 monthly values per timescale, and the agreement over them is pinned
below rather than described.

It is not perfect agreement and the bounds say so. Half a SPEI unit of mean absolute
difference is half a standard deviation of the thing being measured. Three reasons, none of
them a bug: the reference distribution here is forty years against SPEIbase's hundred and
twenty, the water balance uses FAO-56 reference evapotranspiration where SPEIbase uses
Penman-Monteith potential evapotranspiration, and the underlying reanalysis is ERA5 at 25 km
rather than CRU at half a degree. What survives is the ranking and the drought classification
— which months were dry, and which of them were dry enough to matter — and that is what the
component is used for.

The fixtures are the real recorded series: thirty-eight years of Open-Meteo daily balance and
SPEIbase's own values read over byte ranges. No test here touches the network.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import h3
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pytest

from gaia_pipeline.eii.area import H3_RES
from gaia_pipeline.eii.components import drought
from gaia_pipeline.eii.spine import Spine

FIXTURES = Path(__file__).parent / "fixtures" / "drought"
BALANCE = FIXTURES / "water-balance-1985-2022.json"
PUBLISHED = FIXTURES / "speibase-okanagan-2015-2022.json"


@pytest.fixture(scope="module")
def balance() -> tuple[pa.Table, int]:
    payload = json.loads(BALANCE.read_text())
    points, days, rain, demand = [], [], [], []
    for position, answer in enumerate(payload):
        daily = answer["daily"]
        for stamp, p, e in zip(
            daily["time"],
            daily["precipitation_sum"],
            daily["et0_fao_evapotranspiration"],
            strict=True,
        ):
            points.append(position)
            days.append(date.fromisoformat(stamp))
            rain.append(np.nan if p is None else p)
            demand.append(np.nan if e is None else e)
    table = pa.table(
        {
            "point": pa.array(points, pa.int32()),
            "date": pa.array(days, pa.date32()),
            "precipitation_mm": pa.array(rain, pa.float32()),
            "et0_mm": pa.array(demand, pa.float32()),
        }
    )
    return table, len(payload)


@pytest.fixture(scope="module")
def published() -> dict:
    return json.loads(PUBLISHED.read_text())


@pytest.fixture(scope="module")
def spine(tmp_path_factory: pytest.TempPathFactory) -> Spine:
    centre = h3.latlng_to_cell(49.9, -119.5, H3_RES)
    return Spine.for_cells(sorted(h3.grid_disk(centre, 1)), tmp_path_factory.mktemp("drought"))


def _month_end(month: date) -> date:
    following = date(month.year + (month.month == 12), (month.month % 12) + 1, 1)
    return following - timedelta(days=1)


def _agreement(table: pa.Table, n: int, truth: dict, scale: int) -> dict[str, float]:
    values = np.array(
        [
            [np.nan if value is None else value for value in row]
            for row in truth["values"][str(scale)]
        ]
    )
    months = [date.fromisoformat(stamp) for stamp in truth["months"]]

    ours = np.full_like(values, np.nan)
    for column, month in enumerate(months):
        # The last day of the month, because `spei_at` reports the last month *complete* at
        # the as-of date. Passing the first would compare our December against SPEIbase's
        # January and measure a lag rather than an agreement.
        as_of = _month_end(month)
        ours[:, column] = drought.spei_at(table, n_points=n, as_of=as_of, timescales=(scale,))[
            scale
        ]

    both = np.isfinite(ours) & np.isfinite(values)
    difference = ours[both] - values[both]
    return {
        "n": float(both.sum()),
        "r": float(np.corrcoef(ours[both], values[both])[0, 1]),
        "bias": float(difference.mean()),
        "mad": float(np.abs(difference).mean()),
        "drought_agreement": float(((ours[both] < -1.0) == (values[both] < -1.0)).mean()),
    }


class TestAgainstSPEIbase:
    """The measured agreement, pinned so it cannot quietly get worse."""

    @pytest.mark.parametrize("scale", [1, 3, 12])
    def test_it_ranks_the_same_months_dry(self, balance, published, scale) -> None:
        table, n = balance
        measured = _agreement(table, n, published, scale)

        assert measured["n"] > 250
        assert measured["r"] > 0.70

    @pytest.mark.parametrize("scale", [1, 3, 12])
    def test_it_is_not_systematically_wetter_or_drier(self, balance, published, scale) -> None:
        """A bias would mean the whole index sits off-centre, which a threshold would inherit."""
        table, n = balance
        assert abs(_agreement(table, n, published, scale)["bias"]) < 0.20

    @pytest.mark.parametrize("scale", [1, 3, 12])
    def test_the_difference_stays_inside_half_a_standard_deviation(
        self, balance, published, scale
    ) -> None:
        table, n = balance
        assert _agreement(table, n, published, scale)["mad"] < 0.60

    @pytest.mark.parametrize("scale", [1, 3, 12])
    def test_it_agrees_about_which_months_were_droughts(self, balance, published, scale) -> None:
        """The classification is what the component is used for, and it survives better."""
        table, n = balance
        assert _agreement(table, n, published, scale)["drought_agreement"] > 0.85


class TestTheFit:
    def test_a_median_value_scores_about_zero(self) -> None:
        sample = np.linspace(-200.0, 200.0, 60)
        alpha, _, _ = drought.log_logistic_parameters(sample)

        assert np.isfinite(alpha)
        value = drought.spei_from_reference(
            np.array([float(np.median(sample))]), sample.reshape(1, -1)
        )
        assert abs(float(value[0])) < 0.3

    def test_a_wet_value_scores_positive_and_a_dry_one_negative(self) -> None:
        sample = np.linspace(-200.0, 200.0, 60).reshape(1, -1)

        wet = drought.spei_from_reference(np.array([300.0]), sample)
        dry = drought.spei_from_reference(np.array([-300.0]), sample)

        assert wet[0] > 1.0
        assert dry[0] < -1.0

    def test_it_never_returns_an_infinity(self) -> None:
        """Below the fitted origin the probability is zero and the quantile is -inf."""
        sample = np.linspace(-200.0, 200.0, 60).reshape(1, -1)
        value = drought.spei_from_reference(np.array([-1e9]), sample)

        assert np.isfinite(value[0])
        assert value[0] < -2.0

    def test_too_short_a_record_is_not_fitted(self) -> None:
        alpha, _, _ = drought.log_logistic_parameters(np.linspace(-100.0, 100.0, 12))
        assert np.isnan(alpha)

    def test_a_flat_sample_is_not_fitted(self) -> None:
        alpha, _, _ = drought.log_logistic_parameters(np.full(60, 3.0))
        assert np.isnan(alpha)


class TestMonthlyAggregation:
    def test_a_partial_month_is_missing_rather_than_short(self, balance) -> None:
        """A month summed over half its days reads as dry, and drought is the measurement."""
        table, n = balance
        clipped = table.filter(pc.less(pc.field("date"), pa.scalar(date(2022, 12, 15))))
        months, values = drought.monthly_balance(clipped, n_points=n)

        assert months[-1] == date(2022, 12, 1)
        assert np.isnan(values[:, -1]).all()

    def test_accumulation_needs_every_month_in_its_window(self) -> None:
        balance = np.array([[1.0, np.nan, 3.0, 4.0, 5.0]])
        accumulated = drought.accumulate(balance, 3)

        assert np.isnan(accumulated[0, 0])
        assert np.isnan(accumulated[0, 2])
        assert np.isnan(accumulated[0, 3])
        assert accumulated[0, 4] == pytest.approx(12.0)


class TestComponentE:
    def test_the_timescales_survive_beside_the_blend(self, spine) -> None:
        n = spine.n_cells
        table = drought.component_e(
            spine,
            spei_by_scale={
                1: np.full(n, -2.0),
                3: np.full(n, -1.0),
                12: np.full(n, 0.0),
            },
        )

        assert set(table.column_names) >= {"h3", "spei_1", "spei_3", "spei_12", "e_score"}
        assert np.allclose(np.asarray(table.column("e_score")), 1.0)

    def test_drought_reads_positive(self, spine) -> None:
        """SPEI runs negative for drought; the index runs positive for hazard."""
        n = spine.n_cells
        table = drought.component_e(
            spine, spei_by_scale={scale: np.full(n, -2.0) for scale in drought.TIMESCALES}
        )

        assert (np.asarray(table.column("e_score")) > 0).all()

    def test_a_cell_with_nothing_behind_it_scores_nothing(self, spine) -> None:
        n = spine.n_cells
        table = drought.component_e(
            spine, spei_by_scale={scale: np.full(n, np.nan) for scale in drought.TIMESCALES}
        )

        score = np.asarray(table.column("e_score"))
        assert np.isnan(score).all()
        assert not (score == 0.0).any()

    def test_the_weights_are_equal_and_stated(self) -> None:
        assert set(drought.WEIGHTS) == set(drought.TIMESCALES)
        assert len(set(drought.WEIGHTS.values())) == 1
        assert sum(drought.WEIGHTS.values()) == pytest.approx(1.0)


class TestWhichMonthItReports:
    """A monthly index asked for on the fourteenth: the honest answer is last month's.

    This is the bug that emptied Component E for every one of the eighty-eight nodes on the
    first real backfill. `monthly_balance` correctly refuses a month it has only fourteen
    days of — a half-summed August reads as a dry August — and `spei_at` asked it for exactly
    that month. Nothing raised. The component came back all-missing and the only trace was a
    log line reading "0 of 88 nodes fitted".
    """

    def test_a_mid_month_as_of_reports_the_month_before(self) -> None:
        assert drought.latest_complete_month(date(2023, 8, 14)) == date(2023, 7, 1)

    def test_a_month_end_as_of_reports_its_own_month(self) -> None:
        assert drought.latest_complete_month(date(2023, 8, 31)) == date(2023, 8, 1)
        assert drought.latest_complete_month(date(2024, 2, 29)) == date(2024, 2, 1)

    def test_the_first_of_january_reaches_back_a_year(self) -> None:
        assert drought.latest_complete_month(date(2023, 1, 1)) == date(2022, 12, 1)

    def test_a_short_month_is_not_mistaken_for_complete(self) -> None:
        """28 February is a month end in a common year and is not one in a leap year."""
        assert drought.latest_complete_month(date(2023, 2, 28)) == date(2023, 2, 1)
        assert drought.latest_complete_month(date(2024, 2, 28)) == date(2024, 1, 1)

    def test_a_mid_month_as_of_actually_fits_nodes(self, balance) -> None:
        """The regression itself. The recorded window ends in 2022, so the date is 2022's."""
        table, n = balance
        result = drought.spei_at(table, n_points=n, as_of=date(2022, 8, 14))

        for scale, values in result.items():
            assert np.isfinite(values).any(), f"SPEI-{scale} fitted no node at all"

    def test_a_mid_month_and_the_previous_month_end_agree(self, balance) -> None:
        """Both name July, so both must return the same numbers rather than merely both work."""
        table, n = balance
        mid = drought.spei_at(table, n_points=n, as_of=date(2022, 8, 14))
        end = drought.spei_at(table, n_points=n, as_of=date(2022, 7, 31))

        for scale, values in mid.items():
            assert np.allclose(values, end[scale], equal_nan=True)


class TestTheDistributionRefusesLeftSkewedSamples:
    """The published log-logistic is right-skewed by construction, and some samples are not.

    Vicente-Serrano's three-parameter log-logistic has support above its origin and a shape
    parameter that must exceed one for the fitted distribution to have moments at all. A
    left-skewed reference sample drives the probability-weighted-moment estimator to a shape
    below one, or negative, and the honest answer is that this distribution cannot describe
    that sample.

    It is not hypothetical. Over the study lattice the three-month accumulation ending in
    July is left-skewed at most nodes — a dry interior summer has a floor it cannot go below
    and a ceiling that occasional wet years lift — and 75 of 88 nodes refuse to fit, against
    0 of 88 at one month and 3 of 88 at twelve. Recorded as D-019. What must not happen is a
    fit produced anyway.
    """

    def test_a_left_skewed_sample_is_refused_rather_than_forced(self) -> None:
        rng = np.random.default_rng(0)
        left_skewed = -np.abs(rng.gamma(2.0, 30.0, 40)) - 50.0

        alpha, beta, origin = drought.log_logistic_parameters(left_skewed)

        assert not np.isfinite(alpha)
        assert not np.isfinite(beta)
        assert not np.isfinite(origin)

    def test_a_right_skewed_sample_of_the_same_spread_does_fit(self) -> None:
        """The refusal has to be about shape, not about being hard to fit in general."""
        rng = np.random.default_rng(0)
        right_skewed = np.abs(rng.gamma(2.0, 30.0, 40)) - 50.0

        alpha, beta, _ = drought.log_logistic_parameters(right_skewed)

        assert np.isfinite(alpha)
        assert beta > 1.0

    def test_an_unfittable_node_comes_back_missing_not_zero(self) -> None:
        """Zero is the middle of a departure scale: the strongest claim of ordinariness there is."""
        rng = np.random.default_rng(1)
        reference = np.array([-np.abs(rng.gamma(2.0, 30.0, 40)) - 50.0])

        result = drought.spei_from_reference(np.array([-100.0]), reference)

        assert np.isnan(result[0])
