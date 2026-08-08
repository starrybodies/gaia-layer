"""Climate and soil moisture ingestion from ERA5-Land.

Four indicators per month, all of them the atmospheric and soil half of the substrate
picture that satellite reflectance cannot see: how hard the air was pulling water out of
vegetation, how hot it got, how much rain fell, how long since it last did, and what the
soil was holding.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime
from statistics import mean

from ..config import AreaOfInterest, geometry_hash
from ..provenance import ChainBuilder
from ..schemas.common import UNITS, DateRange, IndicatorId, indicator_family
from ..schemas.provenance import Method
from ..sources.openmeteo import (
    ACCESS_ROUTE,
    DATASET,
    SOURCE,
    DailyRecord,
    PointSeries,
    fetch_point,
    sample_points,
    vapour_pressure_deficit_kpa,
)
from ..store import lake
from ..validation import ValidationContext, validate_value
from ..version import ALGORITHM_VERSION, PIPELINE_VERSION
from .sentinel2 import months_between

log = logging.getLogger(__name__)

VPD_METHOD = Method(
    name="Vapour pressure deficit from ERA5-Land temperature and dew point",
    citation=(
        "Tetens, O. (1930). Uber einige meteorologische Begriffe. Zeitschrift fur "
        "Geophysik, 6, 297-309. Applied to ERA5-Land 2 m temperature and 2 m dew point: "
        "Munoz-Sabater, J. et al. (2021). ERA5-Land: a state-of-the-art global reanalysis "
        "dataset for land applications. Earth System Science Data, 13, 4349-4383. "
        "doi:10.5194/essd-13-4349-2021"
    ),
    doi="10.5194/essd-13-4349-2021",
    formula="VPD = es(T_air) - es(T_dew), es(T) = 0.6108 exp(17.27 T / (T + 237.3))",
    notes=(
        "Atmospheric demand for water. The daily maximum-temperature VPD is used rather "
        "than the daily mean, because fuel dries fastest in the afternoon and it is the "
        "afternoon that carries fire."
    ),
)

ERA5_METHOD = Method(
    name="ERA5-Land reanalysis",
    citation=(
        "Munoz-Sabater, J. et al. (2021). ERA5-Land: a state-of-the-art global reanalysis "
        "dataset for land applications. Earth System Science Data, 13, 4349-4383. "
        "doi:10.5194/essd-13-4349-2021"
    ),
    doi="10.5194/essd-13-4349-2021",
    notes=(
        "Accessed through the Open-Meteo historical archive rather than the Copernicus "
        "Climate Data Store; see docs/DIVERGENCES.md D-002. Roughly 9 km native grid, "
        "sampled here on a lattice across the area and averaged."
    ),
)

DRY_DAY_MM = 1.0


def _monthly_values(
    series: list[PointSeries], period: tuple[date, date]
) -> dict[IndicatorId, float]:
    """Reduce daily records across the sample lattice to one value per indicator."""
    in_period: dict[float, list[DailyRecord]] = defaultdict(list)
    for point in series:
        for record in point.records:
            if period[0] <= record.day <= period[1]:
                in_period[point.latitude + point.longitude].append(record)

    if not in_period:
        return {}

    vpds: list[float] = []
    temp_maxes: list[float] = []
    precip_totals: list[float] = []
    dry_runs: list[float] = []
    soil_shallow: list[float] = []
    soil_deep: list[float] = []

    for records in in_period.values():
        ordered = sorted(records, key=lambda r: r.day)

        daily_vpd = [
            vapour_pressure_deficit_kpa(r.temp_max_c, r.dew_point_c)
            for r in ordered
            if r.temp_max_c is not None and r.dew_point_c is not None
        ]
        if daily_vpd:
            vpds.append(mean(daily_vpd))

        maxes = [r.temp_max_c for r in ordered if r.temp_max_c is not None]
        if maxes:
            temp_maxes.append(max(maxes))

        rain = [r.precip_mm for r in ordered if r.precip_mm is not None]
        if rain:
            precip_totals.append(sum(rain))

        # Days since measurable rain, counted back from the end of the period. A month that
        # ends in a three-week dry spell is a different fire proposition from one with the
        # same total that ended in a downpour, and the monthly sum alone cannot tell them
        # apart.
        run = 0
        for record in reversed(ordered):
            if record.precip_mm is not None and record.precip_mm >= DRY_DAY_MM:
                break
            run += 1
        dry_runs.append(float(run))

        shallow = [r.soil_moisture_0_7 for r in ordered if r.soil_moisture_0_7 is not None]
        if shallow:
            soil_shallow.append(mean(shallow))
        deep = [r.soil_moisture_7_28 for r in ordered if r.soil_moisture_7_28 is not None]
        if deep:
            soil_deep.append(mean(deep))

    out: dict[IndicatorId, float] = {}
    if vpds:
        out[IndicatorId.VPD_KPA] = mean(vpds)
    if temp_maxes:
        out[IndicatorId.TEMP_MAX_C] = mean(temp_maxes)
    if precip_totals:
        out[IndicatorId.PRECIP_30D_MM] = mean(precip_totals)
    if dry_runs:
        out[IndicatorId.DAYS_SINCE_RAIN] = mean(dry_runs)
    if soil_shallow:
        out[IndicatorId.SOIL_MOISTURE_0_7CM] = mean(soil_shallow)
    if soil_deep:
        out[IndicatorId.SOIL_MOISTURE_7_28CM] = mean(soil_deep)
    return out


def ingest(aoi: AreaOfInterest, start: date, end: date, *, force: bool = False) -> int:
    """Monthly climate and soil moisture indicators for an area over a date range."""
    ghash = geometry_hash(aoi.geometry)
    conn = lake.connect()

    points = sample_points(aoi.bbox().as_tuple())
    run_id = lake.start_run(
        conn,
        aoi_id=aoi.aoi_id,
        command="ingest climate",
        parameters={"start": start, "end": end, "sample_points": len(points)},
    )

    written = 0
    try:
        log.info("fetching ERA5-Land at %d sample points", len(points))
        series = [fetch_point(lat, lon, start, end) for lat, lon in points]
        for point in series:
            lake.record_observation(
                conn,
                observation_id=point.observation_id,
                source=SOURCE,
                dataset_id=DATASET,
                access_route=ACCESS_ROUTE,
                asset_id=f"{point.latitude:.4f},{point.longitude:.4f}",
                acquired_at=None,
                spatial_ref="EPSG:4326",
                resolution_m=9000.0,
                url="https://archive-api.open-meteo.com/v1/archive",
                metadata={"elevation_m": point.elevation_m},
            )

        for period in months_between(start, end):
            values = _monthly_values(series, period)
            if not values:
                log.warning("no ERA5-Land records for %s", period[0].strftime("%Y-%m"))
                continue

            for indicator, value in values.items():
                if not force and period in lake.existing_periods(conn, aoi.aoi_id, indicator):
                    continue

                method = VPD_METHOD if indicator is IndicatorId.VPD_KPA else ERA5_METHOD
                report = validate_value(
                    ValidationContext(
                        indicator=indicator,
                        value=value,
                        period=DateRange(start=period[0], end=period[1]),
                        history=lake.history_for(conn, ghash, indicator, period[0]),
                        covariates=lake.covariates_for(
                            conn, ghash, period[0], period[1], exclude=indicator
                        ),
                        observation_count=len(points),
                        # Reanalysis is a model product, not an observation through an
                        # atmosphere, so cloud does not apply and the gap is one day.
                        cloud_fraction=0.0,
                        revisit_gap_days=1.0,
                        spatial_coverage=1.0,
                    )
                )

                chain = ChainBuilder("EPSG:4326", 9000.0)
                chain.observation(
                    description=(
                        f"ERA5-Land daily fields at {len(points)} sample points across the "
                        f"area for {period[0].strftime('%Y-%m')}."
                    ),
                    source=SOURCE,
                    dataset_id=DATASET,
                    access_route=ACCESS_ROUTE,
                    asset_ids=[p.observation_id for p in series],
                    acquired_at=None,
                    parameters={
                        "variables": sorted(
                            {
                                "temperature_2m_max",
                                "dew_point_2m_mean",
                                "precipitation_sum",
                                "soil_moisture_0_to_7cm_mean",
                                "soil_moisture_7_to_28cm_mean",
                            }
                        )
                    },
                )
                if indicator is IndicatorId.VPD_KPA:
                    chain.processing(
                        description=(
                            "Daily vapour pressure deficit from maximum temperature and mean "
                            "dew point by the Tetens equation, then averaged over the month."
                        ),
                        software=f"gaia-pipeline {PIPELINE_VERSION}",
                        parameters={"formula": VPD_METHOD.formula},
                    )
                elif indicator is IndicatorId.DAYS_SINCE_RAIN:
                    chain.processing(
                        description=(
                            "Consecutive days to the end of the period with less than "
                            f"{DRY_DAY_MM:g} mm of precipitation."
                        ),
                        software=f"gaia-pipeline {PIPELINE_VERSION}",
                        parameters={"dry_day_threshold_mm": DRY_DAY_MM},
                    )
                else:
                    chain.processing(
                        description="Daily values reduced over the month, then averaged across sample points.",
                        software=f"gaia-pipeline {PIPELINE_VERSION}",
                        parameters={
                            "monthly_reducer": "sum"
                            if indicator is IndicatorId.PRECIP_30D_MM
                            else "max"
                            if indicator is IndicatorId.TEMP_MAX_C
                            else "mean"
                        },
                    )
                chain.validation(
                    description="Constraint engine applied.",
                    parameters={
                        "status": report.status,
                        "flag_codes": [f.code for f in report.flags],
                    },
                )

                rejected = report.status == "rejected"
                lake.upsert_indicator_value(
                    conn,
                    {
                        "value_id": lake.value_id_for(
                            aoi.aoi_id, ghash, indicator, period[0], period[1]
                        ),
                        "run_id": run_id,
                        "aoi_id": aoi.aoi_id,
                        "geometry_hash": ghash,
                        "indicator": indicator.value,
                        "family": indicator_family(indicator).value,
                        "unit": UNITS[indicator],
                        "period_start": period[0],
                        "period_end": period[1],
                        "value": None if rejected else value,
                        "mean": value,
                        "median": value,
                        "std": 0.0,
                        "p10": value,
                        "p90": value,
                        "minimum": value,
                        "maximum": value,
                        "valid_pixels": len(points),
                        "total_pixels": len(points),
                        "validation_status": report.status,
                        "confidence": report.confidence,
                        "confidence_basis_json": report.confidence_basis.model_dump_json(),
                        "flags_json": json.dumps([f.model_dump(mode="json") for f in report.flags]),
                        "constraints_checked": json.dumps(report.constraints_checked),
                        "method_json": method.model_dump_json(),
                        "provenance_json": json.dumps(
                            [s.model_dump(mode="json") for s in chain.build()]
                        ),
                        "observation_ids": json.dumps([p.observation_id for p in series]),
                        "raster_path": None,
                        "source": SOURCE,
                        "dataset_id": DATASET,
                        "access_route": ACCESS_ROUTE,
                        "spatial_ref": "EPSG:4326",
                        "resolution_m": 9000.0,
                        "pipeline_version": PIPELINE_VERSION,
                        "algorithm_version": ALGORITHM_VERSION,
                        "computed_at": datetime.now().astimezone(),
                    },
                )
                written += 1

        lake.finish_run(conn, run_id, status="ok", input_ids=[p.observation_id for p in series])
        return written
    except Exception as exc:
        lake.finish_run(conn, run_id, status="failed", error=str(exc))
        raise
    finally:
        conn.close()
