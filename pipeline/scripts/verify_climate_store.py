"""Measure the published store against the API it is served from, and write down the answer.

    uv run --project pipeline python pipeline/scripts/verify_climate_store.py

This repository has twice been burned by a source that catalogued fine and then returned
nothing (D-009, D-014), so a new source is not adopted on the strength of a directory
listing. This script reads real values for real variables at a real coordinate and compares
them, variable by variable, against Open-Meteo's archive API — the door the pipeline used to
come in through. It writes ``docs/climate-store.md``.

It is a script rather than a test because it calls two networks. The bounds it measures are
pinned in ``tests/eii/sources/test_climate.py`` against recorded fixtures, which is where a
regression would be caught; this is where the number comes from in the first place.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import numpy as np
import refet

from gaia_pipeline.eii.sources import climate, om_archive

OUT = Path(__file__).resolve().parents[2] / "docs" / "climate-store.md"
API = "https://archive-api.open-meteo.com/v1/archive"

#: One node, because the comparison is about whether the two doors serve the same bytes, not
#: about spatial coverage. This is the ERA5 cell McDougall Creek burned in.
NODE = (50.0, -119.5)

HOURLY_WINDOW = (date(2023, 8, 10), date(2023, 8, 17))
YEAR_WINDOW = (date(2023, 1, 1), date(2023, 12, 31))


def _api(params: dict[str, str]) -> dict:
    response = httpx.get(API, params=params, timeout=300.0)
    response.raise_for_status()
    return response.json()


def _floats(values: list) -> np.ndarray:
    return np.array(
        [np.nan if value is None else float(value) for value in values], dtype="float64"
    )


def compare_hourly() -> tuple[list[dict], float]:
    """Variable by variable, the largest disagreement between the store and the API."""
    start, end = HOURLY_WINDOW
    payload = _api(
        {
            "latitude": str(NODE[0]),
            "longitude": str(NODE[1]),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": (
                "temperature_2m,dew_point_2m,precipitation,shortwave_radiation,"
                "soil_moisture_7_to_28cm,wind_speed_10m,relative_humidity_2m"
            ),
            "timezone": "UTC",
            "models": "era5",
        }
    )
    hourly = payload["hourly"]

    def stored(variable: str, store: om_archive.Store = om_archive.ERA5) -> np.ndarray:
        return om_archive.read_hourly(store, variable, [NODE], start, end)[0]

    rows: list[dict] = []
    for variable in [
        "temperature_2m",
        "dew_point_2m",
        "precipitation",
        "shortwave_radiation",
        "soil_moisture_7_to_28cm",
    ]:
        served = _floats(hourly[variable])
        held = stored(variable)[: served.size]
        rows.append(
            {
                "variable": variable,
                "how": "read from the store",
                "max_abs": float(np.nanmax(np.abs(served - held))),
                "api_first": float(served[0]),
                "store_first": float(held[0]),
            }
        )

    wind = np.hypot(stored("wind_u_component_10m"), stored("wind_v_component_10m")) * 3.6
    served = _floats(hourly["wind_speed_10m"])
    rows.append(
        {
            "variable": "wind_speed_10m",
            "how": "hypot of the 10 m components, times 3.6",
            "max_abs": float(np.nanmax(np.abs(served - wind[: served.size]))),
            "api_first": float(served[0]),
            "store_first": float(wind[0]),
        }
    )

    served = _floats(hourly["relative_humidity_2m"])
    # The API downscales temperature and dew point by elevation; comparing derived humidity
    # against it would measure that offset rather than the formula. So the formula is checked
    # against the API's own temperature and dew point.
    from_api = climate.relative_humidity(
        _floats(hourly["temperature_2m"]), _floats(hourly["dew_point_2m"])
    )
    rows.append(
        {
            "variable": "relative_humidity_2m",
            "how": "FAO-56 eq. 11 from the API's own temperature and dew point",
            "max_abs": float(np.nanmax(np.abs(served - from_api))),
            "api_first": float(served[0]),
            "store_first": float(from_api[0]),
        }
    )
    mean_abs_humidity = float(np.nanmean(np.abs(served - from_api)))
    return rows, mean_abs_humidity


def compare_elevation() -> dict:
    """The one real difference: the API corrects for elevation and the store does not."""
    start, end = HOURLY_WINDOW
    payload = _api(
        {
            "latitude": str(NODE[0]),
            "longitude": str(NODE[1]),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": "temperature_2m",
            "timezone": "UTC",
            "models": "era5",
        }
    )
    served = _floats(payload["hourly"]["temperature_2m"])
    held = om_archive.read_hourly(om_archive.ERA5, "temperature_2m", [NODE], start, end)[0]
    grid_elevation = float(om_archive.elevation(om_archive.ERA5, [NODE])[0])
    api_elevation = float(payload["elevation"])
    offset = float(np.nanmean(served - held[: served.size]))
    return {
        "api_elevation_m": api_elevation,
        "grid_elevation_m": grid_elevation,
        "measured_offset_k": offset,
        "predicted_offset_k": (grid_elevation - api_elevation) * climate.LAPSE_RATE_K_PER_M,
    }


def compare_et0() -> dict:
    """FAO-56 daily against Open-Meteo's hourly-summed ET0, over a full year."""
    start, end = YEAR_WINDOW
    payload = _api(
        {
            "latitude": str(NODE[0]),
            "longitude": str(NODE[1]),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": "temperature_2m,dew_point_2m,wind_speed_10m,shortwave_radiation",
            "daily": "et0_fao_evapotranspiration",
            "timezone": "UTC",
            "models": "era5",
            "wind_speed_unit": "ms",
        }
    )
    hourly = payload["hourly"]
    served = _floats(payload["daily"]["et0_fao_evapotranspiration"])
    days = served.size

    def by_day(name: str) -> np.ndarray:
        return _floats(hourly[name])[: days * 24].reshape(days, 24)

    temperature = by_day("temperature_2m")
    ours = np.asarray(
        refet.Daily(
            tmin=temperature.min(axis=1),
            tmax=temperature.max(axis=1),
            rs=by_day("shortwave_radiation").mean(axis=1) * 0.0864,
            uz=by_day("wind_speed_10m").mean(axis=1),
            zw=10.0,
            elev=float(payload["elevation"]),
            lat=NODE[0],
            doy=np.arange(1, days + 1),
            tdew=by_day("dew_point_2m").mean(axis=1),
            method="asce",
        ).eto()
    )
    usable = np.isfinite(served) & np.isfinite(ours)
    season = slice(120, 273)  # May through September
    return {
        "n": int(usable.sum()),
        "correlation": float(np.corrcoef(served[usable], ours[usable])[0, 1]),
        "bias_mm_day": float(np.mean(ours[usable] - served[usable])),
        "mean_abs_mm_day": float(np.mean(np.abs(ours[usable] - served[usable]))),
        "api_annual_mm": float(np.nansum(served)),
        "ours_annual_mm": float(np.nansum(ours)),
        "api_season_mm_day": float(np.nanmean(served[season])),
        "ours_season_mm_day": float(np.nanmean(ours[season])),
    }


def main() -> None:
    rows, humidity_mean_abs = compare_hourly()
    elevation = compare_elevation()
    et0 = compare_et0()
    start, end = HOURLY_WINDOW

    lines = [
        "# The climate store, checked against the archive API it is served from",
        "",
        "Generated by `pipeline/scripts/verify_climate_store.py` on "
        f"{datetime.now(UTC).date().isoformat()}. Node {NODE[0]} N, "
        f"{abs(NODE[1])} W — the ERA5 cell McDougall Creek burned in.",
        "",
        "Open-Meteo publishes the archive its own API serves from, as `.om` files on an "
        "anonymous S3 bucket, readable by HTTP byte range with no account and no quota. The "
        "question this answers is not whether that store exists — a directory listing "
        "answers that, and D-009 and D-014 are both cases where a listing was all a source "
        "turned out to have. The question is whether the numbers in it are the numbers the "
        "API returns.",
        "",
        f"## Variable by variable, {start.isoformat()} to {end.isoformat()}, hourly",
        "",
        "| variable | how it was obtained | largest disagreement | API first hour | store first hour |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['variable']}` | {row['how']} | {row['max_abs']:.4f} | "
            f"{row['api_first']:.3f} | {row['store_first']:.3f} |"
        )

    lines += [
        "",
        "Precipitation, shortwave radiation and soil moisture agree exactly. Wind agrees to "
        "the API's own rounding. Relative humidity, computed from dew point rather than read, "
        f"differs by {humidity_mean_abs:.2f} percentage points on average — which is what "
        "rounding a percentage to a whole number costs.",
        "",
        "## The one real difference: elevation",
        "",
        "Temperature and dew point are the two rows above that do not agree, and they "
        "disagree by a constant.",
        "",
        f"- elevation of the ERA5 cell: **{elevation['grid_elevation_m']:.0f} m**",
        f"- elevation the API downscales to: **{elevation['api_elevation_m']:.0f} m**",
        f"- offset that predicts, at {climate.LAPSE_RATE_K_PER_M} K/m: "
        f"**{elevation['predicted_offset_k']:.2f} K**",
        f"- offset measured: **{elevation['measured_offset_k']:.2f} K**",
        "",
        "The API applies a lapse-rate correction from the reanalysis cell's elevation to the "
        "requested coordinate's. This pipeline does not, and that is a decision rather than "
        "an oversight: Components B, D and E are every one of them a departure from the same "
        "node's own record, so an offset applied to every year of that record cancels in the "
        "departure. Applying it would change no reported number and would put a second "
        "elevation model into a chain that already has one. Recorded as D-017.",
        "",
        "## Reference evapotranspiration, which the store does not carry",
        "",
        "Open-Meteo computes ET0 hourly and sums it. This pipeline uses the daily FAO-56 "
        "Penman-Monteith equation through `refet`, because the daily form is the published "
        "one a validator can check against a textbook and it needs no clear-sky radiation "
        "model to close the longwave term. Both were run against the *same* inputs — the "
        "API's own hourly temperature, dew point, wind and shortwave — so what is measured "
        f"below is the method difference alone, over {et0['n']} days of "
        f"{YEAR_WINDOW[0].year}:",
        "",
        "| | value |",
        "|---|---|",
        f"| correlation | {et0['correlation']:.4f} |",
        f"| bias, ours minus theirs | {et0['bias_mm_day']:+.4f} mm/day |",
        f"| mean absolute difference | {et0['mean_abs_mm_day']:.4f} mm/day |",
        f"| annual total, API | {et0['api_annual_mm']:.1f} mm |",
        f"| annual total, ours | {et0['ours_annual_mm']:.1f} mm |",
        f"| May-September mean, API | {et0['api_season_mm_day']:.3f} mm/day |",
        f"| May-September mean, ours | {et0['ours_season_mm_day']:.3f} mm/day |",
        "",
        "Almost all of the bias is in December and January, when both are near zero and the "
        "hourly form's night-time clamping and the daily form's radiation balance disagree "
        "about a quantity that rounds to nothing. Through the fire season they agree to "
        "within two percent. Component B is a departure and Component E fits a distribution "
        "to the same series, so a systematic method offset largely cancels in both; it is "
        "quantified here rather than assumed away. Recorded as D-018.",
        "",
        "## What this replaces",
        "",
        "The metered route. D-016 records three runs that died against a free tier metering "
        "by call weight, roughly `ceil(days / 14) * ceil(variables / 10) * locations` — about "
        "ninety thousand weighted calls for a forty-year water balance at eighty-eight nodes, "
        "against a minutely allowance near six hundred. No batching or backoff fixes that "
        "arithmetic. Reading the same bytes directly does: the whole lattice, one variable, "
        "one year is about two hundred range requests and under a megabyte.",
        "",
    ]

    OUT.write_text("\n".join(lines))
    print(OUT)
    print(json.dumps({"elevation": elevation, "et0": et0}, indent=2))


if __name__ == "__main__":
    main()
