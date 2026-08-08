"""The data lake: DuckDB for tables, cloud-optimised GeoTIFFs for rasters.

No managed services. The whole layer runs from one file on disk plus a directory of
rasters, which is what makes a cold start on a laptop a realistic proposition.

Writes go through here and nowhere else, so every value that lands carries its run id and
its provenance. The DDL is in ``schema/lake.sql`` at the repository root, shared with the
TypeScript reader — neither side redefines it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
from ulid import ULID

from ..config import AreaOfInterest, geometry_hash, settings
from ..schemas.common import IndicatorId
from ..version import ALGORITHM_VERSION, PIPELINE_VERSION

log = logging.getLogger(__name__)


def schema_path() -> Path:
    return Path(__file__).resolve().parents[4] / "schema" / "lake.sql"


def connect(path: Path | None = None, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the lake, creating it and applying the schema if needed."""
    target = path or settings().duckdb_path
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(target), read_only=read_only)
    if not read_only:
        conn.execute(schema_path().read_text())
    return conn


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(parts: Iterable[str]) -> str:
    hasher = hashlib.sha256()
    for part in sorted(parts):
        hasher.update(part.encode())
        hasher.update(b"\x1f")
    return hasher.hexdigest()[:32]


# ------------------------------------------------------------------ areas of interest


def register_aoi(conn: duckdb.DuckDBPyConnection, aoi: AreaOfInterest, area_km2: float) -> str:
    """Insert or refresh an area of interest. Returns its geometry hash."""
    bbox = aoi.bbox()
    ghash = geometry_hash(aoi.geometry)
    conn.execute(
        """
        INSERT INTO aoi (aoi_id, name, description, geometry_json, geometry_hash,
                         west, south, east, north, area_km2, analysis_crs,
                         grid_resolution_m, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (aoi_id) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            geometry_json = excluded.geometry_json,
            geometry_hash = excluded.geometry_hash,
            west = excluded.west, south = excluded.south,
            east = excluded.east, north = excluded.north,
            area_km2 = excluded.area_km2,
            analysis_crs = excluded.analysis_crs,
            grid_resolution_m = excluded.grid_resolution_m
        """,
        [
            aoi.aoi_id,
            aoi.name,
            aoi.description,
            aoi.geometry.model_dump_json(),
            ghash,
            bbox.west,
            bbox.south,
            bbox.east,
            bbox.north,
            area_km2,
            aoi.analysis_crs,
            aoi.grid_resolution_m,
            _now(),
        ],
    )
    return ghash


def load_aoi(conn: duckdb.DuckDBPyConnection, aoi_id: str) -> AreaOfInterest | None:
    row = conn.execute(
        "SELECT aoi_id, name, description, geometry_json, analysis_crs, grid_resolution_m "
        "FROM aoi WHERE aoi_id = ?",
        [aoi_id],
    ).fetchone()
    if row is None:
        return None
    return AreaOfInterest.model_validate(
        {
            "aoi_id": row[0],
            "name": row[1],
            "description": row[2],
            "geometry": json.loads(row[3]),
            "analysis_crs": row[4],
            "grid_resolution_m": row[5],
        }
    )


def list_aoi_ids(conn: duckdb.DuckDBPyConnection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT aoi_id FROM aoi ORDER BY aoi_id").fetchall()]


# --------------------------------------------------------------------- run manifests


def start_run(
    conn: duckdb.DuckDBPyConnection,
    *,
    aoi_id: str,
    command: str,
    parameters: dict[str, Any],
) -> str:
    """Open a run manifest. Every value written names the run that produced it."""
    run_id = f"run_{ULID()}"
    conn.execute(
        """
        INSERT INTO run_manifest (run_id, aoi_id, command, parameters_json,
                                  pipeline_version, algorithm_version, started_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'running')
        """,
        [
            run_id,
            aoi_id,
            command,
            json.dumps(parameters, sort_keys=True, default=str),
            PIPELINE_VERSION,
            ALGORITHM_VERSION,
            _now(),
        ],
    )
    return run_id


def finish_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    *,
    status: str,
    input_ids: Sequence[str] = (),
    output_ids: Sequence[str] = (),
    error: str | None = None,
) -> None:
    """Close a run manifest.

    The two digests are what make determinism checkable: the same inputs under the same
    versions must produce the same outputs digest. A mismatch is a regression, even when
    every individual value still looks reasonable.
    """
    conn.execute(
        """
        UPDATE run_manifest
        SET finished_at = ?, status = ?, inputs_digest = ?, outputs_digest = ?,
            observation_count = ?, value_count = ?, error = ?
        WHERE run_id = ?
        """,
        [
            _now(),
            status,
            _digest(input_ids),
            _digest(output_ids),
            len(input_ids),
            len(output_ids),
            error,
            run_id,
        ],
    )


# ----------------------------------------------------------------------- observations


def record_observation(
    conn: duckdb.DuckDBPyConnection,
    *,
    observation_id: str,
    source: str,
    dataset_id: str,
    access_route: str,
    asset_id: str,
    acquired_at: datetime | None,
    spatial_ref: str,
    resolution_m: float | None = None,
    cloud_cover: float | None = None,
    url: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO observation (observation_id, source, dataset_id, access_route, asset_id,
                                 acquired_at, ingested_at, spatial_ref, resolution_m,
                                 cloud_cover, url, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (observation_id) DO UPDATE SET ingested_at = excluded.ingested_at
        """,
        [
            observation_id,
            source,
            dataset_id,
            access_route,
            asset_id,
            acquired_at,
            _now(),
            spatial_ref,
            resolution_m,
            cloud_cover,
            url,
            json.dumps(metadata or {}, sort_keys=True, default=str),
        ],
    )


# ------------------------------------------------------------------- indicator values


def value_id_for(
    aoi_id: str, geometry_hash_: str, indicator: IndicatorId, start: date, end: date
) -> str:
    """Deterministic primary key, so re-ingesting a period replaces rather than duplicates."""
    canonical = "|".join(
        [
            aoi_id,
            geometry_hash_,
            indicator.value,
            start.isoformat(),
            end.isoformat(),
            ALGORITHM_VERSION,
        ]
    )
    return "val_" + hashlib.blake2b(canonical.encode(), digest_size=12).hexdigest()


def upsert_indicator_value(conn: duckdb.DuckDBPyConnection, row: dict[str, Any]) -> None:
    columns = list(row.keys())
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c != "value_id")
    conn.execute(
        f"INSERT INTO indicator_value ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT (value_id) DO UPDATE SET {updates}",
        [row[c] for c in columns],
    )


def upsert_cells(conn: duckdb.DuckDBPyConnection, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c != "cell_id")
    conn.executemany(
        f"INSERT INTO indicator_cell ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT (cell_id) DO UPDATE SET {updates}",
        [[r[c] for c in columns] for r in rows],
    )


def existing_periods(
    conn: duckdb.DuckDBPyConnection, aoi_id: str, indicator: IndicatorId
) -> set[tuple[date, date]]:
    """Periods already ingested, so a re-run can skip them and stay resumable."""
    rows = conn.execute(
        "SELECT period_start, period_end FROM indicator_value "
        "WHERE aoi_id = ? AND indicator = ? AND algorithm_version = ?",
        [aoi_id, indicator.value, ALGORITHM_VERSION],
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def history_for(
    conn: duckdb.DuckDBPyConnection,
    geometry_hash_: str,
    indicator: IndicatorId,
    before: date,
) -> list[tuple[date, float]]:
    """Prior validated values for the rate checks, ascending.

    Rejected values are excluded. Comparing against a number the engine already refused
    would let one bad month poison the judgement of the next.
    """
    rows = conn.execute(
        """
        SELECT period_end, value FROM indicator_value
        WHERE geometry_hash = ? AND indicator = ? AND period_end < ?
          AND value IS NOT NULL AND validation_status <> 'rejected'
        ORDER BY period_end
        """,
        [geometry_hash_, indicator.value, before],
    ).fetchall()
    return [(r[0], float(r[1])) for r in rows]


def covariates_for(
    conn: duckdb.DuckDBPyConnection,
    geometry_hash_: str,
    start: date,
    end: date,
    *,
    exclude: IndicatorId | None = None,
) -> dict[IndicatorId, float]:
    """Other indicators over the same geometry and period, for coherence checks."""
    rows = conn.execute(
        """
        SELECT indicator, value FROM indicator_value
        WHERE geometry_hash = ? AND period_start = ? AND period_end = ?
          AND value IS NOT NULL AND validation_status <> 'rejected'
        """,
        [geometry_hash_, start, end],
    ).fetchall()
    out: dict[IndicatorId, float] = {}
    for name, value in rows:
        try:
            indicator = IndicatorId(name)
        except ValueError:
            continue
        if exclude is not None and indicator is exclude:
            continue
        out[indicator] = float(value)
    return out
