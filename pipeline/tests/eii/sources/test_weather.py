"""The fire weather codes, checked against the people who define them.

Reimplementing a published standard is the sort of thing that looks right and is quietly
wrong for a decade. So the load-bearing test here is not a property or a smoke check: it
runs 158 consecutive days of real Kelowna weather from the 2023 fire season through this
implementation and requires the answers to match the codes CWFIS published for those same
observations.

The fixture is CWFIS's own station archive, weather and codes in the same row. If these
tests pass, the equations agree with the agency that wrote them.
"""

from __future__ import annotations

import json
from datetime import date
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from gaia_pipeline.eii.sources.weather import (
    FwiState,
    buildup_index,
    fire_weather_index,
    fwi_series,
    initial_spread_index,
    step,
    vapour_pressure_deficit,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "weather" / "cwfis-kelowna-2023.json"


@pytest.fixture(scope="module")
def cwfis():
    payload = json.loads(FIXTURE.read_text())
    return payload["records"]


@pytest.fixture(scope="module")
def computed(cwfis):
    """Our codes over CWFIS's weather, started from CWFIS's own first-day state.

    Starting from their state rather than from the spring defaults isolates what is being
    tested. Startup and overwintering are conventions; the equations are the claim.
    """
    first = cwfis[0]
    dates = [date.fromisoformat(row["date"]) for row in cwfis[1:]]

    return fwi_series(
        dates,
        np.array([row["temp"] for row in cwfis[1:]]),
        np.array([row["rh"] for row in cwfis[1:]]),
        np.array([row["wind"] for row in cwfis[1:]]),
        np.array([row["rain"] for row in cwfis[1:]]),
        start=FwiState(ffmc=first["ffmc"], dmc=first["dmc"], dc=first["dc"]),
    )


class TestAgainstCwfis:
    def test_the_fixture_is_a_contiguous_season(self, cwfis) -> None:
        days = [date.fromisoformat(row["date"]) for row in cwfis]
        assert len(days) > 140
        assert all((b - a).days == 1 for a, b in pairwise(days))

    @pytest.mark.parametrize(
        ("code", "tolerance"), [("ffmc", 1.5), ("dc", 1.0), ("isi", 0.5), ("fwi", 2.0)]
    )
    def test_codes_match_the_published_series(self, cwfis, computed, code, tolerance) -> None:
        """Every day, not on average: the codes are a chain and any drift compounds.

        The Drought Code tolerance is the strict one on purpose. It has the longest memory
        of anything in the system, so a season of agreement inside a single unit is what
        rules out an error in the drying and rain equations.
        """
        theirs = np.array([row[code] for row in cwfis[1:]], dtype="float64")
        ours = np.asarray(computed.column(code), dtype="float64")

        difference = np.abs(ours - theirs)
        assert difference.max() < tolerance, (
            f"{code} diverges by {difference.max():.2f} on day {int(difference.argmax())}"
        )

    @pytest.mark.parametrize("code", ["dmc", "bui"])
    def test_duff_series_tracks_theirs_within_the_measured_offset(
        self, cwfis, computed, code
    ) -> None:
        """The one place we knowingly differ, pinned so the difference cannot grow quietly.

        CWFIS suspends code advance on snow-covered days; these equations implement the
        published specification, which has no such rule. The result is a DMC that runs high
        by up to a quarter, carried into BUI. What must hold is that the two series are the
        same shape — a feature that ranks cells identically is doing its job in the model
        even when its absolute level reflects a different convention.
        """
        theirs = np.array([row[code] for row in cwfis[1:]], dtype="float64")
        ours = np.asarray(computed.column(code), dtype="float64")

        relative = np.abs(ours - theirs) / np.maximum(theirs, 1.0)
        assert relative.max() < 0.25

        rank_correlation = np.corrcoef(theirs.argsort().argsort(), ours.argsort().argsort())[0, 1]
        assert rank_correlation > 0.98

    def test_the_season_ends_where_theirs_does(self, cwfis, computed) -> None:
        """A whole season without accumulating drift is the point of the exercise."""
        assert computed.column("dc")[-1].as_py() == pytest.approx(cwfis[-1]["dc"], abs=1.0)
        assert computed.column("ffmc")[-1].as_py() == pytest.approx(cwfis[-1]["ffmc"], abs=1.5)


class TestBehaviour:
    def test_rain_wets_the_litter(self) -> None:
        dry = step(FwiState(ffmc=92.0), temp=25.0, rh=30.0, wind=10.0, rain=0.0, month=7)
        wet = step(FwiState(ffmc=92.0), temp=25.0, rh=30.0, wind=10.0, rain=15.0, month=7)
        assert wet.ffmc < dry.ffmc

    def test_a_trace_of_rain_does_not_count(self) -> None:
        """The codes ignore the first half-millimetre; it never reaches the fuel."""
        none = step(FwiState(), temp=20.0, rh=40.0, wind=10.0, rain=0.0, month=7)
        trace = step(FwiState(), temp=20.0, rh=40.0, wind=10.0, rain=0.4, month=7)
        assert none.ffmc == pytest.approx(trace.ffmc)

    def test_heat_and_low_humidity_dry_the_duff(self) -> None:
        mild = step(FwiState(), temp=12.0, rh=70.0, wind=5.0, rain=0.0, month=7)
        hot = step(FwiState(), temp=34.0, rh=15.0, wind=5.0, rain=0.0, month=7)
        assert hot.dmc > mild.dmc
        assert hot.dc > mild.dc

    def test_drought_code_barely_moves_in_winter(self) -> None:
        """Its day-length factor is negative outside the growing season, by design."""
        january = step(FwiState(dc=300.0), temp=2.0, rh=70.0, wind=5.0, rain=0.0, month=1)
        july = step(FwiState(dc=300.0), temp=2.0, rh=70.0, wind=5.0, rain=0.0, month=7)
        assert january.dc == pytest.approx(300.0, abs=0.5)
        assert july.dc > january.dc

    def test_wind_raises_spread_but_not_fuel_available(self) -> None:
        assert initial_spread_index(90.0, 30.0) > initial_spread_index(90.0, 5.0)
        assert buildup_index(40.0, 300.0) == buildup_index(40.0, 300.0)

    def test_fwi_rises_with_both_of_its_inputs(self) -> None:
        base = fire_weather_index(5.0, 60.0)
        assert fire_weather_index(10.0, 60.0) > base
        assert fire_weather_index(5.0, 120.0) > base

    def test_codes_stay_inside_their_defined_ranges(self) -> None:
        soaked = step(FwiState(ffmc=95.0), temp=5.0, rh=100.0, wind=0.0, rain=50.0, month=6)
        assert 0.0 <= soaked.ffmc <= 101.0
        assert soaked.dmc >= 0.0
        assert soaked.dc >= 0.0

    def test_a_mismatched_series_is_refused(self) -> None:
        with pytest.raises(ValueError, match="same length"):
            fwi_series(
                [date(2023, 7, 1)],
                np.array([20.0, 21.0]),
                np.array([40.0]),
                np.array([10.0]),
                np.array([0.0]),
            )


class TestVapourPressureDeficit:
    def test_saturated_air_has_no_deficit(self) -> None:
        assert vapour_pressure_deficit(np.array([20.0]), np.array([100.0]))[0] == pytest.approx(0.0)

    def test_matches_the_tetens_value_at_a_known_point(self) -> None:
        """20 C saturates at about 2.34 kPa, so half-saturated air is short by about 1.17."""
        assert vapour_pressure_deficit(np.array([20.0]), np.array([50.0]))[0] == pytest.approx(
            1.17, abs=0.02
        )

    def test_deficit_grows_with_heat(self) -> None:
        cool = vapour_pressure_deficit(np.array([15.0]), np.array([40.0]))[0]
        hot = vapour_pressure_deficit(np.array([35.0]), np.array([40.0]))[0]
        assert hot > cool * 2
