"""ERA5 read straight out of Open-Meteo's published store, by byte range, with no quota.

Components B, D and E all want the same forty years of reanalysis at eighty-eight lattice
nodes. Asking Open-Meteo's archive API for it does not work and cannot be made to work: the
free tier meters by *call weight*, roughly ``ceil(days / 14) * ceil(variables / 10) *
locations``, so the water balance alone is about ninety thousand weighted calls whatever the
batching. Three separate runs died against that limit before it was accepted as a property
of the acquisition strategy rather than bad luck. This module changes the strategy — it is
the fix recorded in D-016, and it deletes the retry, backoff and quota machinery rather than
tuning it.

**The same data, not a substitute for it.** Open-Meteo publishes the archive its own API
serves from, as ``.om`` files on an anonymous S3 bucket. Byte-for-byte comparison against
the API over 2023-08-10 to 2023-08-17 at 50.00 N, 119.50 W: precipitation, shortwave
radiation and both soil moisture layers agree exactly; 10 m wind agrees to 0.05 km/h, which
is the API's rounding. So this is not a different product with similar semantics, it is the
product.

**What differs, and why it is left alone.** Temperature and dew point come back exactly
3.0 K colder here than from the API, because the API applies a lapse-rate correction from
the ERA5 grid cell's elevation to the requested coordinate's real elevation — 959 m against
499 m at that node, times the 0.0065 K/m standard lapse rate. This module returns the
uncorrected reanalysis. That is deliberate. Components B, D and E are every one of them a
*departure* from the same node's own record, and a constant offset applied to every year of
that record cancels in the departure. Correcting it would change no reported number and
would introduce a second elevation model into a chain that already has one. Recorded as
D-017.

**Two variables the store does not carry.** Reference evapotranspiration and relative
humidity are computed by the API rather than stored, so they are computed here too, from
the stored variables the published equations take. Both are checked against the API in
``docs/climate-store.md`` with the disagreement quantified rather than assumed away.

**The file layout, because the read plan depends on it.** Each variable is a directory of
whole-globe files: ``year_1940.om`` through ``year_2021.om``, then ``chunk_N.om`` covering
504 hours each from 1970-01-01 UTC. Arrays are ``(lat, lon, hour)`` with latitude running
south to north from -90, and are chunked ``(1, 6, 1098)`` — one row of latitude, six of
longitude, an eighth of a year. A whole-lattice read of one variable for one year therefore
touches about two hundred chunks and moves under a megabyte, which is why the caller should
ask for the whole lattice at once rather than a few nodes at a time.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
from omfiles import OmFileReader
from tenacity import retry, stop_after_attempt, wait_exponential

from ..archive import SourceRecord

log = logging.getLogger(__name__)

#: Open-Meteo's open data bucket. Anonymous, no account, no token, no metering — the whole
#: reason this module exists. Documented at https://github.com/open-meteo/open-data.
ARCHIVE_ROOT = "https://openmeteo.s3.amazonaws.com/data"

#: Hours in one ``chunk_N.om``. Read from the store's own ``static/meta.json``, pinned here
#: because the read plan is arithmetic on it and a silent change should fail loudly rather
#: than return a series shifted in time.
CHUNK_HOURS = 504

#: The instant ``chunk_N`` indices are counted from.
CHUNK_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

#: Years fetched at once. The store is not metered, so this is only about the remote
#: latency of about two hundred small range reads per variable-year.
FETCH_WORKERS = 8


@dataclass(frozen=True)
class Store:
    """One published model: its grid, and what a source record has to say about it.

    Two of them are used. ERA5 at a quarter degree carries everything Components B, D and E
    need except soil moisture at a useful depth resolution; ERA5-Land at a tenth of a degree
    carries the soil but is a land-surface model and carries no precipitation, no reference
    evapotranspiration and no 10 m wind — the gap recorded as D-014 and, before it, the one
    that silently took FFMC, ISI and FWI down.
    """

    model: str
    ny: int
    nx: int
    step: float
    dataset: str
    native_resolution_m: float
    citation: str
    licence: str

    def index_of(self, lat: float, lon: float) -> tuple[int, int]:
        """The grid cell a coordinate falls in, as ``(row, column)``.

        Latitude runs south to north from -90, which is the opposite of ERA5's native
        ordering and was confirmed by reading the same variable at mirrored latitudes: the
        northern node runs cold in January and the southern one warm.
        """
        row = round((lat + 90.0) / self.step)
        column = round((lon + 180.0) / self.step) % self.nx
        if not 0 <= row < self.ny:
            raise ValueError(f"latitude {lat} is off the {self.model} grid")
        return row, column


ERA5 = Store(
    model="copernicus_era5",
    ny=721,
    nx=1440,
    step=0.25,
    dataset="ERA5",
    native_resolution_m=25_000.0,
    citation=(
        "Hersbach, H. et al. (2020). The ERA5 global reanalysis. Quarterly Journal of the "
        "Royal Meteorological Society 146:1999-2049. doi:10.1002/qj.3803"
    ),
    licence="Open-Meteo open data: CC-BY-4.0; ERA5: Copernicus licence",
)

ERA5_LAND = Store(
    model="copernicus_era5_land",
    ny=1801,
    nx=3600,
    step=0.1,
    dataset="ERA5-Land",
    native_resolution_m=9_000.0,
    citation=(
        "Muñoz-Sabater, J. et al. (2021). ERA5-Land: a state-of-the-art global reanalysis "
        "dataset for land applications. Earth Syst. Sci. Data 13:4349-4383. "
        "doi:10.5194/essd-13-4349-2021"
    ),
    licence="Open-Meteo open data: CC-BY-4.0; ERA5-Land: Copernicus licence",
)


class VariableAbsentError(RuntimeError):
    """The store has no file for this variable over this window.

    Its own error rather than a bare 404 because the failure it guards against is the one
    D-014 records: a source that answers for a variable it does not carry, with something
    that parses. Nothing here returns a value it did not read.
    """


# ------------------------------------------------------------------ the read plan


@dataclass(frozen=True)
class _Segment:
    """One file, the hours wanted from it, and where they land in the answer."""

    path: str
    first: int
    last: int
    into: int


def _hour_index(moment: date) -> int:
    stamp = datetime(moment.year, moment.month, moment.day, tzinfo=UTC)
    return int((stamp - CHUNK_EPOCH).total_seconds() // 3600)


def _year_hours(year: int) -> int:
    return int((date(year + 1, 1, 1) - date(year, 1, 1)).days) * 24


def _plan(store: Store, variable: str, start: date, end: date, exists: object) -> list[_Segment]:
    """Which files answer ``[start, end]`` inclusive, in order, without gaps.

    A year is served whole by its year file when one exists and by 504-hour chunks when it
    does not; the two overlap around the changeover and the year file wins, so no hour is
    ever assembled from two sources. Whether a year file exists is a property of the store
    rather than of the calendar — the archive is rewritten forward over time — so it is
    asked rather than assumed.
    """
    directory = f"{store.model}/{variable}"
    want_from, want_to = _hour_index(start), _hour_index(end) + 24
    segments: list[_Segment] = []
    covered = 0

    for year in range(start.year, end.year + 1):
        year_from = _hour_index(date(year, 1, 1))
        year_to = year_from + _year_hours(year)
        first, last = max(want_from, year_from), min(want_to, year_to)
        if first >= last:
            continue

        path = f"{directory}/year_{year}.om"
        if exists(path):  # type: ignore[operator]
            segments.append(_Segment(path, first - year_from, last - year_from, first - want_from))
            covered += last - first
            continue

        for chunk in range(first // CHUNK_HOURS, (last - 1) // CHUNK_HOURS + 1):
            base = chunk * CHUNK_HOURS
            path = f"{directory}/chunk_{chunk}.om"
            if not exists(path):  # type: ignore[operator]
                continue
            lo, hi = max(first, base), min(last, base + CHUNK_HOURS)
            segments.append(_Segment(path, lo - base, hi - base, lo - want_from))
            covered += hi - lo

    # Partial coverage is refused rather than returned short. A series silently missing its
    # first decade still fits every downstream array and still produces a departure — one
    # measured against a reference distribution that is not the one the method claims.
    if covered < want_to - want_from:
        raise VariableAbsentError(
            f"{store.model} carries {covered} of the {want_to - want_from} hours of "
            f"{variable} between {start} and {end}: no year or chunk file covers the rest. "
            "A component computed from a variable that is partly absent is not a weak "
            "measurement, it is a measurement of a different window."
        )
    return segments


# ------------------------------------------------------------------ the transport


class _Ranges:
    """The two methods ``OmFileReader.from_fsspec`` actually calls, over plain HTTP.

    fsspec's own HTTP filesystem is not used because its ``cat_file`` signature does not
    match what the reader passes. Two methods against ``httpx`` — already a dependency —
    is less machinery than adapting a filesystem abstraction that is not otherwise wanted.
    """

    def __init__(self, root: str) -> None:
        self.root = root.rstrip("/")
        self._client = httpx.Client(timeout=120.0, follow_redirects=True)
        self._lock = threading.Lock()
        self._known: dict[str, bool] = {}

    def exists(self, path: str) -> bool:
        with self._lock:
            if path in self._known:
                return self._known[path]
        answer = self._client.head(f"{self.root}/{path}").status_code == 200
        with self._lock:
            self._known[path] = answer
        return answer

    def size(self, path: str) -> int:
        response = self._client.head(path)
        response.raise_for_status()
        return int(response.headers["content-length"])

    def cat_file(self, path: str, start: int | None = None, end: int | None = None) -> bytes:
        headers = {}
        if start is not None:
            headers["Range"] = f"bytes={start}-{'' if end is None else end - 1}"
        response = self._client.get(path, headers=headers)
        response.raise_for_status()
        return response.content

    def reader(self, path: str) -> OmFileReader:
        return OmFileReader.from_fsspec(self, f"{self.root}/{path}")


class _Files:
    """The same two methods against a directory, which is what the tests read from."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def exists(self, path: str) -> bool:
        return (self.root / path).exists()

    def reader(self, path: str) -> OmFileReader:
        return OmFileReader.from_path(str(self.root / path))


def _transport(root: str | None) -> _Ranges | _Files:
    root = root or ARCHIVE_ROOT
    return _Files(root) if "://" not in root else _Ranges(root)


# ------------------------------------------------------------------ reading


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, max=20), reraise=True)
def _read_segment(
    transport: _Ranges | _Files, segment: _Segment, rows: slice, columns: slice
) -> np.ndarray:
    """One file's contribution, as ``(row, column, hour)``.

    Retried because this is thousands of small range requests against a public bucket over
    a domestic connection, and one refused socket should not cost a forty-year fetch.
    """
    reader = transport.reader(segment.path)
    try:
        return np.asarray(
            reader.read_array((rows, columns, slice(segment.first, segment.last))), dtype="float64"
        )
    finally:
        reader.close()


def read_hourly(
    store: Store,
    variable: str,
    points: list[tuple[float, float]],
    start: date,
    end: date,
    *,
    root: str | None = None,
) -> np.ndarray:
    """Hourly values at each point over ``[start, end]`` inclusive, as ``(point, hour)``.

    Points are read as the bounding box that contains them rather than one at a time,
    because the store's chunks are six longitudes wide: neighbouring lattice nodes share
    bytes, and asking for them separately downloads the same chunk repeatedly. The whole
    eighty-eight node lattice is one box eight rows by eleven columns, which is about two
    hundred range reads per variable-year.

    Nulls in the store arrive as NaN and stay NaN. ERA5-Land has no soil over water, and a
    lake node returning 0.0 m3/m3 would read as bone dry rather than as unmeasured.
    """
    if not points:
        raise ValueError("read_hourly needs at least one point")

    transport = _transport(root)
    indices = [store.index_of(lat, lon) for lat, lon in points]
    row_from, row_to = min(row for row, _ in indices), max(row for row, _ in indices) + 1
    col_from, col_to = min(col for _, col in indices), max(col for _, col in indices) + 1
    rows, columns = slice(row_from, row_to), slice(col_from, col_to)

    segments = _plan(store, variable, start, end, transport.exists)
    hours = (_hour_index(end) + 24) - _hour_index(start)
    box = np.full((row_to - row_from, col_to - col_from, hours), np.nan)

    log.info(
        "%s/%s: %d point(s) over %d hours from %d file(s)",
        store.model,
        variable,
        len(points),
        hours,
        len(segments),
    )

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        for segment, block in zip(
            segments,
            pool.map(lambda item: _read_segment(transport, item, rows, columns), segments),
            strict=True,
        ):
            box[:, :, segment.into : segment.into + block.shape[2]] = block

    return np.stack([box[row - row_from, col - col_from] for row, col in indices])


def elevation(
    store: Store, points: list[tuple[float, float]], *, root: str | None = None
) -> np.ndarray:
    """Each point's grid-cell elevation in metres, from the store's own static file.

    Reference evapotranspiration needs atmospheric pressure and gets it from elevation. It
    has to be the *reanalysis's* elevation rather than a real one, because the temperature
    and humidity it is combined with are the reanalysis's own — mixing a 30 m elevation into
    a 25 km temperature would produce a pressure the air in that cell never had.
    """
    transport = _transport(root)
    path = f"{store.model}/static/HSURF.om"
    if not transport.exists(path):
        raise VariableAbsentError(f"{store.model} publishes no static elevation at {path}")

    reader = transport.reader(path)
    try:
        grid = np.asarray(reader.read_array((slice(0, store.ny), slice(0, store.nx))))
    finally:
        reader.close()
    return np.array([float(grid[store.index_of(lat, lon)]) for lat, lon in points])


def hours_utc(start: date, end: date) -> np.ndarray:
    """The UTC timestamps ``read_hourly`` returns values for, aligned to its second axis."""
    first = datetime(start.year, start.month, start.day, tzinfo=UTC)
    count = (_hour_index(end) + 24) - _hour_index(start)
    return np.array([first + timedelta(hours=step) for step in range(count)], dtype="object")


def source_record(store: Store, variables: list[str], *, note: str = "") -> SourceRecord:
    """What was read, from where, at what resolution — including what it is not.

    The uri is the directory the bytes came out of rather than an API call, which is the
    point: a reader who wants to check a number can fetch the same file.
    """
    listed = ",".join(sorted(variables))
    return SourceRecord(
        dataset=store.dataset,
        version=f"{store.model} (Open-Meteo open data)",
        access_route="open-meteo-open-data",
        uri=f"{ARCHIVE_ROOT}/{store.model}/ ({listed})",
        citation=store.citation,
        native_resolution_m=store.native_resolution_m,
        native_timestep="hourly" if not note else f"hourly; {note}",
        licence=store.licence,
    )
