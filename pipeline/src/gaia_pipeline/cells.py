"""Map cells: turning the rasters on disk into a queryable grid.

Two jobs.

The first is mechanical. Every indicator backed by a cloud-optimised GeoTIFF gets aggregated
to a coarse cell grid so the console can draw it and a click on it can be answered in SQL.
Sentinel-2 ingestion did this for its own three indices and terrain ingestion did not, which
left half the map's layers with nothing behind them.

The second is the interesting one. Two layers are *derived* here rather than measured:

- **Substrate score per cell.** The score already exists as one number for the whole area.
  Computed per cell instead, it stops being a headline and becomes a map of where on the
  landscape the substrate is actually dry — which is the question a land manager has.
- **Departure from normal.** Each cell against its own twelve-month median, which separates
  "this place is dry" from "this place is drier than it usually is". Those are different
  claims and only the second one is news.

Both are honest derivations of validated values, and both carry the provenance of the
values they came from. Neither invents an observation.

Everything here runs from rasters already on disk. No network.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
from pyproj import Transformer

from .config import AreaOfInterest, geometry_hash, settings
from .grid import AnalysisGrid, grid_for
from .raster import read_cog
from .schemas.common import IndicatorId
from .store import lake
from .substrate import COMPONENTS, MINIMUM_WEIGHT_PRESENT, band_for

log = logging.getLogger(__name__)

CELL_SIZE_M = 500.0

#: Identifiers for the two derived layers. Prefixed so they can never collide with a
#: measured indicator, and so a reader can tell at a glance that they are computed.
SUBSTRATE_LAYER = "substrate_score"
ANOMALY_SUFFIX = "_departure"

#: Indicators worth a departure layer. Terrain does not change, and a departure from normal
#: is meaningless for a quantity with no normal.
DEPARTURE_INDICATORS = (IndicatorId.NDMI, IndicatorId.NDVI, IndicatorId.NBR)


@dataclass(frozen=True)
class CellGrid:
    """The coarse grid a raster is aggregated onto, and the maths to place each cell."""

    rows: int
    cols: int
    block: int
    cell_m: float
    grid: AnalysisGrid

    @classmethod
    def of(cls, grid: AnalysisGrid) -> CellGrid:
        block = max(1, round(CELL_SIZE_M / grid.resolution_m))
        return cls(
            rows=grid.height // block,
            cols=grid.width // block,
            block=block,
            cell_m=block * grid.resolution_m,
            grid=grid,
        )

    def bounds_wgs84(self) -> np.ndarray:
        """(rows, cols, 4) array of west/south/east/north for every cell."""
        transformer = Transformer.from_crs(self.grid.crs, "EPSG:4326", always_xy=True)

        lefts = self.grid.left + np.arange(self.cols) * self.cell_m
        tops = self.grid.top - np.arange(self.rows) * self.cell_m

        xs = np.concatenate([lefts, lefts + self.cell_m])
        ys = np.concatenate([tops - self.cell_m, tops])
        gx, gy = np.meshgrid(xs, ys)
        lon, lat = transformer.transform(gx, gy)

        out = np.empty((self.rows, self.cols, 4), dtype="float64")
        out[:, :, 0] = lon[self.rows :, : self.cols]  # west
        out[:, :, 1] = lat[self.rows :, : self.cols]  # south
        out[:, :, 2] = lon[: self.rows, self.cols :]  # east
        out[:, :, 3] = lat[: self.rows, self.cols :]  # north
        return out

    def aggregate(self, values: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Block-mean a full-resolution raster onto the cell grid.

        Returns the per-cell mean and the fraction of each cell that carried data, so a cell
        assembled from three valid pixels can be told apart from one assembled from a
        hundred.
        """
        trimmed = values[: self.rows * self.block, : self.cols * self.block]
        keep = mask[: self.rows * self.block, : self.cols * self.block]
        masked = np.where(keep & np.isfinite(trimmed), trimmed, np.nan)

        blocks = masked.reshape(self.rows, self.block, self.cols, self.block)
        with np.errstate(invalid="ignore"):
            means = np.nanmean(blocks, axis=(1, 3))
        fraction = np.isfinite(blocks).sum(axis=(1, 3)) / float(self.block * self.block)
        return means.astype("float32"), fraction.astype("float32")


def _land_mask(aoi: AreaOfInterest, grid: AnalysisGrid) -> np.ndarray:
    path = settings().raster_dir / aoi.aoi_id / "land_mask.tif"
    if not path.exists():
        return np.ones(grid.shape, dtype=bool)
    return read_cog(path) > 0.5


def _raster_for(aoi_id: str, indicator: str, period_start: date | None) -> Path:
    stem = "static" if period_start is None else period_start.strftime("%Y-%m")
    return settings().raster_dir / aoi_id / indicator / f"{stem}.tif"


def _write_layer(
    conn: object,
    means: np.ndarray,
    fraction: np.ndarray,
    cells: CellGrid,
    bounds: np.ndarray,
    *,
    value_id: str,
    aoi_id: str,
    indicator: str,
    period: tuple[date, date],
    confidence: float,
) -> int:
    """Write one cell layer, vectorised.

    Built as columns and inserted in one statement rather than row by row. At roughly
    sixteen thousand cells per layer and fifty layers, a Python loop with an executemany
    behind it takes tens of minutes; this takes seconds.
    """
    valid = np.isfinite(means)
    count = int(valid.sum())
    if count == 0:
        return 0

    rows, cols = np.nonzero(valid)

    # The layer name belongs in the id. A departure layer shares its parent's value_id, so
    # without it the two collide on the primary key and the derived values silently
    # overwrite the measured ones — which is exactly what happened the first time.
    # The trailing `:row:col` is what the interpretation queries join on, so it stays last.
    prefix = f"{value_id}:{indicator}:"
    ids = np.char.add(
        np.char.add(np.full(count, prefix, dtype=object), rows.astype(str)),
        np.char.add(np.full(count, ":", dtype=object), cols.astype(str)),
    )

    batch = pa.table(
        {
            "cell_id": pa.array(ids.tolist(), type=pa.string()),
            "value_id": pa.array([value_id] * count, type=pa.string()),
            "aoi_id": pa.array([aoi_id] * count, type=pa.string()),
            "indicator": pa.array([indicator] * count, type=pa.string()),
            "period_start": pa.array([period[0]] * count, type=pa.date32()),
            "period_end": pa.array([period[1]] * count, type=pa.date32()),
            "west": pa.array(bounds[rows, cols, 0]),
            "south": pa.array(bounds[rows, cols, 1]),
            "east": pa.array(bounds[rows, cols, 2]),
            "north": pa.array(bounds[rows, cols, 3]),
            "value": pa.array(means[rows, cols].astype("float64")),
            "valid_fraction": pa.array(fraction[rows, cols].astype("float64")),
            "confidence": pa.array(np.full(count, confidence, dtype="float64")),
            "cell_size_m": pa.array(np.full(count, cells.cell_m, dtype="float64")),
        }
    )

    conn.register("cell_batch", batch)  # type: ignore[attr-defined]
    conn.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO indicator_cell
        SELECT cell_id, value_id, aoi_id, indicator, period_start, period_end,
               west, south, east, north, value, valid_fraction, confidence, cell_size_m
        FROM cell_batch
        ON CONFLICT (cell_id) DO UPDATE SET
            value = excluded.value,
            valid_fraction = excluded.valid_fraction,
            confidence = excluded.confidence
        """
    )
    conn.unregister("cell_batch")  # type: ignore[attr-defined]
    return count


def rebuild(aoi: AreaOfInterest, *, db_path: Path | None = None) -> dict[str, int]:
    """Rebuild every cell layer for an area from the rasters on disk.

    Idempotent, and safe to re-run after any ingest. Cell ids are derived from the parent
    value id and the cell's position, so a rebuild replaces rather than duplicates.
    """
    grid = grid_for(aoi)
    cells = CellGrid.of(grid)
    bounds = cells.bounds_wgs84()
    land = _land_mask(aoi, grid)
    ghash = geometry_hash(aoi.geometry)

    conn = lake.connect(db_path)
    written: dict[str, int] = {}

    try:
        # Cleared rather than upserted: cell ids changed shape once already, and leaving
        # orphans behind means a layer keeps serving values from a scheme that no longer
        # exists. Cells are derived from rasters on disk and cost forty seconds to rebuild.
        conn.execute("DELETE FROM indicator_cell WHERE aoi_id = ?", [aoi.aoi_id])

        values = conn.execute(
            """
            SELECT indicator, period_start, period_end, value_id, confidence, raster_path
            FROM indicator_value
            WHERE aoi_id = ? AND raster_path IS NOT NULL AND value IS NOT NULL
              AND validation_status <> 'rejected'
            ORDER BY indicator, period_start
            """,
            [aoi.aoi_id],
        ).fetchall()

        # ------------------------------------------------ measured layers, from COGs
        # Cached because the derived layers below need the same arrays and reading a
        # 10-megapixel GeoTIFF repeatedly is the slowest thing here.
        cache: dict[tuple[str, date], np.ndarray] = {}

        for indicator, period_start, period_end, value_id, confidence, _raster in values:
            static = period_start.year < 2001
            path = _raster_for(aoi.aoi_id, indicator, None if static else period_start)
            if not path.exists():
                log.warning("no raster on disk for %s %s", indicator, period_start)
                continue

            array = read_cog(path)
            cache[(indicator, period_start)] = array

            means, fraction = cells.aggregate(array, land)
            count = _write_layer(
                conn,
                means,
                fraction,
                cells,
                bounds,
                value_id=value_id,
                aoi_id=aoi.aoi_id,
                indicator=indicator,
                period=(period_start, period_end),
                confidence=float(confidence),
            )
            written[indicator] = written.get(indicator, 0) + count
            log.info("%s %s: %d cells", indicator, period_start, count)

        # --------------------------------------------------------- departure layers
        #
        # Each cell against its own twelve-month median. "This stand is dry" and "this
        # stand is drier than it has been all year" are different claims, and a map that
        # cannot tell them apart mostly shows you where the conifers are.
        for indicator in DEPARTURE_INDICATORS:
            series = sorted(
                (period, array)
                for (name, period), array in cache.items()
                if name == indicator.value
            )
            if len(series) < 4:
                continue

            stack = np.stack([array for _, array in series], axis=0)
            with np.errstate(invalid="ignore"):
                baseline = np.nanmedian(stack, axis=0)

            for period_start, array in series:
                row = conn.execute(
                    """
                    SELECT value_id, period_end, confidence FROM indicator_value
                    WHERE aoi_id = ? AND indicator = ? AND period_start = ?
                    """,
                    [aoi.aoi_id, indicator.value, period_start],
                ).fetchone()
                if row is None:
                    continue

                means, fraction = cells.aggregate(array - baseline, land)
                layer = f"{indicator.value}{ANOMALY_SUFFIX}"
                count = _write_layer(
                    conn,
                    means,
                    fraction,
                    cells,
                    bounds,
                    value_id=row[0],
                    aoi_id=aoi.aoi_id,
                    indicator=layer,
                    period=(period_start, row[1]),
                    confidence=float(row[2]),
                )
                written[layer] = written.get(layer, 0) + count

        # -------------------------------------------------------- substrate per cell
        #
        # The composite, computed pixel by pixel instead of once for the whole area.
        # Spectral and terrain components vary across the landscape and are taken from
        # their rasters; climate and soil come from ERA5-Land at roughly 9 km, which over
        # an area this size is one value — so they shift the whole surface up or down
        # rather than shaping it. That is a real limitation and it is stated in the
        # layer's own description rather than hidden by interpolating a smooth field
        # nobody measured.
        scores = conn.execute(
            """
            SELECT score_id, period_start, period_end, confidence
            FROM substrate_score WHERE aoi_id = ? ORDER BY period_start
            """,
            [aoi.aoi_id],
        ).fetchall()

        for score_id, period_start, period_end, confidence in scores:
            surface = np.zeros(grid.shape, dtype="float32")
            weight_present = 0.0

            for spec in COMPONENTS:
                array = _component_array(conn, cache, aoi, spec.indicator, period_start, ghash)
                if array is None:
                    continue
                normalised = np.clip((array - spec.benign) / (spec.severe - spec.benign), 0.0, 1.0)
                surface = surface + np.nan_to_num(normalised, nan=0.0) * spec.weight
                weight_present += spec.weight

            if weight_present < MINIMUM_WEIGHT_PRESENT:
                continue

            surface = surface / weight_present * 100.0
            valid = land & np.isfinite(
                cache.get((IndicatorId.NDMI.value, period_start), np.full(grid.shape, np.nan))
            )

            means, fraction = cells.aggregate(surface, valid)
            count = _write_layer(
                conn,
                means,
                fraction,
                cells,
                bounds,
                value_id=score_id,
                aoi_id=aoi.aoi_id,
                indicator=SUBSTRATE_LAYER,
                period=(period_start, period_end),
                confidence=float(confidence),
            )
            written[SUBSTRATE_LAYER] = written.get(SUBSTRATE_LAYER, 0) + count
            log.info(
                "%s substrate: %d cells, %.0f%% of scheme weight",
                period_start.strftime("%Y-%m"),
                count,
                100 * weight_present,
            )

        return written
    finally:
        conn.close()


def _component_array(
    conn: object,
    cache: dict[tuple[str, date], np.ndarray],
    aoi: AreaOfInterest,
    indicator: IndicatorId,
    period_start: date,
    ghash: str,
) -> np.ndarray | None:
    """The full-resolution surface for one substrate component in one month.

    Spectral layers vary monthly; terrain is static and reused; climate has no spatial
    field at this scale and enters as a constant surface.
    """
    from datetime import date as _date

    monthly = cache.get((indicator.value, period_start))
    if monthly is not None:
        return monthly

    static = cache.get((indicator.value, _date(2000, 1, 1)))
    if static is not None:
        return static

    row = conn.execute(  # type: ignore[attr-defined]
        """
        SELECT value FROM indicator_value
        WHERE geometry_hash = ? AND indicator = ? AND period_start = ?
          AND value IS NOT NULL AND validation_status <> 'rejected'
        """,
        [ghash, indicator.value, period_start],
    ).fetchone()
    if row is None:
        return None

    grid = grid_for(aoi)
    return np.full(grid.shape, float(row[0]), dtype="float32")


def describe_layers() -> dict[str, str]:
    """What each derived layer means, for the interface to show rather than assume."""
    return {
        SUBSTRATE_LAYER: (
            "Wildfire substrate condition per 500 m cell, 0-100. Spectral and terrain "
            "components vary across the landscape; climate and soil enter from ERA5-Land at "
            "roughly 9 km, so they raise or lower the whole surface rather than shaping it."
        ),
        **{
            f"{indicator.value}{ANOMALY_SUFFIX}": (
                f"Departure of {indicator.value.upper()} from this cell's own twelve-month "
                "median. Negative is drier than normal for this place, which is a different "
                "claim from simply being dry."
            )
            for indicator in DEPARTURE_INDICATORS
        },
    }


def band_for_score(score: float) -> str:
    return band_for(score)


def now() -> datetime:
    return datetime.now().astimezone()
