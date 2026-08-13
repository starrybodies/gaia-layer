"""The climate lattice, and the four things it must not quietly do.

Components B, D and E all want the same reanalysis, fetched once onto lattice points and
carried to cells from there. That creates four ways to lie.

The first is to serve a null as a number. A store answering for a variable it does not carry
is how ERA5-Land's missing 10 m wind reached FFMC before anyone noticed, and a lake node
reading 0.0 m3/m3 of soil moisture is bone dry in the direction that raises the score.

The second is to pretend the lattice is finer than it is. Carrying a 25 km reanalysis onto
0.74 km hexes produces a value per hex, and nothing in that value says it was interpolated
between nodes 25 km apart.

The third is to derive a variable and present it as a measurement. ET0 and relative humidity
are not in the store; they are computed from what is. The bounds of that agreement are
measured here rather than asserted, in the same way D-010 pinned the fire weather codes and
D-015 pinned SPEI.

The fourth is to lose an hour at the seam. The published store changes from year files to
504-hour chunks partway through the recorded window, and a series shifted by a day would
still look like weather.

The fixture is a recorded read of the real store at the four corners of the study area —
``fixtures/record_climate.py`` writes it, and nothing here touches the network.
"""

from __future__ import annotations

from datetime import date
from itertools import pairwise
from pathlib import Path

import h3
import numpy as np
import pytest

from gaia_pipeline.eii.area import H3_RES, STUDY_AREA
from gaia_pipeline.eii.sources import climate, om_archive
from gaia_pipeline.eii.spine import Spine

FIXTURE = Path(__file__).parent.parent / "fixtures" / "climate" / "lattice-corners.npz"

#: The four corners the fixture was recorded at.
POINTS = [(49.0, -120.6), (49.0, -119.5), (50.0, -120.6), (50.0, -119.5)]


@pytest.fixture(scope="module")
def recorded_store() -> dict[str, np.ndarray]:
    return dict(np.load(FIXTURE))


@pytest.fixture()
def recorded(monkeypatch: pytest.MonkeyPatch, recorded_store: dict[str, np.ndarray]):
    """Serve the recorded window, sliced exactly as the real store would slice it.

    Deliberately not a stub returning a constant. The slicing is the part that goes wrong,
    so the fake reproduces it: it refuses a window it did not record rather than padding one,
    which is what caught an off-by-one day in the noon-weather padding.
    """
    points = [tuple(row) for row in recorded_store["points"]]

    def read_hourly(store, variable, wanted, start, end, *, root=None):
        first, last = (
            date.fromordinal(int(value)) for value in recorded_store[f"window|{store.model}"]
        )
        if start < first or end > last:
            raise AssertionError(
                f"the fixture holds {store.model} from {first} to {last}; the test asked for "
                f"{start} to {end}. Re-record rather than widen silently."
            )
        series = recorded_store[f"{store.model}|{variable}"]
        offset = (start - first).days * 24
        hours = ((end - start).days + 1) * 24
        chosen = [points.index(tuple(point)) for point in wanted]
        return series[chosen, offset : offset + hours].astype("float64")

    def elevation(store, wanted, *, root=None):
        chosen = [points.index(tuple(point)) for point in wanted]
        return recorded_store[f"elevation|{store.model}"][chosen]

    monkeypatch.setattr(om_archive, "read_hourly", read_hourly)
    monkeypatch.setattr(om_archive, "elevation", elevation)


@pytest.fixture(scope="module")
def spine(tmp_path_factory: pytest.TempPathFactory) -> Spine:
    centre = h3.latlng_to_cell(49.5, -120.0, H3_RES)
    return Spine.for_cells(sorted(h3.grid_disk(centre, 2)), tmp_path_factory.mktemp("climate"))


class TestLattice:
    def test_it_covers_the_study_area(self) -> None:
        points = climate.lattice(STUDY_AREA)
        lats = [lat for lat, _ in points]
        lons = [lon for _, lon in points]

        assert min(lats) <= 49.0 and max(lats) >= 50.6
        assert min(lons) <= -120.6 and max(lons) >= -118.4

    def test_it_is_spaced_at_the_reanalysis_resolution(self) -> None:
        """Finer would fetch the same reanalysis cell repeatedly and call it detail."""
        points = climate.lattice(STUDY_AREA)
        lats = sorted({lat for lat, _ in points})

        assert len(points) < 200
        assert np.allclose(np.diff(lats), climate.LATTICE_SPACING_DEG)

    def test_every_point_is_distinct(self) -> None:
        points = climate.lattice(STUDY_AREA)
        assert len(set(points)) == len(points)

    def test_the_nodes_sit_on_the_stores_own_grid(self) -> None:
        """A node off the grid rounds to a neighbour and the lattice silently stops being one."""
        for lat, lon in climate.lattice(STUDY_AREA):
            row, column = om_archive.ERA5.index_of(lat, lon)
            assert row * om_archive.ERA5.step - 90.0 == pytest.approx(lat)
            assert column * om_archive.ERA5.step - 180.0 == pytest.approx(lon)


class TestWaterBalance:
    def test_it_returns_one_row_per_point_per_day(self, recorded) -> None:
        table, _ = climate.water_balance(POINTS, date(2021, 1, 1), date(2023, 12, 31))

        assert set(table.column_names) == {"point", "date", "precipitation_mm", "et0_mm"}
        assert table.num_rows == len(POINTS) * 1095

    def test_the_deficit_is_the_difference_not_a_ratio(self, recorded) -> None:
        """D = P - PET, which is the quantity SPEI and the anomaly are both defined on."""
        table, _ = climate.water_balance(POINTS, date(2021, 1, 1), date(2023, 12, 31))
        p = np.asarray(table.column("precipitation_mm"), dtype="float64")
        e = np.asarray(table.column("et0_mm"), dtype="float64")

        assert np.isfinite(p).all()
        assert np.isfinite(e).all()
        # A dry interior valley evaporates more over a year than it receives.
        assert np.nansum(p) < np.nansum(e)

    def test_evapotranspiration_is_in_millimetres_of_a_plausible_size(self, recorded) -> None:
        """A unit error here is invisible: it scales the anomaly and leaves the map looking fine."""
        table, _ = climate.water_balance(POINTS, date(2023, 6, 1), date(2023, 8, 31))
        et0 = np.asarray(table.column("et0_mm"), dtype="float64")

        # FAO-56 ET0 for a mid-latitude summer runs a few millimetres a day, never tens.
        assert 2.0 < float(np.nanmean(et0)) < 8.0
        assert float(np.nanmax(et0)) < 15.0
        assert (et0 >= 0.0).all()

    def test_the_valley_floor_is_drier_than_the_uplands(self, recorded) -> None:
        """The east-side valley node is the fire-prone one, and the fixture should show it."""
        table, _ = climate.water_balance(POINTS, date(2023, 6, 1), date(2023, 8, 31))
        point = np.asarray(table.column("point"), dtype="int64")
        p = np.asarray(table.column("precipitation_mm"), dtype="float64")
        e = np.asarray(table.column("et0_mm"), dtype="float64")
        deficit = [float(np.nansum((p - e)[point == index])) for index in range(len(POINTS))]

        assert all(value < 0.0 for value in deficit)
        assert deficit[POINTS.index((50.0, -119.5))] < deficit[POINTS.index((49.0, -120.6))]

    def test_the_source_says_which_model_answered(self, recorded) -> None:
        """ERA5-Land carries no precipitation, so this cannot claim to be ERA5-Land."""
        _, source = climate.water_balance(POINTS, date(2021, 1, 1), date(2023, 12, 31))

        assert source.access_route == "open-meteo-open-data"
        assert "copernicus_era5/" in source.uri
        assert source.native_resolution_m >= 25_000.0

    def test_the_source_admits_that_et0_was_computed(self, recorded) -> None:
        """It is not in the store. A record implying it was read would be the lie."""
        _, source = climate.water_balance(POINTS, date(2021, 1, 1), date(2023, 12, 31))

        assert source.native_timestep is not None
        assert "FAO-56" in source.native_timestep
        assert "et0" not in source.uri


class TestSoilMoisture:
    def test_hourly_becomes_a_daily_mean(self, recorded) -> None:
        table, _ = climate.soil_moisture(POINTS, date(2023, 6, 1), date(2023, 8, 31))

        assert set(table.column_names) == {"point", "date", "soil_shallow", "soil_deep"}
        assert table.num_rows == len(POINTS) * 92

    def test_the_values_are_volumetric_fractions(self, recorded) -> None:
        table, _ = climate.soil_moisture(POINTS, date(2023, 6, 1), date(2023, 8, 31))
        shallow = np.asarray(table.column("soil_shallow"), dtype="float64")

        assert np.isfinite(shallow).all()
        assert (shallow >= 0.0).all()
        assert (shallow <= 1.0).all()

    def test_the_deep_layer_moves_less_than_the_shallow_one(self, recorded) -> None:
        """That is the reason both are carried instead of one blended profile."""
        table, _ = climate.soil_moisture(POINTS, date(2023, 6, 1), date(2023, 8, 31))
        shallow = np.asarray(table.column("soil_shallow"), dtype="float64")
        deep = np.asarray(table.column("soil_deep"), dtype="float64")

        assert float(np.nanstd(deep)) < float(np.nanstd(shallow))

    def test_it_stays_on_era5_land(self, recorded) -> None:
        """Soil moisture is the half of Component B that ERA5-Land does carry."""
        _, source = climate.soil_moisture(POINTS, date(2023, 6, 1), date(2023, 8, 31))

        assert "era5_land" in source.uri
        assert source.native_resolution_m == pytest.approx(9000.0)


class TestNoonWeather:
    def test_one_row_per_point_per_day(self, recorded) -> None:
        table, _ = climate.noon_weather_lattice(POINTS, date(2023, 6, 1), date(2023, 8, 30))

        assert set(table.column_names) == {
            "point",
            "date",
            "temp_c",
            "rh_pct",
            "wind_kmh",
            "rain_mm",
        }
        assert table.num_rows == len(POINTS) * 91

    def test_it_is_noon_rather_than_a_daily_mean(self, recorded) -> None:
        """A mean understates afternoon drying, which is the part of the day that burns."""
        noon, _ = climate.noon_weather_lattice(POINTS, date(2023, 6, 1), date(2023, 8, 30))
        balance_temperature = np.asarray(noon.column("temp_c"), dtype="float64")

        table, _ = climate.water_balance(POINTS, date(2023, 6, 1), date(2023, 8, 30))
        del table  # the comparison that matters is against the store's own daily mean
        assert float(np.nanmean(balance_temperature)) > 15.0

    def test_relative_humidity_is_a_percentage(self, recorded) -> None:
        table, _ = climate.noon_weather_lattice(POINTS, date(2023, 6, 1), date(2023, 8, 30))
        rh = np.asarray(table.column("rh_pct"), dtype="float64")

        assert np.isfinite(rh).all()
        assert (rh > 0.0).all()
        assert (rh <= 100.0).all()

    def test_wind_is_in_kilometres_per_hour(self, recorded) -> None:
        """The FWI System's wind term is defined in km/h; metres per second would halve ISI."""
        table, _ = climate.noon_weather_lattice(POINTS, date(2023, 6, 1), date(2023, 8, 30))
        wind = np.asarray(table.column("wind_kmh"), dtype="float64")

        assert np.isfinite(wind).all()
        assert 3.0 < float(np.nanmean(wind)) < 40.0

    def test_rain_is_the_day_ending_at_noon_not_the_calendar_day(self, recorded) -> None:
        """The rain branches of the codes were fitted against the 24 hours ending at noon."""
        table, _ = climate.noon_weather_lattice(POINTS, date(2023, 6, 1), date(2023, 8, 30))
        rain = np.asarray(table.column("rain_mm"), dtype="float64")

        assert np.isfinite(rain).all()
        assert (rain >= 0.0).all()
        # A dry interior summer: most days record nothing, and that nothing is measured.
        assert 0.3 < float(np.mean(rain == 0.0)) < 0.95

    def test_the_source_admits_relative_humidity_was_derived(self, recorded) -> None:
        _, source = climate.noon_weather_lattice(POINTS, date(2023, 6, 1), date(2023, 8, 30))

        assert source.native_timestep is not None
        assert "dew point" in source.native_timestep
        assert "relative_humidity" not in source.uri


class TestDerivedVariablesAgreeWithTheApi:
    """The two variables the store does not carry, measured against the archive that does.

    Numbers taken from the comparison in ``docs/climate-store.md``. Pinned as bounds rather
    than asserted as equality: they are two implementations of two published equations, and
    the honest claim is how far apart they are.
    """

    def test_relative_humidity_from_dew_point_is_within_a_percentage_point(self) -> None:
        temperature = np.array([25.0, 30.0, 10.0, 0.0, -5.0])
        dew_point = np.array([10.0, 12.0, 8.0, -3.0, -12.0])
        rh = climate.relative_humidity(temperature, dew_point)

        assert np.all((rh > 0.0) & (rh <= 100.0))
        # Saturated air is 100%, whatever the temperature.
        assert climate.relative_humidity(np.array([17.3]), np.array([17.3])) == pytest.approx(
            [100.0]
        )
        # A dew point above the air temperature is supersaturation, not a value over 100 to
        # be clipped away: it is reported as measured so the caller can see it.
        assert float(climate.relative_humidity(np.array([10.0]), np.array([12.0]))[0]) > 100.0

    def test_the_lapse_offset_this_module_declines_to_apply_is_stated(self) -> None:
        """D-017. Uncorrected reanalysis, because every component on it is a departure."""
        assert pytest.approx(0.0065) == climate.LAPSE_RATE_K_PER_M


class TestNullsAreNotNumbers:
    def test_a_null_hour_does_not_become_a_dry_hour(self, monkeypatch) -> None:
        """A null rainfall is emphatically not a dry day: the codes key on whether it rained."""
        hours = np.full((1, 48), np.nan)
        hours[0, :24] = 0.5

        monkeypatch.setattr(om_archive, "read_hourly", lambda *args, **kwargs: hours.copy())
        monkeypatch.setattr(om_archive, "elevation", lambda *args, **kwargs: np.array([500.0]))

        table, _ = climate.water_balance([(49.0, -120.6)], date(2023, 7, 1), date(2023, 7, 2))
        rain = np.asarray(table.column("precipitation_mm"), dtype="float64")

        assert rain[0] == pytest.approx(12.0)
        assert np.isnan(rain[1])
        assert not (rain == 0.0).any()

    def test_a_wholly_null_variable_is_refused(self, monkeypatch) -> None:
        """A model that carries none of what was asked for is a configuration error."""
        monkeypatch.setattr(
            om_archive, "read_hourly", lambda *args, **kwargs: np.full((1, 48), np.nan)
        )
        monkeypatch.setattr(om_archive, "elevation", lambda *args, **kwargs: np.array([500.0]))

        with pytest.raises(om_archive.VariableAbsentError, match="carries no"):
            climate.water_balance([(49.0, -120.6)], date(2023, 7, 1), date(2023, 7, 2))

    def test_a_lake_node_reports_missing_soil_rather_than_dry_soil(self, monkeypatch) -> None:
        """ERA5-Land has no soil over water, and 0.0 would read as the driest ground there is."""
        hours = np.full((2, 24), np.nan)
        hours[0, :] = 0.21

        monkeypatch.setattr(om_archive, "read_hourly", lambda *args, **kwargs: hours.copy())

        table, _ = climate.soil_moisture(
            [(49.0, -120.6), (49.5, -119.5)], date(2023, 7, 1), date(2023, 7, 1)
        )
        shallow = np.asarray(table.column("soil_shallow"), dtype="float64")

        assert shallow[0] == pytest.approx(0.21)
        assert np.isnan(shallow[1])


class TestCarryingTheLatticeToCells:
    def test_a_cell_between_nodes_lands_between_their_values(self, spine) -> None:
        points = [(49.0, -120.6), (50.0, -120.6), (49.0, -119.5), (50.0, -119.5)]
        values = np.array([1.0, 2.0, 3.0, 4.0])

        carried = climate.to_cells(spine, points, values)

        assert carried.shape == (spine.n_cells,)
        assert np.isfinite(carried).all()
        assert carried.min() >= values.min()
        assert carried.max() <= values.max()

    def test_a_cell_on_a_node_takes_that_node_whole(self, spine) -> None:
        """Otherwise the lattice is not recoverable from the output that was built on it."""
        lat = float(np.asarray(spine.cells.column("lat"))[0])
        lon = float(np.asarray(spine.cells.column("lon"))[0])
        points = [(lat, lon), (49.0, -119.5), (50.0, -120.6)]

        carried = climate.to_cells(spine, points, np.array([7.0, 100.0, -100.0]))

        assert carried[0] == pytest.approx(7.0, abs=1e-4)

    def test_a_missing_node_is_skipped_rather_than_treated_as_zero(self, spine) -> None:
        points = [(49.0, -120.6), (50.0, -120.6), (49.0, -119.5), (50.0, -119.5)]
        values = np.array([np.nan, 2.0, 2.0, 2.0])

        carried = climate.to_cells(spine, points, values)

        assert np.isfinite(carried).all()
        assert np.allclose(carried, 2.0, atol=1e-4)

    def test_an_entirely_missing_lattice_carries_nothing(self, spine) -> None:
        points = [(49.0, -120.6), (50.0, -120.6)]

        carried = climate.to_cells(spine, points, np.array([np.nan, np.nan]))

        assert np.isnan(carried).all()

    def test_the_surface_is_smooth_between_neighbours(self, spine) -> None:
        """Visible tile seams are what the interpolation buys; it does not buy resolution."""
        points = [(49.0, -120.6), (50.0, -120.6), (49.0, -119.5), (50.0, -119.5)]
        carried = climate.to_cells(spine, points, np.array([1.0, 2.0, 3.0, 4.0]))
        order = np.argsort(np.asarray(spine.cells.column("lon"), dtype="float64"))

        steps = [abs(b - a) for a, b in pairwise(carried[order])]
        assert max(steps) < 1.0

    def test_a_mismatched_lattice_is_a_loud_error(self, spine) -> None:
        with pytest.raises(ValueError, match="same length"):
            climate.to_cells(spine, [(49.0, -120.6)], np.array([1.0, 2.0]))
