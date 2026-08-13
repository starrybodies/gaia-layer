"""The climate lattice: one reanalysis read, carried to cells, shared by three components.

Components B, D and E are all functions of the same weather. Fetching it three times would
triple the traffic and, worse, allow the three to disagree about what the weather was. So it
is read once onto lattice points spaced at the reanalysis's own resolution and carried from
there to cells.

**Where it comes from, and why that changed.** Until D-016 this module went through
Open-Meteo's archive API. That does not work for a forty-year reference distribution at
eighty-eight nodes and no amount of batching, pacing or backoff makes it work: the free tier
meters by call weight, roughly ``ceil(days / 14) * ceil(variables / 10) * locations``, so the
water balance alone is about ninety thousand weighted calls. Three runs died on it. It now
reads the same archive out of Open-Meteo's published open-data bucket by byte range —
anonymous, unmetered, and the same bytes the API serves from. See ``om_archive`` for the
verification, and D-016 for the story.

**Two models, because one is incomplete.** ERA5-Land carries soil moisture but no
precipitation, no reference evapotranspiration and no 10 m wind. This is the same
partial-variable-set gap that returned nulls for 10 m wind and took FFMC, ISI and FWI down
with it, and it is recorded as D-014. So the water balance and the noon weather come from
ERA5 at a quarter degree, and soil moisture stays on ERA5-Land at a tenth. The two halves of
Component B therefore sit at different native resolutions, roughly 25 km against 9 km, and
both source records say so rather than presenting the component as one measurement.

**Two variables the store does not carry, computed rather than fetched.** Open-Meteo's API
derives reference evapotranspiration and relative humidity rather than storing them, so they
are derived here: ET0 by the FAO-56 Penman-Monteith daily equation through ``refet``, and
relative humidity from dew point by the FAO-56 saturation vapour pressure curve. Both are
measured against the API's own answers rather than assumed equivalent — ET0 agrees at
r = 0.991 with a bias of -0.13 mm/day, relative humidity to 0.27 percentage points, both
recorded as D-018. A method substitution stated as one is the precedent D-010 and D-015 set.

**The lattice does not add resolution and does not pretend to.** A 25 km reanalysis carried
onto 0.74 km hexes produces a number for every hex, and nothing in that number says it was
interpolated between nodes 25 km apart. What the interpolation buys is the absence of
visible tile seams; it cannot buy detail the reanalysis never had. Every source record
carries the native resolution so a consumer can see the gap for the cell they asked about.

Nulls arrive as NaN. A variable that is null everywhere is a configuration error and is
raised rather than served: a component computed from an entirely absent variable is not a
weak measurement, it is not a measurement.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow as pa
import refet

from ...config import AreaOfInterest
from ..archive import SourceRecord
from ..spine import Spine
from . import om_archive
from .weather import noon_rows

log = logging.getLogger(__name__)

#: The ERA5 0.25 degree product. A finer lattice would fetch the same reanalysis cell several
#: times over and present the repetition as spatial detail.
LATTICE_SPACING_DEG = 0.25

#: How far back the reference distribution reaches. SPEI and the water-balance anomaly are
#: both departures, and a departure is only as meaningful as the distribution behind it.
#: Forty years is longer than the WMO thirty-year normal and short enough that the fetch
#: finishes; SPEIbase fits on 1901 onward, and ``drought.py`` measures what that costs us.
REFERENCE_START = date(1985, 1, 1)

#: The three nearest nodes. Two would interpolate along a line and leave the third direction
#: unrepresented; more would drag in nodes on the far side of a valley.
NEIGHBOURS = 3

#: Local standard time is what the FWI System's noon observation is defined on, and what
#: Open-Meteo returned when this module asked it for ``timezone=America/Vancouver``. Kept
#: identical so the codes are computed off the same hour of the day they were before.
STUDY_TZ = ZoneInfo("America/Vancouver")

#: Standard atmospheric lapse rate, used only to state the offset this module does *not*
#: apply. Open-Meteo's API corrects temperature and dew point from the ERA5 cell's elevation
#: to the requested coordinate's; this module returns the uncorrected reanalysis, because
#: every component built on it is a departure from the same node's own record and a constant
#: offset cancels in a departure. Recorded as D-017.
LAPSE_RATE_K_PER_M = 0.0065


#: Re-exported so callers need not know which store refused, and so that catching it here
#: catches what ``om_archive`` raises rather than a look-alike.
VariableAbsentError = om_archive.VariableAbsentError


def lattice(area: AreaOfInterest) -> list[tuple[float, float]]:
    """The reanalysis nodes covering an area, on a whole-degree-aligned grid.

    Aligned to whole degrees rather than to the area's own corners so that two areas which
    overlap ask for the same nodes and the cache is shared rather than duplicated.
    """
    bbox = area.bbox()
    step = LATTICE_SPACING_DEG
    lats = np.arange(np.floor(bbox.south / step) * step, bbox.north + step, step)
    lons = np.arange(np.floor(bbox.west / step) * step, bbox.east + step, step)
    return [(round(float(lat), 4), round(float(lon), 4)) for lat in lats for lon in lons]


# ------------------------------------------------------------------ derived variables


def relative_humidity(temperature_c: np.ndarray, dew_point_c: np.ndarray) -> np.ndarray:
    """Relative humidity in percent, from temperature and dew point.

    The ratio of the saturation vapour pressure at the dew point to that at the air
    temperature, on the FAO-56 curve (Allen et al. 1998, eq. 11) — the same curve ``refet``
    uses for ET0, so the two derived variables cannot disagree about what saturated air is.
    Checked against Open-Meteo's own ``relative_humidity_2m`` over June to August 2023:
    mean absolute difference 0.27 percentage points, which is that field's own rounding.
    """
    ratio = _saturation_kpa(dew_point_c) / _saturation_kpa(temperature_c)
    return np.asarray(100.0 * ratio, dtype="float64")


def _saturation_kpa(temperature_c: np.ndarray) -> np.ndarray:
    return 0.6108 * np.exp(17.27 * temperature_c / (temperature_c + 237.3))


def _et0_daily(
    *,
    tmin: np.ndarray,
    tmax: np.ndarray,
    tdew: np.ndarray,
    wind_ms: np.ndarray,
    shortwave_w: np.ndarray,
    elevation_m: float,
    latitude: float,
    day_of_year: np.ndarray,
) -> np.ndarray:
    """FAO-56 reference evapotranspiration for a grass surface, one node, mm/day.

    Open-Meteo computes ET0 hourly and sums; this uses the daily form of the same equation,
    because the daily form is the published one a validator can check against a textbook and
    it needs no clear-sky radiation model to close the longwave term. Over 2023 at the
    Kelowna node the two agree at r = 0.9912, with the daily form running 0.13 mm/day drier —
    almost all of it in December and January, when both are near zero. In the fire season the
    gap is under 2%. Component B is a departure and Component E fits a distribution, so a
    systematic offset in the method largely cancels; the disagreement is quantified rather
    than assumed away, and D-018 records it.

    Elevation is the reanalysis cell's own, not the ground's, because the temperature and
    humidity it is combined with are the reanalysis's own.
    """
    return np.asarray(
        refet.Daily(
            tmin=tmin,
            tmax=tmax,
            rs=shortwave_w * 0.0864,  # mean W m-2 over the day -> MJ m-2 day-1
            uz=wind_ms,
            zw=10.0,
            elev=elevation_m,
            lat=latitude,
            doy=day_of_year,
            tdew=tdew,
            method="asce",
        ).eto(),
        dtype="float64",
    )


# ------------------------------------------------------------------ aggregation helpers


def _daily_mean(hourly: np.ndarray, day_of: np.ndarray, n_days: int) -> np.ndarray:
    """Mean of the hours that reported, per day. A day with no hours comes back NaN."""
    present = np.isfinite(hourly)
    totals = np.bincount(day_of[present], weights=hourly[present], minlength=n_days)
    counts = np.bincount(day_of[present], minlength=n_days)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.asarray(np.where(counts > 0, totals / np.maximum(counts, 1), np.nan))


def _daily_sum(hourly: np.ndarray, day_of: np.ndarray, n_days: int) -> np.ndarray:
    """Total of the hours that reported, per day. A day with no hours comes back NaN.

    Never zero for an absent day. Zero rain is a measurement — a dry day is the input the
    rain branch of every fire weather code keys on — and an unmeasured day is not.
    """
    present = np.isfinite(hourly)
    totals = np.bincount(day_of[present], weights=hourly[present], minlength=n_days)
    counts = np.bincount(day_of[present], minlength=n_days)
    return np.asarray(np.where(counts > 0, totals, np.nan))


def _daily_reduce(hourly: np.ndarray, day_of: np.ndarray, n_days: int, how: str) -> np.ndarray:
    """Per-day minimum or maximum over the hours that reported."""
    out = np.full(n_days, np.nan)
    present = np.isfinite(hourly)
    if not present.any():
        return out
    reducer = np.fmin.at if how == "min" else np.fmax.at
    out[:] = np.nan
    reducer(out, day_of[present], hourly[present])
    return out


def _utc_days(start: date, end: date) -> tuple[list[date], np.ndarray]:
    """The UTC calendar days in ``[start, end]`` and, per hour, which day it belongs to."""
    n_days = (end - start).days + 1
    days = [start + timedelta(days=step) for step in range(n_days)]
    return days, np.repeat(np.arange(n_days), 24)


def _require_something(name: str, values: np.ndarray, model: str) -> None:
    if not np.isfinite(values).any():
        raise VariableAbsentError(
            f"{model} carries no {name} over the area: every value came back null. This is "
            "the ERA5-Land partial-variable-set failure, and it is a configuration error "
            "rather than a weak measurement."
        )


# ------------------------------------------------------------------ Component B and E input


def water_balance(
    points: list[tuple[float, float]], start: date, end: date
) -> tuple[pa.Table, SourceRecord]:
    """Daily precipitation and reference evapotranspiration at every lattice node.

    ET0 rather than actual ET, and reference rather than potential. MODIS actual ET is a
    land-surface product and ERA5-Land does not carry ET0 at all; FAO-56 ET0 is the
    well-watered reference the SPEI literature already substitutes, so the substitution is
    one the drought literature has already made.
    """
    days, day_of = _utc_days(start, end)
    n_days = len(days)

    temperature = om_archive.read_hourly(om_archive.ERA5, "temperature_2m", points, start, end)
    dew_point = om_archive.read_hourly(om_archive.ERA5, "dew_point_2m", points, start, end)
    rain_hourly = om_archive.read_hourly(om_archive.ERA5, "precipitation", points, start, end)
    shortwave = om_archive.read_hourly(om_archive.ERA5, "shortwave_radiation", points, start, end)
    wind_u = om_archive.read_hourly(om_archive.ERA5, "wind_u_component_10m", points, start, end)
    wind_v = om_archive.read_hourly(om_archive.ERA5, "wind_v_component_10m", points, start, end)
    elevations = om_archive.elevation(om_archive.ERA5, points)

    _require_something("precipitation", rain_hourly, om_archive.ERA5.model)
    _require_something("temperature", temperature, om_archive.ERA5.model)
    _require_something("shortwave radiation", shortwave, om_archive.ERA5.model)

    day_of_year = np.array([moment.timetuple().tm_yday for moment in days])
    index: list[int] = []
    rain: list[np.ndarray] = []
    demand: list[np.ndarray] = []

    for position, (latitude, _) in enumerate(points):
        precipitation = _daily_sum(rain_hourly[position], day_of, n_days)
        et0 = _et0_daily(
            tmin=_daily_reduce(temperature[position], day_of, n_days, "min"),
            tmax=_daily_reduce(temperature[position], day_of, n_days, "max"),
            tdew=_daily_mean(dew_point[position], day_of, n_days),
            wind_ms=_daily_mean(np.hypot(wind_u[position], wind_v[position]), day_of, n_days),
            shortwave_w=_daily_mean(shortwave[position], day_of, n_days),
            elevation_m=float(elevations[position]),
            latitude=latitude,
            day_of_year=day_of_year,
        )
        index.extend([position] * n_days)
        rain.append(precipitation)
        demand.append(et0)

    table = pa.table(
        {
            "point": pa.array(index, pa.int32()),
            "date": pa.array(days * len(points), pa.date32()),
            "precipitation_mm": pa.array(np.concatenate(rain), pa.float32()),
            "et0_mm": pa.array(np.concatenate(demand), pa.float32()),
        }
    )

    source = om_archive.source_record(
        om_archive.ERA5,
        [
            "precipitation",
            "temperature_2m",
            "dew_point_2m",
            "shortwave_radiation",
            "wind_u_component_10m",
            "wind_v_component_10m",
        ],
        note="aggregated to UTC days; ET0 by FAO-56 Penman-Monteith (refet, ASCE daily)",
    )
    return table, source


# ------------------------------------------------------------------ Component B input


def soil_moisture(
    points: list[tuple[float, float]], start: date, end: date
) -> tuple[pa.Table, SourceRecord]:
    """Daily mean volumetric soil moisture at two depths, from ERA5-Land.

    The shallow layer (7-28 cm) is where a season's drying shows and the deep one
    (28-100 cm) is where a multi-year deficit accumulates, which is why both are carried
    rather than blended. ERA5-Land is the finer of the two models at about 9 km and is the
    half of Component B it does carry.

    A node over open water has no soil and comes back NaN rather than 0.0. An Okanagan Lake
    node reading bone dry would be the worst kind of wrong: plausible, and in the direction
    that raises the score.
    """
    days, day_of = _utc_days(start, end)
    n_days = len(days)

    shallow_hourly = om_archive.read_hourly(
        om_archive.ERA5_LAND, "soil_moisture_7_to_28cm", points, start, end
    )
    deep_hourly = om_archive.read_hourly(
        om_archive.ERA5_LAND, "soil_moisture_28_to_100cm", points, start, end
    )
    _require_something("shallow soil moisture", shallow_hourly, om_archive.ERA5_LAND.model)
    _require_something("deep soil moisture", deep_hourly, om_archive.ERA5_LAND.model)

    index: list[int] = []
    shallow: list[np.ndarray] = []
    deep: list[np.ndarray] = []
    for position in range(len(points)):
        index.extend([position] * n_days)
        shallow.append(_daily_mean(shallow_hourly[position], day_of, n_days))
        deep.append(_daily_mean(deep_hourly[position], day_of, n_days))

    table = pa.table(
        {
            "point": pa.array(index, pa.int32()),
            "date": pa.array(days * len(points), pa.date32()),
            "soil_shallow": pa.array(np.concatenate(shallow), pa.float32()),
            "soil_deep": pa.array(np.concatenate(deep), pa.float32()),
        }
    )

    source = om_archive.source_record(
        om_archive.ERA5_LAND,
        ["soil_moisture_7_to_28cm", "soil_moisture_28_to_100cm"],
        note="aggregated to UTC daily means",
    )
    return table, source


# ------------------------------------------------------------------ Component D input


def noon_weather_lattice(
    points: list[tuple[float, float]], start: date, end: date
) -> tuple[pa.Table, SourceRecord]:
    """Noon weather at every lattice node, in the form the fire weather codes expect.

    The FWI System is defined on a single daily observation taken at noon local standard
    time, not on daily means. A mean would understate afternoon drying, which is the part of
    the day that carries fire.

    ERA5 rather than ERA5-Land, for the reason ``weather.py`` records: ERA5-Land carries no
    10 m wind, and asking for it returns nulls that take FFMC, ISI and FWI down while DMC,
    DC and BUI — the three codes that do not use wind — come back looking perfectly healthy.
    That asymmetry is what exposed it.

    A day either side of the window is read so that local noon can be found for every day
    in it whatever the UTC offset, and so the twenty-four hours of rain ending at the first
    noon are present rather than truncated to whatever the window happened to start with.
    """
    padded_start, padded_end = start - timedelta(days=1), end + timedelta(days=1)
    stamps = om_archive.hours_utc(padded_start, padded_end)
    local = [stamp.astimezone(STUDY_TZ) for stamp in stamps]

    temperature = om_archive.read_hourly(
        om_archive.ERA5, "temperature_2m", points, padded_start, padded_end
    )
    dew_point = om_archive.read_hourly(
        om_archive.ERA5, "dew_point_2m", points, padded_start, padded_end
    )
    rain = om_archive.read_hourly(
        om_archive.ERA5, "precipitation", points, padded_start, padded_end
    )
    wind_u = om_archive.read_hourly(
        om_archive.ERA5, "wind_u_component_10m", points, padded_start, padded_end
    )
    wind_v = om_archive.read_hourly(
        om_archive.ERA5, "wind_v_component_10m", points, padded_start, padded_end
    )
    _require_something("noon weather", temperature, om_archive.ERA5.model)

    times = [moment.isoformat() for moment in local]

    tables: list[pa.Table] = []
    for position in range(len(points)):
        hourly = {
            "time": times,
            "temperature_2m": _nullable(temperature[position]),
            "relative_humidity_2m": _nullable(
                relative_humidity(temperature[position], dew_point[position])
            ),
            # km/h, which is the unit the FWI System's wind term is defined in.
            "wind_speed_10m": _nullable(np.hypot(wind_u[position], wind_v[position]) * 3.6),
            "precipitation": _nullable(rain[position]),
        }
        rows = noon_rows(hourly)
        wanted = [
            index
            for index, moment in enumerate(rows.column("date").to_pylist())
            if start <= moment <= end
        ]
        rows = rows.take(pa.array(wanted, pa.int32()))
        tables.append(rows.add_column(0, "point", pa.array([position] * rows.num_rows, pa.int32())))

    if not tables:
        raise VariableAbsentError("the archive returned nothing for the lattice")

    table = pa.concat_tables(tables)
    source = om_archive.source_record(
        om_archive.ERA5,
        [
            "temperature_2m",
            "dew_point_2m",
            "precipitation",
            "wind_u_component_10m",
            "wind_v_component_10m",
        ],
        note=(
            "sampled at noon America/Vancouver; relative humidity from dew point by "
            "FAO-56 eq. 11; wind speed from the 10 m components"
        ),
    )
    return table, source


def _nullable(values: np.ndarray) -> list[float | None]:
    """NaN back to None, because ``noon_rows`` reads the archive's own null-bearing form."""
    return [None if not np.isfinite(value) else float(value) for value in values]


# ------------------------------------------------------------------ carrying to cells


def to_cells(spine: Spine, points: list[tuple[float, float]], values: np.ndarray) -> np.ndarray:
    """Carry one value per lattice node onto the spine's cells.

    Inverse-distance weighted over the three nearest nodes that have a value. A cell sitting
    on a node takes that node's value exactly rather than a blend of it with its neighbours,
    which is the property that keeps the lattice recoverable from the output. A cell whose
    three nearest nodes are all missing comes back missing.

    Distance is great-circle on a sphere. At a lattice spacing of 25 km the difference
    between that and a projected distance is centimetres, and the sphere needs no CRS.
    """
    values = np.asarray(values, dtype="float64")
    if values.shape != (len(points),):
        raise ValueError(
            f"lattice and values must be the same length: {len(points)} nodes, {values.size} values"
        )

    node_lat = np.radians(np.array([lat for lat, _ in points], dtype="float64"))
    node_lon = np.radians(np.array([lon for _, lon in points], dtype="float64"))
    cell_lat = np.radians(np.asarray(spine.cells.column("lat"), dtype="float64"))
    cell_lon = np.radians(np.asarray(spine.cells.column("lon"), dtype="float64"))

    # Haversine, cells down the rows and nodes across the columns.
    dlat = node_lat[None, :] - cell_lat[:, None]
    dlon = node_lon[None, :] - cell_lon[:, None]
    haversine = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(cell_lat)[:, None] * np.cos(node_lat)[None, :] * np.sin(dlon / 2.0) ** 2
    )
    distance = 6_371_000.0 * 2.0 * np.arcsin(np.sqrt(np.clip(haversine, 0.0, 1.0)))

    usable = np.isfinite(values)
    if not usable.any():
        return np.full(spine.n_cells, np.nan, dtype="float32")
    distance = np.where(usable[None, :], distance, np.inf)

    nearest = np.argsort(distance, axis=1)[:, :NEIGHBOURS]
    rows = np.arange(spine.n_cells)[:, None]
    picked = distance[rows, nearest]
    weight = np.where(np.isfinite(picked), 1.0 / np.maximum(picked, 1.0), 0.0)

    # A cell within a metre of a node is on it, and takes it whole.
    on_node = picked <= 1.0
    weight = np.where(on_node.any(axis=1, keepdims=True), on_node.astype("float64"), weight)

    total = weight.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        carried = np.where(
            total > 0.0, (weight * values[nearest]).sum(axis=1) / np.maximum(total, 1e-12), np.nan
        )
    return np.asarray(carried, dtype="float32")


def season_window(as_of: date, days: int) -> tuple[date, date]:
    """The `days`-long window ending on `as_of`, which is how every component is dated."""
    return (as_of - timedelta(days=days - 1), as_of)
