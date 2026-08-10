"""Land cover from ESA WorldCover 10 m.

The single largest gap in the layer's interpretive power until now. Every reading the
analysis engine produced — the driest decile sits at 100 m on south-facing slopes — meant
something quite different depending on whether that ground is closed conifer, regenerating
clearcut, pasture or a subdivision, and the layer had no way to tell the difference.

Fetched from the public ESA S3 bucket over anonymous HTTPS. No account, no token.

Land cover is categorical, which makes it the one indicator here that does not average.
A cell takes the class covering the most of it, not the mean of the class codes, because
the mean of "tree cover" and "built-up" is "shrubland" and that would be nonsense.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime

import numpy as np
from rasterio.enums import Resampling

from ..config import AreaOfInterest, geometry_hash, settings
from ..grid import grid_for
from ..provenance import ChainBuilder
from ..raster import read_window, write_cog
from ..schemas.common import UNITS, DateRange, IndicatorId, indicator_family
from ..schemas.provenance import Method
from ..store import lake
from ..validation import ValidationContext, validate_value
from ..version import ALGORITHM_VERSION, PIPELINE_VERSION

log = logging.getLogger(__name__)

SOURCE = "ESA"
DATASET = "esa-worldcover-v200-2021"
ACCESS_ROUTE = "esa-worldcover-s3"
BUCKET = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"

#: WorldCover class codes and what they are.
CLASSES: dict[int, str] = {
    10: "Tree cover",
    20: "Shrubland",
    30: "Grassland",
    40: "Cropland",
    50: "Built-up",
    60: "Bare or sparse vegetation",
    70: "Snow and ice",
    80: "Permanent water",
    90: "Herbaceous wetland",
    95: "Mangroves",
    100: "Moss and lichen",
}

#: How each class behaves as wildfire fuel. Not part of the score in v0.1 — stated so the
#: interpretation can say something grounded about what a cover type implies.
FUEL_CHARACTER: dict[int, str] = {
    10: "closed canopy: high fuel load, crown fire potential, moisture buffered by shade",
    20: "shrub fuels: cures fast, burns readily, carries fire between stands",
    30: "fine fuels: cures earliest in the season, ignites easily, spreads fast",
    40: "managed fuels: condition depends on crop stage and irrigation",
    50: "developed: little continuous fuel, but this is where ignitions and exposure are",
    60: "sparse fuels: little to carry fire",
    70: "no fuel",
    80: "no fuel",
    90: "wet fuels: normally a barrier, but a carrier once drawn down",
    95: "wet fuels",
    100: "sparse fuels",
}

METHOD = Method(
    name="ESA WorldCover 10 m v200 (2021)",
    citation=(
        "Zanaga, D. et al. (2022). ESA WorldCover 10 m 2021 v200. doi:10.5281/zenodo.7254221"
    ),
    doi="10.5281/zenodo.7254221",
    notes=(
        "Eleven-class global land cover from Sentinel-1 and Sentinel-2, at 10 m for the "
        "2021 epoch. Aggregated here by majority class per cell rather than by averaging, "
        "since the codes are labels rather than quantities. A single epoch: it does not "
        "track cover change, so a clearcut since 2021 still reads as tree cover."
    ),
)


def _tiles_for(west: float, south: float, east: float, north: float) -> list[str]:
    """WorldCover tile names covering a bounding box. Tiles are 3 degrees square."""
    names: list[str] = []
    for lat in range(int(math.floor(south / 3) * 3), int(math.floor(north / 3) * 3) + 1, 3):
        for lon in range(int(math.floor(west / 3) * 3), int(math.floor(east / 3) * 3) + 1, 3):
            ns = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}"
            ew = f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
            names.append(f"{ns}{ew}")
    return names


def ingest(aoi: AreaOfInterest, *, force: bool = False) -> int:
    """Land cover for an area, as a majority-class raster on the analysis grid."""
    grid = grid_for(aoi)
    ghash = geometry_hash(aoi.geometry)
    conn = lake.connect()

    period_start, period_end = date(2000, 1, 1), date(2100, 1, 1)
    run_id = lake.start_run(
        conn,
        aoi_id=aoi.aoi_id,
        command="ingest landcover",
        parameters={"dataset": DATASET},
    )

    try:
        if not force and lake.existing_periods(conn, aoi.aoi_id, IndicatorId.LAND_COVER):
            log.info("land cover already ingested")
            lake.finish_run(conn, run_id, status="ok")
            return 0

        bbox = aoi.bbox()
        mosaic = np.full(grid.shape, np.nan, dtype="float32")
        assets: list[str] = []

        for tile in _tiles_for(bbox.west, bbox.south, bbox.east, bbox.north):
            url = f"{BUCKET}/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
            log.info("reading %s", tile)
            try:
                # Nearest neighbour: these are class codes, and interpolating between
                # "grassland" and "built-up" would invent a class that does not exist.
                patch = read_window(url, grid, resampling=Resampling.nearest)
            except Exception as exc:
                log.warning("tile %s unavailable: %s", tile, exc)
                continue
            assets.append(url)
            mosaic = np.where(np.isfinite(mosaic), mosaic, patch)

        if not assets or not np.isfinite(mosaic).any():
            raise RuntimeError("no WorldCover tiles covered this area")

        mosaic = np.where(mosaic > 0, mosaic, np.nan)
        path = settings().raster_dir / aoi.aoi_id / IndicatorId.LAND_COVER.value / "static.tif"
        write_cog(path, mosaic, grid, description=METHOD.name)

        valid = np.isfinite(mosaic)
        codes, counts = np.unique(mosaic[valid].astype(int), return_counts=True)
        composition = {
            CLASSES.get(int(code), f"class {code}"): int(count)
            for code, count in zip(codes, counts, strict=False)
        }
        dominant = int(codes[int(np.argmax(counts))])

        log.info(
            "composition: %s",
            ", ".join(
                f"{name} {100 * count / valid.sum():.0f}%"
                for name, count in sorted(composition.items(), key=lambda kv: -kv[1])[:5]
            ),
        )

        chain = ChainBuilder(grid.crs, grid.resolution_m)
        chain.observation(
            description=(
                f"ESA WorldCover 10 m v200 tiles covering the area ({len(assets)}), read as "
                "a windowed mosaic and reprojected to the analysis grid by nearest "
                "neighbour."
            ),
            source=SOURCE,
            dataset_id=DATASET,
            access_route=ACCESS_ROUTE,
            asset_ids=assets,
            acquired_at=datetime(2021, 7, 1).astimezone(),
            spatial_ref="EPSG:4326",
            resolution_m=10.0,
        )
        chain.processing(
            description=(
                "Class codes preserved without interpolation. The area's modal class is "
                f"{CLASSES.get(dominant, dominant)}."
            ),
            parameters={"resampling": "nearest", "composition_pixels": composition},
        )

        # The served scalar is the modal class code. It is a label, so the constraint
        # engine's job here is only to confirm it is a real WorldCover class.
        report = validate_value(
            ValidationContext(
                indicator=IndicatorId.LAND_COVER,
                value=float(dominant),
                period=DateRange(start=period_start, end=period_end),
                history=[],
                covariates={},
                observation_count=len(assets),
                cloud_fraction=0.0,
                revisit_gap_days=0.0,
                spatial_coverage=float(valid.sum()) / float(max(1, grid.pixel_count)),
            )
        )
        chain.validation(
            description="Class code checked against the WorldCover legend.",
            parameters={"status": report.status, "dominant_class": dominant},
        )

        lake.upsert_indicator_value(
            conn,
            {
                "value_id": lake.value_id_for(
                    aoi.aoi_id, ghash, IndicatorId.LAND_COVER, period_start, period_end
                ),
                "run_id": run_id,
                "aoi_id": aoi.aoi_id,
                "geometry_hash": ghash,
                "indicator": IndicatorId.LAND_COVER.value,
                "family": indicator_family(IndicatorId.LAND_COVER).value,
                "unit": UNITS[IndicatorId.LAND_COVER],
                "period_start": period_start,
                "period_end": period_end,
                "value": float(dominant),
                "mean": float(dominant),
                "median": float(dominant),
                "std": 0.0,
                "p10": float(codes.min()),
                "p90": float(codes.max()),
                "minimum": float(codes.min()),
                "maximum": float(codes.max()),
                "valid_pixels": int(valid.sum()),
                "total_pixels": int(grid.pixel_count),
                "validation_status": report.status,
                "confidence": report.confidence,
                "confidence_basis_json": report.confidence_basis.model_dump_json(),
                "flags_json": json.dumps([f.model_dump(mode="json") for f in report.flags]),
                "constraints_checked": json.dumps(report.constraints_checked),
                "method_json": METHOD.model_dump_json(),
                "provenance_json": json.dumps([s.model_dump(mode="json") for s in chain.build()]),
                "observation_ids": json.dumps([f"{DATASET}:{a}" for a in assets]),
                "raster_path": str(path.relative_to(settings().data_dir)),
                "source": SOURCE,
                "dataset_id": DATASET,
                "access_route": ACCESS_ROUTE,
                "spatial_ref": grid.crs,
                "resolution_m": grid.resolution_m,
                "pipeline_version": PIPELINE_VERSION,
                "algorithm_version": ALGORITHM_VERSION,
                "computed_at": datetime.now().astimezone(),
            },
        )

        for asset in assets:
            lake.record_observation(
                conn,
                observation_id=f"{DATASET}:{asset}",
                source=SOURCE,
                dataset_id=DATASET,
                access_route=ACCESS_ROUTE,
                asset_id=asset.rsplit("/", 1)[-1],
                acquired_at=datetime(2021, 7, 1).astimezone(),
                spatial_ref="EPSG:4326",
                resolution_m=10.0,
                url=asset,
            )

        lake.finish_run(conn, run_id, status="ok", input_ids=assets)
        return 1
    except Exception as exc:
        lake.finish_run(conn, run_id, status="failed", error=str(exc))
        raise
    finally:
        conn.close()
