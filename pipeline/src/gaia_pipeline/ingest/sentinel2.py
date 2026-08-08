"""Sentinel-2 ingestion: monthly cloud-masked composites of NDVI, NDMI and NBR.

Two passes. The first reaches the network: for each month it selects the least-cloudy
scenes, reads only the analysis-grid window of each asset, masks cloud from the scene
classification layer, computes the three indices per scene and reduces them to a monthly
median composite written as a COG. The second pass touches only local disk: it applies the
land mask accumulated during the first, computes statistics, runs every value through the
validation engine, and writes rows.

Splitting it this way means the land mask can be derived from the whole ingest window
without reading anything twice.
"""

from __future__ import annotations

import calendar
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path

import numpy as np
from rasterio.enums import Resampling

from ..config import AreaOfInterest, geometry_hash, settings
from ..grid import AnalysisGrid, grid_for
from ..indices.spectral import (
    ASSET_SCL,
    SPECTRAL_INDICES,
    SpectralIndex,
    clear_land_mask,
    normalised_difference,
    to_reflectance,
    water_mask,
)
from ..provenance import ChainBuilder
from ..raster import read_cog, read_window, write_cog
from ..schemas.common import UNITS, IndicatorId, indicator_family
from ..schemas.provenance import ProvenanceStep
from ..sources.stac import ACCESS_ROUTE, COLLECTION, SOURCE, Scene, search_scenes, select_for_month
from ..store import lake
from ..validation import ValidationContext, validate_value
from ..version import ALGORITHM_VERSION, PIPELINE_VERSION

log = logging.getLogger(__name__)

# A pixel counts as water when it was classified water in at least this share of the
# observations that had any data there, and land otherwise.
#
# Phrased around water rather than land on purpose. Defining land as "clear and not water"
# would make the mask a function of cloudiness: a pixel obscured in every scene would drop
# out of the denominator, spatial coverage would come out at 1.0 for every period, and
# confidence would be measuring nothing. Counting water votes instead leaves permanently
# clouded land in the mask, where it correctly lowers the coverage score.
WATER_VOTE_THRESHOLD = 0.5

# Cell grid for the console map. 500 m keeps a click meaningful at parcel scale while
# holding the row count per layer in the low thousands.
CELL_SIZE_M = 500.0


@dataclass
class MonthResult:
    period: tuple[date, date]
    scenes: list[Scene]
    cloud_fraction: float
    revisit_gap_days: float
    composites: dict[IndicatorId, Path] = field(default_factory=dict)


@dataclass
class IngestSummary:
    aoi_id: str
    run_id: str
    months_processed: int
    months_skipped: int
    values_written: int
    values_rejected: int
    scenes_used: int


def months_between(start: date, end: date) -> list[tuple[date, date]]:
    """Whole calendar months overlapping [start, end], clipped to the range."""
    out: list[tuple[date, date]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        out.append((max(first, start), min(last, end)))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def _read_scene_indices(
    scene: Scene, grid: AnalysisGrid
) -> tuple[dict[IndicatorId, np.ndarray], np.ndarray, np.ndarray, float]:
    """Read one scene and return its indices, its water mask, its data footprint, cloud share.

    The five asset reads run concurrently. They are independent HTTP range requests against
    different files, so the wall clock is one read rather than five.
    """
    needed = {ASSET_SCL}
    for index in SPECTRAL_INDICES:
        needed.update(index.assets)

    def fetch(asset: str) -> tuple[str, np.ndarray]:
        resampling = Resampling.nearest if asset == ASSET_SCL else Resampling.average
        return asset, read_window(scene.assets[asset], grid, resampling=resampling)

    with ThreadPoolExecutor(max_workers=len(needed)) as pool:
        bands = dict(pool.map(fetch, sorted(needed)))

    scl = bands.pop(ASSET_SCL)
    clear = clear_land_mask(scl)
    water = water_mask(scl)
    has_data = np.isfinite(scl) & (np.rint(scl) > 0)

    land_seen = has_data & ~water
    cloud_fraction = float(1.0 - clear.sum() / land_seen.sum()) if land_seen.sum() > 0 else 1.0

    reflectance = {asset: to_reflectance(array, scene.boa_offset) for asset, array in bands.items()}

    indices: dict[IndicatorId, np.ndarray] = {}
    for spec in SPECTRAL_INDICES:
        value = normalised_difference(
            reflectance[spec.numerator_asset], reflectance[spec.denominator_asset]
        )
        indices[spec.indicator] = np.where(clear, value, np.nan).astype("float32")

    return indices, water, has_data, max(0.0, min(1.0, cloud_fraction))


def _composite(stack: list[np.ndarray]) -> np.ndarray:
    """Per-pixel median across scenes, ignoring masked observations.

    Median rather than mean: residual cloud that survives the classification mask is bright
    and would drag a mean, while a median of three tolerates one bad observation.
    """
    if not stack:
        raise ValueError("cannot composite an empty stack")
    with np.errstate(invalid="ignore"):
        return np.asarray(np.nanmedian(np.stack(stack, axis=0), axis=0), dtype="float32")


def _revisit_gap_days(scenes: list[Scene], period: tuple[date, date]) -> float:
    """Longest gap between contributing observations.

    A single scene has no gap between observations, so the honest figure is the span of the
    period it is standing in for — one moment of data claiming to describe a month.
    """
    days = sorted({s.acquired_at.date() for s in scenes})
    if len(days) < 2:
        return float((period[1] - period[0]).days + 1)
    return float(max((b - a).days for a, b in pairwise(days)))


def _raster_path(aoi_id: str, indicator: IndicatorId, period_start: date) -> Path:
    return (
        settings().raster_dir / aoi_id / indicator.value / f"{period_start.strftime('%Y-%m')}.tif"
    )


def _spatial_stats(values: np.ndarray, land: np.ndarray) -> dict[str, float] | None:
    valid = np.isfinite(values) & land
    count = int(valid.sum())
    if count == 0:
        return None
    sample = values[valid]
    return {
        "mean": float(np.mean(sample)),
        "median": float(np.median(sample)),
        "std": float(np.std(sample)),
        "p10": float(np.percentile(sample, 10)),
        "p90": float(np.percentile(sample, 90)),
        "minimum": float(np.min(sample)),
        "maximum": float(np.max(sample)),
        "valid_pixels": count,
        "total_pixels": int(land.sum()),
    }


def _cell_rows(
    values: np.ndarray,
    land: np.ndarray,
    grid: AnalysisGrid,
    *,
    value_id: str,
    aoi_id: str,
    indicator: IndicatorId,
    period: tuple[date, date],
    confidence: float,
) -> list[dict[str, object]]:
    """Aggregate the raster to map cells, in WGS84, keeping only cells with real data."""
    from pyproj import Transformer

    block = max(1, round(CELL_SIZE_M / grid.resolution_m))
    rows_n = grid.height // block
    cols_n = grid.width // block
    if rows_n == 0 or cols_n == 0:
        return []

    trimmed = values[: rows_n * block, : cols_n * block]
    land_trimmed = land[: rows_n * block, : cols_n * block]
    masked = np.where(land_trimmed & np.isfinite(trimmed), trimmed, np.nan)

    blocks = masked.reshape(rows_n, block, cols_n, block)
    with np.errstate(invalid="ignore"):
        means = np.nanmean(blocks, axis=(1, 3))
    valid_counts = np.isfinite(blocks).sum(axis=(1, 3))
    valid_fraction = valid_counts / float(block * block)

    transformer = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    cell_m = block * grid.resolution_m

    out: list[dict[str, object]] = []
    for r in range(rows_n):
        for c in range(cols_n):
            if not np.isfinite(means[r, c]):
                continue
            left = grid.left + c * cell_m
            top = grid.top - r * cell_m
            west, south = transformer.transform(left, top - cell_m)
            east, north = transformer.transform(left + cell_m, top)
            out.append(
                {
                    "cell_id": f"{value_id}:{r}:{c}",
                    "value_id": value_id,
                    "aoi_id": aoi_id,
                    "indicator": indicator.value,
                    "period_start": period[0],
                    "period_end": period[1],
                    "west": west,
                    "south": south,
                    "east": east,
                    "north": north,
                    "value": float(means[r, c]),
                    "valid_fraction": float(valid_fraction[r, c]),
                    "confidence": confidence,
                    "cell_size_m": cell_m,
                }
            )
    return out


def _build_chain(
    spec: SpectralIndex,
    scenes: list[Scene],
    grid: AnalysisGrid,
    *,
    cloud_fraction: float,
    water_threshold: float,
    validation_summary: dict[str, object],
) -> list[ProvenanceStep]:
    chain = ChainBuilder(grid.crs, grid.resolution_m)
    for scene in sorted(scenes, key=lambda s: s.acquired_at):
        chain.observation(
            description=(
                f"Sentinel-2 L2A scene {scene.item_id} over MGRS tile {scene.tile}, "
                f"{scene.cloud_cover:.1f}% scene cloud cover."
            ),
            source=SOURCE,
            dataset_id=COLLECTION,
            access_route=ACCESS_ROUTE,
            asset_ids=[scene.assets[a] for a in spec.assets] + [scene.assets[ASSET_SCL]],
            acquired_at=scene.acquired_at,
            spatial_ref=f"EPSG:{scene.epsg}",
            parameters={
                "processing_baseline": scene.processing_baseline,
                "boa_offset_applied": scene.boa_offset_applied,
                "boa_offset": scene.boa_offset,
            },
        )

    chain.processing(
        description=(
            "Windowed read of the area of interest from each cloud-optimised GeoTIFF, "
            "resampled to the analysis grid by area-weighted averaging."
        ),
        parameters={
            "resampling": "average",
            "grid_width": grid.width,
            "grid_height": grid.height,
            "bounds": list(grid.bounds),
        },
    )
    chain.processing(
        description=(
            "Cloud, shadow, snow and water removed using the scene classification layer; "
            "classes 4, 5 and 7 retained."
        ),
        parameters={"retained_scl_classes": [4, 5, 7], "resampling": "nearest"},
    )
    chain.processing(
        description=f"{spec.method.name} computed per scene as {spec.method.formula}.",
        parameters={
            "formula": spec.method.formula,
            "numerator_asset": spec.numerator_asset,
            "denominator_asset": spec.denominator_asset,
        },
    )
    chain.processing(
        description=(
            f"Per-pixel median across {len(scenes)} masked scenes, giving the monthly "
            "composite. Mean scene cloud fraction over land was "
            f"{cloud_fraction:.3f}."
        ),
        parameters={"reducer": "median", "scene_count": len(scenes)},
    )
    chain.processing(
        description=(
            "Restricted to land pixels, where a pixel is water when it was classified water "
            f"in at least {water_threshold:.0%} of the observations that saw it."
        ),
        parameters={"water_vote_threshold": water_threshold},
    )
    chain.validation(
        description=(
            "Constraint engine applied: physical bounds, plausible range, temporal "
            "consistency and cross-variable coherence."
        ),
        parameters=validation_summary,
    )
    return chain.build()


def ingest(
    aoi: AreaOfInterest,
    start: date,
    end: date,
    *,
    force: bool = False,
    max_scenes_per_tile: int = 3,
    db_path: Path | None = None,
) -> IngestSummary:
    """Ingest Sentinel-2 spectral indicators for an area over a date range."""
    grid = grid_for(aoi)
    ghash = geometry_hash(aoi.geometry)
    conn = lake.connect(db_path)

    area_km2 = grid.pixel_count * grid.pixel_area_km2()
    lake.register_aoi(conn, aoi, area_km2)

    run_id = lake.start_run(
        conn,
        aoi_id=aoi.aoi_id,
        command="ingest sentinel2",
        parameters={
            "start": start,
            "end": end,
            "max_scenes_per_tile": max_scenes_per_tile,
            "grid": {"crs": grid.crs, "width": grid.width, "height": grid.height},
        },
    )

    input_ids: list[str] = []
    output_ids: list[str] = []
    months_skipped = 0
    results: list[MonthResult] = []

    water_votes = np.zeros(grid.shape, dtype=np.int32)
    observed_votes = np.zeros(grid.shape, dtype=np.int32)

    try:
        already = {
            indicator: lake.existing_periods(conn, aoi.aoi_id, indicator)
            for indicator in (spec.indicator for spec in SPECTRAL_INDICES)
        }

        # ---------------------------------------------------------- pass 1: network
        for period in months_between(start, end):
            if not force and all(period in already[spec.indicator] for spec in SPECTRAL_INDICES):
                log.info("skipping %s, already ingested", period[0].strftime("%Y-%m"))
                months_skipped += 1
                continue

            scenes = select_for_month(
                search_scenes(aoi.bbox().as_tuple(), period[0], period[1]),
                max_scenes_per_tile,
            )
            if not scenes:
                log.warning("no usable scenes for %s", period[0].strftime("%Y-%m"))
                continue

            log.info("%s: compositing %d scenes", period[0].strftime("%Y-%m"), len(scenes))

            stacks: dict[IndicatorId, list[np.ndarray]] = {
                spec.indicator: [] for spec in SPECTRAL_INDICES
            }
            cloud_fractions: list[float] = []

            for scene in scenes:
                indices, water, has_data, cloud_fraction = _read_scene_indices(scene, grid)
                for indicator, array in indices.items():
                    stacks[indicator].append(array)
                cloud_fractions.append(cloud_fraction)

                observed_votes += has_data.astype(np.int32)
                water_votes += water.astype(np.int32)

                input_ids.append(scene.observation_id)
                lake.record_observation(
                    conn,
                    observation_id=scene.observation_id,
                    source=SOURCE,
                    dataset_id=COLLECTION,
                    access_route=ACCESS_ROUTE,
                    asset_id=scene.item_id,
                    acquired_at=scene.acquired_at,
                    spatial_ref=f"EPSG:{scene.epsg}",
                    resolution_m=grid.resolution_m,
                    cloud_cover=scene.cloud_cover / 100.0,
                    url=scene.assets.get("nir08"),
                    metadata={
                        "tile": scene.tile,
                        "processing_baseline": scene.processing_baseline,
                        "boa_offset_applied": scene.boa_offset_applied,
                    },
                )

            result = MonthResult(
                period=period,
                scenes=scenes,
                cloud_fraction=float(np.mean(cloud_fractions)) if cloud_fractions else 1.0,
                revisit_gap_days=_revisit_gap_days(scenes, period),
            )
            for spec in SPECTRAL_INDICES:
                composite = _composite(stacks[spec.indicator])
                path = _raster_path(aoi.aoi_id, spec.indicator, period[0])
                write_cog(path, composite, grid, description=spec.method.name)
                result.composites[spec.indicator] = path
                del composite
            del stacks
            results.append(result)

        # ------------------------------------------------ land mask from the whole window
        with np.errstate(invalid="ignore", divide="ignore"):
            water_fraction = np.where(
                observed_votes > 0, water_votes / np.maximum(observed_votes, 1), 1.0
            )
        land = (observed_votes > 0) & (water_fraction < WATER_VOTE_THRESHOLD)
        if land.sum() == 0:
            raise RuntimeError(
                "no land pixels found in the area of interest; check the bounding box"
            )
        write_cog(
            settings().raster_dir / aoi.aoi_id / "land_mask.tif",
            land.astype("float32"),
            grid,
            description="Land mask from Sentinel-2 scene classification",
        )
        log.info("land mask: %.1f%% of the grid", 100.0 * land.sum() / grid.pixel_count)

        # ------------------------------------------------------- pass 2: local, validate
        values_written = 0
        values_rejected = 0

        for result in results:
            period = result.period
            for spec in SPECTRAL_INDICES:
                path = result.composites[spec.indicator]
                composite = read_cog(path)
                stats = _spatial_stats(composite, land)

                if stats is None:
                    log.warning(
                        "%s %s: no valid land pixels, skipping",
                        period[0].strftime("%Y-%m"),
                        spec.indicator.value,
                    )
                    continue

                context = ValidationContext(
                    indicator=spec.indicator,
                    value=stats["mean"],
                    period=_date_range(period),
                    history=lake.history_for(conn, ghash, spec.indicator, period[0]),
                    covariates=lake.covariates_for(
                        conn, ghash, period[0], period[1], exclude=spec.indicator
                    ),
                    observation_count=len(result.scenes),
                    cloud_fraction=result.cloud_fraction,
                    revisit_gap_days=result.revisit_gap_days,
                    spatial_coverage=stats["valid_pixels"] / max(1, stats["total_pixels"]),
                )
                report = validate_value(context)

                value_id = lake.value_id_for(
                    aoi.aoi_id, ghash, spec.indicator, period[0], period[1]
                )
                chain = _build_chain(
                    spec,
                    result.scenes,
                    grid,
                    cloud_fraction=result.cloud_fraction,
                    water_threshold=WATER_VOTE_THRESHOLD,
                    validation_summary={
                        "status": report.status,
                        "constraints_checked": report.constraints_checked,
                        "flag_codes": [f.code for f in report.flags],
                    },
                )

                rejected = report.status == "rejected"
                lake.upsert_indicator_value(
                    conn,
                    {
                        "value_id": value_id,
                        "run_id": run_id,
                        "aoi_id": aoi.aoi_id,
                        "geometry_hash": ghash,
                        "indicator": spec.indicator.value,
                        "family": indicator_family(spec.indicator).value,
                        "unit": UNITS[spec.indicator],
                        "period_start": period[0],
                        "period_end": period[1],
                        "value": None if rejected else stats["mean"],
                        "mean": stats["mean"],
                        "median": stats["median"],
                        "std": stats["std"],
                        "p10": stats["p10"],
                        "p90": stats["p90"],
                        "minimum": stats["minimum"],
                        "maximum": stats["maximum"],
                        "valid_pixels": int(stats["valid_pixels"]),
                        "total_pixels": int(stats["total_pixels"]),
                        "validation_status": report.status,
                        "confidence": report.confidence,
                        "confidence_basis_json": report.confidence_basis.model_dump_json(),
                        "flags_json": json.dumps([f.model_dump(mode="json") for f in report.flags]),
                        "constraints_checked": json.dumps(report.constraints_checked),
                        "method_json": spec.method.model_dump_json(),
                        "provenance_json": json.dumps([s.model_dump(mode="json") for s in chain]),
                        "observation_ids": json.dumps([s.observation_id for s in result.scenes]),
                        "raster_path": str(path.relative_to(settings().data_dir)),
                        "source": SOURCE,
                        "dataset_id": COLLECTION,
                        "access_route": ACCESS_ROUTE,
                        "spatial_ref": grid.crs,
                        "resolution_m": grid.resolution_m,
                        "pipeline_version": PIPELINE_VERSION,
                        "algorithm_version": ALGORITHM_VERSION,
                        "computed_at": datetime.now(tz=None).astimezone(),
                    },
                )
                output_ids.append(value_id)

                if rejected:
                    values_rejected += 1
                else:
                    values_written += 1
                    lake.upsert_cells(
                        conn,
                        _cell_rows(
                            composite,
                            land,
                            grid,
                            value_id=value_id,
                            aoi_id=aoi.aoi_id,
                            indicator=spec.indicator,
                            period=period,
                            confidence=report.confidence,
                        ),
                    )
                del composite

        lake.finish_run(conn, run_id, status="ok", input_ids=input_ids, output_ids=output_ids)
        return IngestSummary(
            aoi_id=aoi.aoi_id,
            run_id=run_id,
            months_processed=len(results),
            months_skipped=months_skipped,
            values_written=values_written,
            values_rejected=values_rejected,
            scenes_used=len(input_ids),
        )

    except Exception as exc:
        lake.finish_run(
            conn,
            run_id,
            status="failed",
            input_ids=input_ids,
            output_ids=output_ids,
            error=str(exc),
        )
        raise
    finally:
        conn.close()


def _date_range(period: tuple[date, date]):  # type: ignore[no-untyped-def]
    from ..schemas.common import DateRange

    return DateRange(start=period[0], end=period[1])
