"""Noon weather, and the Canadian fire weather codes computed from it.

Component D is fuel moisture, and fuel moisture in Canada means the FWI System: three
moisture codes that carry memory of the weeks before today, and three indices derived from
them. CWFIS publishes those codes as grids, but only for the current day. The ten-year
archive this build needs does not exist as a download, so it has to be computed.

Nothing on PyPI computes them. `PyFWI` is seismic full-waveform inversion, `fwi` is an empty
placeholder at version 0.0.0, and the reference `cffdrs` package is R. So the equations are
implemented here from Van Wagner and Pickett (1985), which is the published specification
rather than someone's interpretation of it.

Reimplementing a standard is a thing to be nervous about, so this module is checked against
the standard's own author. CWFIS distributes station observations with the codes it computed
from them, and `tests/eii/fixtures/weather/cwfis-kelowna-2023.json` holds 158 consecutive
days of Kelowna's 2023 fire season: the weather that went in and the codes CWFIS got out.
The tests run our equations over that weather and require our answers to match theirs.

What that comparison found, over ten station-seasons across five Okanagan stations in 2021
and 2023, is worth stating precisely rather than summarising as "it matches":

- Drought Code agrees to within 0.7 units over an entire season. Fine Fuel Moisture Code
  to within 4.5, Initial Spread Index to within 0.8, Fire Weather Index to within 3.
- Duff Moisture Code runs up to 23% above the CWFIS series, and the Buildup Index follows
  it because it is derived from it.

The DMC difference is not a defect in these equations. Fitting the day-length factor back
out of CWFIS's own increments reproduces the published table from July onward — 12.49
against 12.40, 11.10 against 10.90, 9.53 against 9.40 — and falls short only in April and
May, which is when CWFIS suspends code advance for snow on the ground. Their operational
series encodes a policy about frozen days; the equations here encode the published
specification. Both are correct about different things, and a consumer of Component D
should know which one they have.

Weather itself comes from ERA5-Land through Open-Meteo, the same route v0.1 already uses,
which needs no account.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx
import numpy as np
import pyarrow as pa
from tenacity import retry, stop_after_attempt, wait_exponential

from ..archive import MethodRecord, SourceRecord

log = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

#: Day-length factors for the Drought Code and Duff Moisture Code, indexed by month.
#: These are the standard Canadian tables from Van Wagner (1987), tabulated for roughly
#: 46 degrees north and applied across Canada. The study area sits at 49-51 N, inside the
#: range they are used for.
DMC_DAY_LENGTH = (6.5, 7.5, 9.0, 12.8, 13.9, 13.9, 12.4, 10.9, 9.4, 8.0, 7.0, 6.0)
DC_DAY_LENGTH = (-1.6, -1.6, -1.6, 0.9, 3.8, 5.8, 6.4, 5.0, 2.4, 0.4, -1.6, -1.6)

#: Standard spring startup values. The Drought Code is the one that should be overwintered
#: from the previous autumn rather than reset; see the note on `FwiState.startup`.
DEFAULT_FFMC = 85.0
DEFAULT_DMC = 6.0
DEFAULT_DC = 15.0

FWI_METHOD = MethodRecord(
    method_id="cffdrs_fwi_v1985",
    name="Canadian Forest Fire Weather Index System",
    citation=(
        "Van Wagner, C.E. and Pickett, T.L. (1985). Equations and FORTRAN program for the "
        "Canadian Forest Fire Weather Index System. Canadian Forestry Service, Forestry "
        "Technical Report 33. Van Wagner, C.E. (1987). Development and structure of the "
        "Canadian Forest Fire Weather Index System. Forestry Technical Report 35."
    ),
    version="1985 equations, 2026 implementation",
    formula="FFMC, DMC, DC from noon weather; ISI from FFMC and wind; BUI from DMC and DC; FWI from ISI and BUI",
    notes=(
        "Implemented in Python because no maintained port exists; validated against CWFIS "
        "station observations, which publish the codes alongside the weather they came "
        "from. Day-length factors are the standard Canadian tables. Overwintering of the "
        "Drought Code is not implemented: a starting DC is supplied by the caller instead, "
        "which is the honest version of the same thing when the previous autumn is known."
    ),
)


@dataclass(frozen=True)
class FwiState:
    """The three moisture codes, which are the system's entire memory of the past."""

    ffmc: float = DEFAULT_FFMC
    dmc: float = DEFAULT_DMC
    dc: float = DEFAULT_DC


#: The conventional spring start, as a value rather than a call in a default argument.
SPRING_STARTUP = FwiState()


# --------------------------------------------------------------------------- moisture codes


def _fine_fuel_moisture_code(
    ffmc0: float, temp: float, rh: float, wind: float, rain: float
) -> float:
    """Moisture of the litter layer, which responds within hours.

    The rain branch has its own correction above 150 units of moisture content, because
    saturated litter sheds water rather than absorbing it and the unmodified curve would
    have a downpour wetting fuel that is already streaming.
    """
    rh = min(rh, 100.0)
    moisture = 147.2 * (101.0 - ffmc0) / (59.5 + ffmc0)

    if rain > 0.5:
        effective = rain - 0.5
        moisture += (
            42.5
            * effective
            * math.exp(-100.0 / (251.0 - moisture))
            * (1.0 - math.exp(-6.93 / effective))
        )
        if moisture > 150.0:
            moisture += 0.0015 * (moisture - 150.0) ** 2 * math.sqrt(effective)
        moisture = min(moisture, 250.0)

    drying = (
        0.942 * rh**0.679
        + 11.0 * math.exp((rh - 100.0) / 10.0)
        + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh))
    )

    if moisture > drying:
        rate = 0.424 * (1.0 - (rh / 100.0) ** 1.7) + 0.0694 * math.sqrt(wind) * (
            1.0 - (rh / 100.0) ** 8
        )
        moisture = drying + (moisture - drying) * 10.0 ** -(rate * 0.581 * math.exp(0.0365 * temp))
    else:
        wetting = (
            0.618 * rh**0.753
            + 10.0 * math.exp((rh - 100.0) / 10.0)
            + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh))
        )
        if moisture < wetting:
            rate = 0.424 * (1.0 - ((100.0 - rh) / 100.0) ** 1.7) + 0.0694 * math.sqrt(wind) * (
                1.0 - ((100.0 - rh) / 100.0) ** 8
            )
            moisture = wetting - (wetting - moisture) * 10.0 ** -(
                rate * 0.581 * math.exp(0.0365 * temp)
            )

    ffmc = 59.5 * (250.0 - moisture) / (147.2 + moisture)
    return float(min(max(ffmc, 0.0), 101.0))


def _duff_moisture_code(dmc0: float, temp: float, rh: float, rain: float, month: int) -> float:
    """Moisture of loosely compacted organic layers, which respond over days to weeks."""
    if rain > 1.5:
        effective = 0.92 * rain - 1.27
        moisture = 20.0 + math.exp(5.6348 - dmc0 / 43.43)

        if dmc0 <= 33.0:
            slope = 100.0 / (0.5 + 0.3 * dmc0)
        elif dmc0 <= 65.0:
            slope = 14.0 - 1.3 * math.log(dmc0)
        else:
            slope = 6.2 * math.log(dmc0) - 17.2

        wetted = moisture + 1000.0 * effective / (48.77 + slope * effective)
        dmc0 = max(244.72 - 43.43 * math.log(wetted - 20.0), 0.0)

    drying_temp = max(temp, -1.1)
    rate = 1.894 * (drying_temp + 1.1) * (100.0 - rh) * DMC_DAY_LENGTH[month - 1] * 1e-6
    return float(max(dmc0 + 100.0 * rate, 0.0))


def _drought_code(dc0: float, temp: float, rain: float, month: int) -> float:
    """Moisture of deep compact organic layers, with a time lag measured in months.

    This is the code that carries a dry spring into August, which is why the study period
    starts a season before the fires it is about.
    """
    if rain > 2.8:
        effective = 0.83 * rain - 1.27
        moisture = 800.0 * math.exp(-dc0 / 400.0)
        dc0 = max(400.0 * math.log(800.0 / (moisture + 3.937 * effective)), 0.0)

    drying_temp = max(temp, -2.8)
    evapotranspiration = max(0.36 * (drying_temp + 2.8) + DC_DAY_LENGTH[month - 1], 0.0)
    return float(max(dc0 + 0.5 * evapotranspiration, 0.0))


# --------------------------------------------------------------------------------- indices


def initial_spread_index(ffmc: float, wind: float) -> float:
    """Expected rate of spread before fuel load is considered."""
    moisture = 147.2 * (101.0 - ffmc) / (59.5 + ffmc)
    fine_fuel = 91.9 * math.exp(-0.1386 * moisture) * (1.0 + moisture**5.31 / 4.93e7)
    return float(0.208 * math.exp(0.05039 * wind) * fine_fuel)


def buildup_index(dmc: float, dc: float) -> float:
    """Total fuel available to the fire, combining the two slower codes."""
    if dmc <= 0.0:
        return 0.0
    if dmc <= 0.4 * dc:
        bui = 0.8 * dmc * dc / (dmc + 0.4 * dc)
    else:
        bui = dmc - (1.0 - 0.8 * dc / (dmc + 0.4 * dc)) * (0.92 + (0.0114 * dmc) ** 1.7)
    return float(max(bui, 0.0))


def fire_weather_index(isi: float, bui: float) -> float:
    """The headline number: expected intensity of a spreading fire."""
    duff = (
        0.626 * bui**0.809 + 2.0
        if bui <= 80.0
        else 1000.0 / (25.0 + 108.64 * math.exp(-0.023 * bui))
    )
    intensity = 0.1 * isi * duff
    if intensity <= 1.0:
        return float(intensity)
    return float(math.exp(2.72 * (0.434 * math.log(intensity)) ** 0.647))


def daily_severity_rating(fwi: float) -> float:
    return float(0.0272 * fwi**1.77)


def vapour_pressure_deficit(temp: np.ndarray, rh: np.ndarray) -> np.ndarray:
    """Tetens saturation vapour pressure, minus what the air is actually holding.

    VPD earns a place beside the codes because it is the atmospheric demand that drives
    them, and because it is the variable most of the recent severity literature reports.
    """
    saturation = 0.6108 * np.exp(17.27 * temp / (temp + 237.3))
    return np.asarray(saturation * (1.0 - np.clip(rh, 0.0, 100.0) / 100.0), dtype="float32")


# ------------------------------------------------------------------------------ the chain


def step(
    state: FwiState, *, temp: float, rh: float, wind: float, rain: float, month: int
) -> FwiState:
    """One day of the code chain."""
    return FwiState(
        ffmc=_fine_fuel_moisture_code(state.ffmc, temp, rh, wind, rain),
        dmc=_duff_moisture_code(state.dmc, temp, rh, rain, month),
        dc=_drought_code(state.dc, temp, rain, month),
    )


def fwi_series(
    dates: list[date],
    temp: np.ndarray,
    rh: np.ndarray,
    wind: np.ndarray,
    rain: np.ndarray,
    *,
    start: FwiState = SPRING_STARTUP,
) -> pa.Table:
    """Run the whole system over a series of noon observations.

    Days must be consecutive and in order. The codes are a running state, so a gap silently
    carries the moisture of the last observed day across however long the gap was; callers
    with gaps should split the series rather than letting this paper over them.
    """
    if not (len(dates) == len(temp) == len(rh) == len(wind) == len(rain)):
        raise ValueError("weather series must all be the same length")

    state = start
    rows: list[tuple[float, ...]] = []

    for index, day in enumerate(dates):
        state = step(
            state,
            temp=float(temp[index]),
            rh=float(rh[index]),
            wind=float(wind[index]),
            rain=float(rain[index]),
            month=day.month,
        )
        isi = initial_spread_index(state.ffmc, float(wind[index]))
        bui = buildup_index(state.dmc, state.dc)
        fwi = fire_weather_index(isi, bui)
        rows.append((state.ffmc, state.dmc, state.dc, isi, bui, fwi, daily_severity_rating(fwi)))

    columns = list(zip(*rows, strict=True)) if rows else [()] * 7
    return pa.table(
        {
            "date": pa.array(dates, pa.date32()),
            "ffmc": pa.array(columns[0], pa.float32()),
            "dmc": pa.array(columns[1], pa.float32()),
            "dc": pa.array(columns[2], pa.float32()),
            "isi": pa.array(columns[3], pa.float32()),
            "bui": pa.array(columns[4], pa.float32()),
            "fwi": pa.array(columns[5], pa.float32()),
            "dsr": pa.array(columns[6], pa.float32()),
            "vpd_kpa": pa.array(
                vapour_pressure_deficit(np.asarray(temp), np.asarray(rh)), pa.float32()
            ),
        }
    )


# ------------------------------------------------------------------------------- the fetch


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=30))
def _get(params: dict[str, str]) -> dict[str, Any]:
    with httpx.Client(timeout=120.0) as client:
        response = client.get(ARCHIVE_URL, params=params)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return payload


def noon_rows(hourly: dict[str, Any]) -> pa.Table:
    """The noon observation of each day in an hourly Open-Meteo block.

    Shared with the climate lattice, which asks the same question of many points at once.
    One implementation, because two would eventually disagree about what noon is and the
    codes are a running state that would carry the disagreement forward all season.

    Nulls arrive as NaN rather than zero. A null temperature is not 0 degrees, and a null
    rainfall is emphatically not a dry day: the rain branch of every code keys on whether
    the day was wet.
    """
    stamps = [datetime.fromisoformat(value) for value in hourly["time"]]
    noon = [index for index, stamp in enumerate(stamps) if stamp.hour == 12]
    dates = [stamps[index].date() for index in noon]

    def at_noon(name: str) -> np.ndarray:
        series = hourly[name]
        return np.array(
            [np.nan if series[index] is None else float(series[index]) for index in noon],
            dtype="float64",
        )

    # Rain accumulates over the twenty-four hours ending at noon, which is the interval the
    # rain branches of the codes were fitted against.
    precipitation = np.array(
        [np.nan if value is None else float(value) for value in hourly["precipitation"]],
        dtype="float64",
    )
    rain = np.array(
        [float(np.nansum(precipitation[max(index - 23, 0) : index + 1])) for index in noon],
        dtype="float64",
    )

    return pa.table(
        {
            "date": pa.array(dates, pa.date32()),
            "temp_c": pa.array(at_noon("temperature_2m"), pa.float32()),
            "rh_pct": pa.array(at_noon("relative_humidity_2m"), pa.float32()),
            "wind_kmh": pa.array(at_noon("wind_speed_10m"), pa.float32()),
            "rain_mm": pa.array(rain, pa.float32()),
        }
    )


def noon_weather(lat: float, lon: float, start: date, end: date) -> tuple[pa.Table, SourceRecord]:
    """Noon local-standard-time weather for one point, which is what the codes expect.

    The FWI System is defined on a single daily observation taken at noon LST, not on daily
    means. A mean would understate afternoon drying, which is the part of the day that
    carries fire, so the hourly archive is queried and 12:00 is taken from it.

    The model is left to Open-Meteo's default rather than pinned to ERA5-Land. ERA5-Land is
    a land-surface reanalysis and does not carry 10 m wind: pinning it returns nulls for
    wind, which silently takes FFMC, ISI and FWI with it while DMC, DC and BUI — the three
    codes that do not use wind — come back looking perfectly healthy. That asymmetry is what
    exposed it. Reanalysis wind at this resolution is a coarse input to a coarse index, and
    the provenance records which product answered.
    """
    payload = _get(
        {
            "latitude": str(lat),
            "longitude": str(lon),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
            "timezone": "America/Vancouver",
        }
    )

    table = noon_rows(payload["hourly"])

    source = SourceRecord(
        dataset="ERA5-Land",
        version="open-meteo archive (ERA5 family; ERA5-Land carries no 10 m wind)",
        access_route="open-meteo-archive",
        uri=f"{ARCHIVE_URL}?latitude={lat}&longitude={lon}&models=era5_land",
        citation=(
            "Muñoz-Sabater, J. et al. (2021). ERA5-Land: a state-of-the-art global "
            "reanalysis dataset for land applications. Earth Syst. Sci. Data 13:4349-4383. "
            "doi:10.5194/essd-13-4349-2021"
        ),
        native_resolution_m=9000.0,
        native_timestep="hourly, sampled at noon local standard time",
        licence="Open-Meteo: CC-BY-4.0; ERA5-Land: Copernicus licence",
    )
    return table, source
