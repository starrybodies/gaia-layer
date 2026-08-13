"""SPEIbase, read over HTTP byte ranges rather than downloaded.

SPEIbase is the published global SPEI product and it is what Component E is checked
against. It is also 376 MB per timescale, three of which is 1.1 GB to extract twelve
half-degree cells over the Okanagan.

It does not have to be downloaded. digital.csic.es answers with `accept-ranges: bytes`, and
the files are HDF5 underneath their `.nc` extension, so h5py reading through a seekable
object that fetches ranges on demand pulls a month's global slice — one chunk — per read.
Twenty-odd requests instead of 376 MB.

Three things were tried before this one, and the reasons they failed are worth keeping so
nobody spends the afternoon again. GDAL's netCDF driver refuses `/vsicurl` outside Linux:
*"Opening a /vsi file with the netCDF driver requires Linux userfaultfd to be available."*
Forcing GDAL's HDF5 driver instead cannot open a `/vsicurl` path at all. And fsspec, which
exists precisely to be this file object, fails on this host because its aiohttp backend
verifies certificates against the system store rather than certifi and cannot complete a TLS
handshake with digital.csic.es — while httpx, which the rest of this codebase already uses,
can. So the range reader here is forty lines against a dependency that does not work.

**The coverage gap, which matters more than any of that.** SPEIbase v2.11 runs 1901-01 to
2022-12. The study years are 2015-2024 and the case study is an August 2023 fire, so the
published product covers neither of the two years the demo turns on. That is why Component E
computes SPEI rather than reading it, and why this module exists only to check the computed
one against the published one over the years they share. Recorded as D-015.
"""

from __future__ import annotations

import io
import logging
from datetime import date, timedelta

import h5py
import httpx
import numpy as np

from ..archive import SourceRecord

log = logging.getLogger(__name__)

#: SPEIbase v2.11 on digital.CSIC. The handle is the citable record; the bitstream numbers
#: below it are per-file and change between versions, which is why they are a table rather
#: than a formula.
HANDLE = "https://digital.csic.es/handle/10261/332007"
BITSTREAM = "https://digital.csic.es/bitstream/10261/332007"

#: Timescale in months to the bitstream that holds it. Only the three the component blends.
FILES: dict[int, str] = {
    1: f"{BITSTREAM}/3/spei01.nc",
    3: f"{BITSTREAM}/5/spei03.nc",
    12: f"{BITSTREAM}/14/spei12.nc",
}

#: The file's own time origin, from the `units` attribute: "days since 1900-1-1".
EPOCH = date(1900, 1, 1)

#: The last month the published product carries. Asserted on read rather than trusted, so
#: that a new release extending the record is noticed instead of silently ignored.
PUBLISHED_THROUGH = date(2022, 12, 1)

#: Values at or above this are the file's fill, not a standardised index.
FILL_ABOVE = 1e29


class RangeReader(io.RawIOBase):
    """A seekable file over HTTP range requests, for handing to h5py.

    Wrap it in `io.BufferedReader` before use. HDF5 reads its own metadata in many small
    pieces, and unbuffered that becomes one request per piece.
    """

    def __init__(self, url: str, client: httpx.Client) -> None:
        self._url = url
        self._client = client
        self._position = 0
        head = client.head(url, follow_redirects=True, timeout=60.0)
        head.raise_for_status()
        if head.headers.get("accept-ranges") != "bytes":
            raise RuntimeError(f"{url} does not serve byte ranges; it would have to be downloaded")
        self._size = int(head.headers["content-length"])
        self.requests = 0

    @property
    def size(self) -> int:
        return self._size

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._position = offset
        elif whence == io.SEEK_CUR:
            self._position += offset
        else:
            self._position = self._size + offset
        return self._position

    def readinto(self, buffer) -> int:  # type: ignore[no-untyped-def]
        wanted = min(len(buffer), self._size - self._position)
        if wanted <= 0:
            return 0
        response = self._client.get(
            self._url,
            headers={"Range": f"bytes={self._position}-{self._position + wanted - 1}"},
            follow_redirects=True,
            timeout=180.0,
        )
        response.raise_for_status()
        self.requests += 1
        payload = response.content[:wanted]
        buffer[: len(payload)] = payload
        self._position += len(payload)
        return len(payload)


def published_spei(
    points: list[tuple[float, float]], months: list[date], *, timescale: int
) -> tuple[np.ndarray, SourceRecord]:
    """SPEIbase's own value at each point for each month. Rows are points, columns months.

    A month outside the published record comes back NaN rather than being clipped to the
    last one available, which is the whole reason this module knows its own end date.
    """
    url = FILES[timescale]
    values = np.full((len(points), len(months)), np.nan)

    with httpx.Client() as client:
        raw = RangeReader(url, client)
        with h5py.File(io.BufferedReader(raw, buffer_size=2**20), "r") as handle:
            lat = np.asarray(handle["lat"][:], dtype="float64")
            lon = np.asarray(handle["lon"][:], dtype="float64")
            stamps = np.asarray(handle["time"][:], dtype="float64")
            grid = handle["spei"]

            available = [EPOCH + timedelta(days=float(value)) for value in stamps]
            last = available[-1]
            if date(last.year, last.month, 1) != PUBLISHED_THROUGH:
                log.warning(
                    "SPEIbase now runs through %s, not %s; D-015 should be revisited",
                    last,
                    PUBLISHED_THROUGH,
                )

            by_month = {(day.year, day.month): index for index, day in enumerate(available)}
            rows = [int(np.argmin(np.abs(lat - point[0]))) for point in points]
            columns = [int(np.argmin(np.abs(lon - point[1]))) for point in points]

            for position, month in enumerate(months):
                index = by_month.get((month.year, month.month))
                if index is None:
                    continue
                slab = np.asarray(grid[index, :, :], dtype="float64")
                for node, (row, column) in enumerate(zip(rows, columns, strict=True)):
                    values[node, position] = slab[row, column]

        log.info("SPEIbase %d-month: %d range requests", timescale, raw.requests)

    values = np.where(np.abs(values) < FILL_ABOVE, values, np.nan)

    source = SourceRecord(
        dataset="SPEIbase",
        version="v2.11",
        access_route="digital-csic-http-range",
        uri=url,
        citation=(
            "Beguería, S., Vicente-Serrano, S.M., Reig, F. and Latorre, B. (2014). "
            "Standardized precipitation evapotranspiration index (SPEI) revisited: "
            "parameter fitting, evapotranspiration models, tools, datasets and drought "
            "monitoring. International Journal of Climatology 34:3001-3023, "
            "doi:10.1002/joc.3887. SPEIbase v2.11, doi:10.20350/digitalCSIC/16497."
        ),
        native_resolution_m=55_000.0,
        native_timestep=f"monthly, {timescale}-month accumulation, through 2022-12",
        licence="Creative Commons Attribution 4.0",
    )
    return values, source
