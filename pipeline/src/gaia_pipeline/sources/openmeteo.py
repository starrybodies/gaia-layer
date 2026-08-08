"""ERA5 and ERA5-Land through the Open-Meteo historical archive.

Why not the Copernicus Climate Data Store directly: the CDS API needs a registered account
and a credential on disk, and its retrievals are queued rather than synchronous. Neither
works on a cold-start machine inside the runbook's thirty-minute budget, and neither runs
unattended. Open-Meteo serves the same ERA5 and ERA5-Land reanalysis anonymously and
synchronously. See docs/DIVERGENCES.md D-002 for the full trade, including what it costs.

The underlying dataset is recorded in provenance as `ECMWF/ERA5-Land`, and the intermediary
separately as `open-meteo-archive`, so a consumer can always tell the two apart.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import settings

log = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SOURCE = "ECMWF"
DATASET = "ECMWF/ERA5-Land"
ACCESS_ROUTE = "open-meteo-archive"

# ERA5-Land is delivered on a roughly 9 km grid. Sampling a 3 x 3 lattice across the pilot
# area covers it at about that spacing without pretending to a resolution the reanalysis
# does not have.
SAMPLE_LATTICE = 3

DAILY_VARIABLES = (
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "dew_point_2m_mean",
    "precipitation_sum",
    "soil_moisture_0_to_7cm_mean",
    "soil_moisture_7_to_28cm_mean",
)


@dataclass(frozen=True)
class DailyRecord:
    day: date
    temp_max_c: float | None
    temp_mean_c: float | None
    dew_point_c: float | None
    precip_mm: float | None
    soil_moisture_0_7: float | None
    soil_moisture_7_28: float | None


@dataclass(frozen=True)
class PointSeries:
    latitude: float
    longitude: float
    elevation_m: float | None
    records: list[DailyRecord]

    @property
    def observation_id(self) -> str:
        return f"{DATASET}:{self.latitude:.4f},{self.longitude:.4f}"


def sample_points(
    bbox: tuple[float, float, float, float], lattice: int = SAMPLE_LATTICE
) -> list[tuple[float, float]]:
    """A regular lattice of sample points inside a bounding box, avoiding its edges."""
    west, south, east, north = bbox
    points: list[tuple[float, float]] = []
    for i in range(lattice):
        for j in range(lattice):
            lon = west + (east - west) * (i + 0.5) / lattice
            lat = south + (north - south) * (j + 0.5) / lattice
            points.append((lat, lon))
    return points


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=3, min=3, max=60), reraise=True)
def fetch_point(latitude: float, longitude: float, start: date, end: date) -> PointSeries:
    """Daily ERA5-Land variables for one coordinate."""
    response = httpx.get(
        ARCHIVE_URL,
        params={
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": ",".join(DAILY_VARIABLES),
            "timezone": "UTC",
        },
        timeout=settings().http_timeout_s,
    )
    response.raise_for_status()
    payload = response.json()
    daily = payload.get("daily", {})
    days = daily.get("time", [])

    def column(name: str) -> list[float | None]:
        values = daily.get(name) or [None] * len(days)
        return list(values)

    temp_max = column("temperature_2m_max")
    temp_mean = column("temperature_2m_mean")
    dew = column("dew_point_2m_mean")
    precip = column("precipitation_sum")
    soil_shallow = column("soil_moisture_0_to_7cm_mean")
    soil_deep = column("soil_moisture_7_to_28cm_mean")

    records = [
        DailyRecord(
            day=datetime.strptime(day, "%Y-%m-%d").date(),
            temp_max_c=temp_max[i],
            temp_mean_c=temp_mean[i],
            dew_point_c=dew[i],
            precip_mm=precip[i],
            soil_moisture_0_7=soil_shallow[i],
            soil_moisture_7_28=soil_deep[i],
        )
        for i, day in enumerate(days)
    ]

    return PointSeries(
        latitude=float(payload.get("latitude", latitude)),
        longitude=float(payload.get("longitude", longitude)),
        elevation_m=payload.get("elevation"),
        records=records,
    )


def saturation_vapour_pressure_kpa(temperature_c: float) -> float:
    """Tetens equation over liquid water.

    Chosen over the fuller Goff-Gratch formulation because the difference between them is
    well under a percent across the temperatures this bioregion sees, and Tetens is what
    the fire-weather literature uses.
    """
    return 0.6108 * math.exp((17.27 * temperature_c) / (temperature_c + 237.3))


def vapour_pressure_deficit_kpa(temperature_c: float, dew_point_c: float) -> float:
    """VPD: how hard the atmosphere is pulling water out of vegetation.

    Computed from air temperature and dew point rather than relative humidity, because
    dew point is what the reanalysis actually carries and converting through relative
    humidity would round-trip through the same equation twice.

    Clamped at zero: a dew point above air temperature means supersaturation, which is a
    reporting artefact rather than negative atmospheric demand.
    """
    return max(
        0.0,
        saturation_vapour_pressure_kpa(temperature_c) - saturation_vapour_pressure_kpa(dew_point_c),
    )
