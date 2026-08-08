"""Terrain ingestion from the Copernicus 30 m digital elevation model.

Terrain is static, so this runs once per area rather than per month. Its values are stamped
with a period covering the whole ingest window so they join the other indicators cleanly,
and the constraint engine knows not to apply rate checks to them.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime

import numpy as np
from pystac_client import Client

from ..config import AreaOfInterest, geometry_hash, settings
from ..grid import AnalysisGrid, grid_for
from ..indices.terrain import (
    SLOPE_METHOD,
    TWI_METHOD,
    slope_and_aspect,
    topographic_wetness_index,
)
from ..provenance import ChainBuilder
from ..raster import read_window, write_cog
from ..schemas.common import UNITS, DateRange, IndicatorId, indicator_family
from ..sources.stac import EARTH_SEARCH_URL
from ..store import lake
from ..validation import ValidationContext, validate_value
from ..version import ALGORITHM_VERSION, PIPELINE_VERSION

log = logging.getLogger(__name__)

COLLECTION = "cop-dem-glo-30"
SOURCE = "ESA/Copernicus"
ACCESS_ROUTE = "earth-search-v1"

# The topographic wetness index needs flow routed downhill across the whole area, which is
# a sequential walk over every cell. At 20 m that is ten million steps in Python; at 100 m
# it is four hundred thousand and takes seconds. TWI describes hillslope position rather
# than fine texture, so the coarser grid costs little — and computing it honestly at 100 m
# is better than approximating it badly at 20 m.
TWI_COARSENING = 5


def _https(href: str) -> str:
    """Rewrite an S3 URI to its public HTTPS equivalent.

    The DEM lives in a public bucket, but the STAC item advertises `s3://`, which GDAL will
    only follow with credentials configured. The HTTPS form needs none.
    """
    if href.startswith("s3://"):
        bucket, _, key = href[5:].partition("/")
        return f"https://{bucket}.s3.amazonaws.com/{key}"
    return href


def _coarsen(array: np.ndarray, factor: int) -> np.ndarray:
    rows = array.shape[0] // factor
    cols = array.shape[1] // factor
    trimmed = array[: rows * factor, : cols * factor]
    with np.errstate(invalid="ignore"):
        return np.asarray(
            np.nanmean(trimmed.reshape(rows, factor, cols, factor), axis=(1, 3)), dtype="float32"
        )


def _expand(array: np.ndarray, factor: int, shape: tuple[int, int]) -> np.ndarray:
    expanded = np.repeat(np.repeat(array, factor, axis=0), factor, axis=1)
    out = np.full(shape, np.nan, dtype="float32")
    rows = min(shape[0], expanded.shape[0])
    cols = min(shape[1], expanded.shape[1])
    out[:rows, :cols] = expanded[:rows, :cols]
    return out


def _fetch_elevation(aoi: AreaOfInterest, grid: AnalysisGrid) -> tuple[np.ndarray, list[str]]:
    client = Client.open(EARTH_SEARCH_URL)
    items = list(client.search(collections=[COLLECTION], bbox=list(aoi.bbox().as_tuple())).items())
    if not items:
        raise RuntimeError(f"no {COLLECTION} tiles intersect {aoi.aoi_id}")

    mosaic = np.full(grid.shape, np.nan, dtype="float32")
    asset_ids: list[str] = []
    for item in items:
        asset = item.assets.get("data") or next(iter(item.assets.values()))
        href = _https(asset.href)
        asset_ids.append(href)
        tile = read_window(href, grid)
        mosaic = np.where(np.isfinite(mosaic), mosaic, tile)

    return mosaic, asset_ids


def ingest(aoi: AreaOfInterest, *, force: bool = False) -> int:
    """Elevation, slope, aspect and topographic wetness index for an area."""
    grid = grid_for(aoi)
    ghash = geometry_hash(aoi.geometry)
    conn = lake.connect()

    period_start, period_end = date(2000, 1, 1), date(2100, 1, 1)
    run_id = lake.start_run(
        conn,
        aoi_id=aoi.aoi_id,
        command="ingest terrain",
        parameters={"collection": COLLECTION, "twi_coarsening": TWI_COARSENING},
    )

    written = 0
    try:
        existing = lake.existing_periods(conn, aoi.aoi_id, IndicatorId.SLOPE_DEG)
        if existing and not force:
            log.info("terrain already ingested for %s", aoi.aoi_id)
            lake.finish_run(conn, run_id, status="ok")
            return 0

        log.info("reading Copernicus DEM")
        elevation, asset_ids = _fetch_elevation(aoi, grid)
        if not np.isfinite(elevation).any():
            raise RuntimeError("the elevation mosaic is empty over this area")

        # The DEM reports sea surface as zero, which would otherwise make the strait look
        # like flat, maximally wet land.
        land = elevation > 0.5
        elevation = np.where(land, elevation, np.nan)

        slope, aspect = slope_and_aspect(elevation, grid.resolution_m)

        log.info("routing flow at %g m for the wetness index", grid.resolution_m * TWI_COARSENING)
        coarse_elevation = _coarsen(elevation, TWI_COARSENING)
        coarse_slope, _ = slope_and_aspect(coarse_elevation, grid.resolution_m * TWI_COARSENING)
        coarse_twi = topographic_wetness_index(
            coarse_elevation, coarse_slope, grid.resolution_m * TWI_COARSENING
        )
        twi = _expand(coarse_twi, TWI_COARSENING, grid.shape)

        for indicator, array, method in (
            (IndicatorId.ELEVATION_M, elevation, SLOPE_METHOD),
            (IndicatorId.SLOPE_DEG, slope, SLOPE_METHOD),
            (IndicatorId.ASPECT_DEG, aspect, SLOPE_METHOD),
            (IndicatorId.TWI, twi, TWI_METHOD),
        ):
            valid = np.isfinite(array) & land
            if not valid.any():
                log.warning("no valid pixels for %s", indicator.value)
                continue
            sample = array[valid]

            path = settings().raster_dir / aoi.aoi_id / indicator.value / "static.tif"
            write_cog(path, np.where(land, array, np.nan), grid, description=method.name)

            chain = ChainBuilder(grid.crs, grid.resolution_m)
            chain.observation(
                description=(
                    "Copernicus DEM GLO-30 tiles covering the area, read as a windowed "
                    "mosaic and reprojected to the analysis grid."
                ),
                source=SOURCE,
                dataset_id=COLLECTION,
                access_route=ACCESS_ROUTE,
                asset_ids=asset_ids,
                acquired_at=None,
                spatial_ref="EPSG:4326",
                resolution_m=30.0,
            )
            chain.processing(
                description="Sea surface removed by discarding elevations at or below 0.5 m.",
                parameters={"land_threshold_m": 0.5},
            )
            if indicator is IndicatorId.TWI:
                chain.processing(
                    description=(
                        f"Elevation block-averaged to {grid.resolution_m * TWI_COARSENING:g} m, "
                        "flow accumulated by D8 routing, index computed and resampled back to "
                        "the analysis grid."
                    ),
                    parameters={
                        "coarsening_factor": TWI_COARSENING,
                        "routing": "D8",
                        "formula": TWI_METHOD.formula,
                    },
                )
            elif indicator in {IndicatorId.SLOPE_DEG, IndicatorId.ASPECT_DEG}:
                chain.processing(
                    description=f"{method.name} from the eight neighbours of each cell.",
                    parameters={"formula": method.formula, "cell_size_m": grid.resolution_m},
                )

            mean_value = float(np.mean(sample))
            report = validate_value(
                ValidationContext(
                    indicator=indicator,
                    value=mean_value,
                    period=DateRange(start=period_start, end=period_end),
                    history=[],
                    covariates={},
                    observation_count=len(asset_ids),
                    cloud_fraction=0.0,
                    revisit_gap_days=0.0,
                    spatial_coverage=float(valid.sum()) / float(max(1, int(land.sum()))),
                )
            )
            chain.validation(
                description=(
                    "Constraint engine applied. Terrain is exempt from rate-of-change checks: "
                    "slope does not move between months, so a difference would mean the "
                    "elevation model was replaced."
                ),
                parameters={"status": report.status, "flag_codes": [f.code for f in report.flags]},
            )

            rejected = report.status == "rejected"
            lake.upsert_indicator_value(
                conn,
                {
                    "value_id": lake.value_id_for(
                        aoi.aoi_id, ghash, indicator, period_start, period_end
                    ),
                    "run_id": run_id,
                    "aoi_id": aoi.aoi_id,
                    "geometry_hash": ghash,
                    "indicator": indicator.value,
                    "family": indicator_family(indicator).value,
                    "unit": UNITS[indicator],
                    "period_start": period_start,
                    "period_end": period_end,
                    "value": None if rejected else mean_value,
                    "mean": mean_value,
                    "median": float(np.median(sample)),
                    "std": float(np.std(sample)),
                    "p10": float(np.percentile(sample, 10)),
                    "p90": float(np.percentile(sample, 90)),
                    "minimum": float(np.min(sample)),
                    "maximum": float(np.max(sample)),
                    "valid_pixels": int(valid.sum()),
                    "total_pixels": int(land.sum()),
                    "validation_status": report.status,
                    "confidence": report.confidence,
                    "confidence_basis_json": report.confidence_basis.model_dump_json(),
                    "flags_json": json.dumps([f.model_dump(mode="json") for f in report.flags]),
                    "constraints_checked": json.dumps(report.constraints_checked),
                    "method_json": method.model_dump_json(),
                    "provenance_json": json.dumps(
                        [s.model_dump(mode="json") for s in chain.build()]
                    ),
                    "observation_ids": json.dumps([f"{COLLECTION}:{a}" for a in asset_ids]),
                    "raster_path": str(path.relative_to(settings().data_dir)),
                    "source": SOURCE,
                    "dataset_id": COLLECTION,
                    "access_route": ACCESS_ROUTE,
                    "spatial_ref": grid.crs,
                    "resolution_m": grid.resolution_m,
                    "pipeline_version": PIPELINE_VERSION,
                    "algorithm_version": ALGORITHM_VERSION,
                    "computed_at": datetime.now().astimezone(),
                },
            )
            for asset in asset_ids:
                lake.record_observation(
                    conn,
                    observation_id=f"{COLLECTION}:{asset}",
                    source=SOURCE,
                    dataset_id=COLLECTION,
                    access_route=ACCESS_ROUTE,
                    asset_id=asset.rsplit("/", 1)[-1],
                    acquired_at=None,
                    spatial_ref="EPSG:4326",
                    resolution_m=30.0,
                    url=asset,
                )
            written += 1

        lake.finish_run(conn, run_id, status="ok", input_ids=asset_ids)
        return written
    except Exception as exc:
        lake.finish_run(conn, run_id, status="failed", error=str(exc))
        raise
    finally:
        conn.close()
