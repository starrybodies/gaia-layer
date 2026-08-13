"""Reading ERA5 out of Open-Meteo's published store, and the four ways that goes wrong.

The store is not an API. It is a directory of whole-globe arrays with an implicit time
origin, an implicit latitude direction and two file layouts that overlap at the changeover.
Every one of those is a chance to return a plausible series that is wrong, and none of them
raises when it happens. So the tests here build a small store on disk whose values encode
their own coordinates, and then check that what comes back is what was asked for:

* a value read at a coordinate is the value that was written there, not its neighbour's;
* latitude runs south to north, because reading it upside down returns a real series from
  the wrong hemisphere and nothing in the numbers says so;
* a window is assembled from year files where they exist and 504-hour chunks where they do
  not, with no hour taken from two sources and no hour left behind;
* a variable the store does not carry raises, rather than coming back as zeroes.

Nothing here touches the network. The store the tests read is written by the same library
that reads the real one, so the read path under test is the whole read path.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest
from omfiles import OmFileWriter

from gaia_pipeline.eii.sources import om_archive
from gaia_pipeline.eii.sources.om_archive import ERA5, ERA5_LAND, Store, VariableAbsentError

#: A grid small enough to write in a test and shaped like the real one: latitude from -90
#: north, longitude from -180 east, a quarter degree apart.
TINY = Store(
    model="tiny_era5",
    ny=8,
    nx=12,
    step=0.25,
    dataset="Tiny",
    native_resolution_m=25_000.0,
    citation="none",
    licence="none",
)


def _origin() -> int:
    """The hour the test store counts time from, so a value can state its own hour plainly."""
    return om_archive._hour_index(date(2020, 1, 1))


def _write(path: Path, values: np.ndarray) -> None:
    """One array on disk, chunked as the real store is: a row, six columns, a time block.

    Written losslessly in float64. The real store uses ``pfor_delta_2d``, which quantises to
    the scale factor — fine for a temperature in tenths, useless for a ramp whose whole job
    is to carry a distinct value in every cell of a three-dimensional array.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = OmFileWriter.at_path(str(path))
    variable = writer.write_array(
        values.astype("float64"),
        chunks=[1, 6, min(values.shape[2], 1098)],
        compression="fpx_xor_2d",
        name="data",
    )
    writer.close(variable)


def _ramp(store: Store, first_hour: int, hours: int) -> np.ndarray:
    """A value naming its own cell and hour: ``row * 1e7 + column * 1e5 + hours_since_origin``.

    A constant would pass every indexing bug there is. This one fails all of them, and the
    failure says which axis was wrong.
    """
    rows = np.arange(store.ny)[:, None, None]
    columns = np.arange(store.nx)[None, :, None]
    steps = (first_hour - _origin() + np.arange(hours))[None, None, :]
    return (rows * 1e7 + columns * 1e5 + steps).astype("float64")


def _expect(row: int, column: int, moment: date, hour: int = 0) -> float:
    return row * 1e7 + column * 1e5 + (om_archive._hour_index(moment) - _origin() + hour)


def _point(row: int, column: int) -> tuple[float, float]:
    return (-90.0 + row * TINY.step, -180.0 + column * TINY.step)


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    """A store with 2020 as a year file and 2021 as chunks, which is the real changeover."""
    root = tmp_path / "store"
    year_start = om_archive._hour_index(date(2020, 1, 1))
    _write(root / TINY.model / "temp" / "year_2020.om", _ramp(TINY, year_start, 366 * 24))

    first = om_archive._hour_index(date(2021, 1, 1)) // om_archive.CHUNK_HOURS
    last = om_archive._hour_index(date(2021, 12, 31)) // om_archive.CHUNK_HOURS
    for chunk in range(first, last + 1):
        base = chunk * om_archive.CHUNK_HOURS
        _write(
            root / TINY.model / "temp" / f"chunk_{chunk}.om",
            _ramp(TINY, base, om_archive.CHUNK_HOURS),
        )

    elevations = (np.arange(TINY.ny)[:, None] * 10.0 + np.arange(TINY.nx)[None, :]).astype(
        "float32"
    )
    static = root / TINY.model / "static"
    static.mkdir(parents=True, exist_ok=True)
    writer = OmFileWriter.at_path(str(static / "HSURF.om"))
    writer.close(writer.write_array(elevations, chunks=[4, 6], name="data"))
    return root


class TestTheGrid:
    def test_latitude_runs_south_to_north(self) -> None:
        """Row zero is the south pole. Reading it the other way up returns Patagonia."""
        assert ERA5.index_of(-90.0, -180.0) == (0, 0)
        assert ERA5.index_of(90.0, -180.0) == (720, 0)
        assert ERA5.index_of(50.0, -119.5)[0] > ERA5.index_of(49.0, -119.5)[0]

    def test_the_two_stores_are_the_resolutions_they_claim(self) -> None:
        assert (ERA5.ny - 1) * ERA5.step == 180.0
        assert ERA5.nx * ERA5.step == 360.0
        assert (ERA5_LAND.ny - 1) * ERA5_LAND.step == 180.0
        assert ERA5_LAND.nx * ERA5_LAND.step == 360.0

    def test_the_antimeridian_does_not_fall_off_the_end(self) -> None:
        assert ERA5.index_of(0.0, 180.0) == ERA5.index_of(0.0, -180.0)

    def test_a_latitude_off_the_grid_is_refused(self) -> None:
        with pytest.raises(ValueError, match="off the"):
            ERA5.index_of(95.0, 0.0)


class TestTheReadPlan:
    def _paths(self, start: date, end: date, exists) -> list[str]:
        return [
            segment.path.rsplit("/", 1)[-1]
            for segment in om_archive._plan(TINY, "temp", start, end, exists)
        ]

    def test_a_year_with_a_year_file_is_served_by_it_alone(self, store: Path) -> None:
        transport = om_archive._Files(str(store))
        assert self._paths(date(2020, 3, 1), date(2020, 3, 31), transport.exists) == [
            "year_2020.om"
        ]

    def test_a_year_without_one_falls_back_to_chunks(self, store: Path) -> None:
        transport = om_archive._Files(str(store))
        paths = self._paths(date(2021, 3, 1), date(2021, 3, 31), transport.exists)
        assert paths and all(name.startswith("chunk_") for name in paths)

    def test_the_changeover_is_covered_once_and_only_once(self, store: Path) -> None:
        """Year files and chunk files overlap. An hour assembled from both is a duplicate."""
        transport = om_archive._Files(str(store))
        segments = om_archive._plan(
            TINY, "temp", date(2020, 12, 30), date(2021, 1, 2), transport.exists
        )
        covered = np.zeros(4 * 24, dtype="int64")
        for segment in segments:
            covered[segment.into : segment.into + (segment.last - segment.first)] += 1
        assert (covered == 1).all()

    def test_a_window_the_store_cannot_reach_is_refused(self, store: Path) -> None:
        transport = om_archive._Files(str(store))
        with pytest.raises(VariableAbsentError, match="carries 0 of"):
            om_archive._plan(TINY, "temp", date(1975, 1, 1), date(1975, 1, 2), transport.exists)


class TestReading:
    def test_a_point_reads_its_own_cell_not_a_neighbour(self, store: Path) -> None:
        values = om_archive.read_hourly(
            TINY, "temp", [_point(5, 7)], date(2020, 6, 1), date(2020, 6, 1), root=str(store)
        )
        assert values.shape == (1, 24)
        assert values[0, 0] == pytest.approx(_expect(5, 7, date(2020, 6, 1)))

    def test_several_points_come_back_in_the_order_asked(self, store: Path) -> None:
        """The bounding-box read has to be unpacked back onto the caller's own numbering."""
        wanted = [(6, 2), (1, 9), (3, 5)]
        values = om_archive.read_hourly(
            TINY,
            "temp",
            [_point(row, column) for row, column in wanted],
            date(2020, 6, 1),
            date(2020, 6, 1),
            root=str(store),
        )
        assert [float(row[0]) for row in values] == [
            _expect(row, column, date(2020, 6, 1)) for row, column in wanted
        ]

    def test_the_hour_axis_advances_in_step_with_the_clock(self, store: Path) -> None:
        values = om_archive.read_hourly(
            TINY, "temp", [_point(2, 2)], date(2020, 6, 1), date(2020, 6, 3), root=str(store)
        )
        assert values.shape == (1, 72)
        assert values[0, 0] == pytest.approx(_expect(2, 2, date(2020, 6, 1)))
        assert np.allclose(np.diff(values[0]), 1.0)

    def test_a_window_spanning_both_layouts_is_continuous(self, store: Path) -> None:
        """Two file formats, one series: a step at the seam would be a silent time shift."""
        values = om_archive.read_hourly(
            TINY, "temp", [_point(2, 2)], date(2020, 12, 30), date(2021, 1, 2), root=str(store)
        )
        assert values.shape == (1, 96)
        assert np.isfinite(values).all()
        assert values[0, 0] == pytest.approx(_expect(2, 2, date(2020, 12, 30)))
        assert np.allclose(np.diff(values[0]), 1.0)

    def test_the_timestamps_line_up_with_the_values(self, store: Path) -> None:
        stamps = om_archive.hours_utc(date(2020, 6, 1), date(2020, 6, 2))
        assert stamps.size == 48
        assert stamps[0] == datetime(2020, 6, 1, tzinfo=UTC)
        assert stamps[-1] == datetime(2020, 6, 2, 23, tzinfo=UTC)

    def test_a_variable_the_store_does_not_carry_raises(self, store: Path) -> None:
        """The D-014 failure: a source answering for something it does not have."""
        with pytest.raises(VariableAbsentError):
            om_archive.read_hourly(
                TINY,
                "wind_u_component_10m",
                [(-90.0, -180.0)],
                date(2020, 6, 1),
                date(2020, 6, 1),
                root=str(store),
            )

    def test_no_points_is_a_caller_error_rather_than_an_empty_answer(self, store: Path) -> None:
        with pytest.raises(ValueError):
            om_archive.read_hourly(
                TINY, "temp", [], date(2020, 6, 1), date(2020, 6, 1), root=str(store)
            )


class TestElevation:
    def test_it_reads_the_cell_the_point_falls_in(self, store: Path) -> None:
        point = (-90.0 + 4 * TINY.step, -180.0 + 3 * TINY.step)
        assert om_archive.elevation(TINY, [point], root=str(store)) == pytest.approx([43.0])

    def test_a_store_without_one_says_so(self, tmp_path: Path) -> None:
        with pytest.raises(VariableAbsentError, match="static elevation"):
            om_archive.elevation(TINY, [(0.0, 0.0)], root=str(tmp_path))


class TestTheSourceRecord:
    def test_it_points_at_the_bytes_rather_than_an_api(self) -> None:
        """A reader checking a number should be able to fetch the same file."""
        record = om_archive.source_record(ERA5, ["precipitation", "temperature_2m"])

        assert record.access_route == "open-meteo-open-data"
        assert record.uri.startswith(om_archive.ARCHIVE_ROOT)
        assert "copernicus_era5" in record.uri
        assert "precipitation" in record.uri
        assert record.native_resolution_m == pytest.approx(25_000.0)

    def test_era5_land_reports_its_own_finer_grid(self) -> None:
        record = om_archive.source_record(ERA5_LAND, ["soil_moisture_7_to_28cm"])
        assert record.native_resolution_m == pytest.approx(9_000.0)
        assert "era5_land" in record.uri
