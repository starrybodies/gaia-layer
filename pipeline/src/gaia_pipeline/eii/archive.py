"""The v0.2 archive: Parquet facts, a DuckDB catalog, and provenance stored once.

v0.1 writes a full provenance chain as JSON onto every value. At 43,303 cells times 120
months times six series that is twenty-five million copies of the same paragraph, which is
several gigabytes of disk to say one thing over and over.

So v0.2 stores provenance by reference. A fact row carries `method_id`, `run_id` and
`source_set_id`; the chain is assembled from dimension tables on read. What a caller
receives is identical to v0.1 — every number still arrives with its sources, their native
resolutions, their citations and the moment they were retrieved. The difference is only
that the archive is the size of the measurements rather than the size of the paperwork.

Facts live in Parquet partitioned by component and year, so a year can be rebuilt without
touching the rest and DuckDB can scan a single component without reading the others.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from ulid import ULID

log = logging.getLogger(__name__)

#: Columns every component partition must have. The archive refuses anything else, because
#: a fact without a run behind it cannot have its provenance resolved later.
FACT_COLUMNS: tuple[str, ...] = (
    "h3",
    "period_start",
    "period_end",
    "component",
    "value",
    "uncertainty_type",
    "uncertainty_value",
    "valid_fraction",
    "method_id",
    "run_id",
    "source_set_id",
    "constraint_flags",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS method (
    method_id     VARCHAR PRIMARY KEY,
    name          VARCHAR NOT NULL,
    formula       VARCHAR,
    citation      VARCHAR NOT NULL,
    doi           VARCHAR,
    notes         VARCHAR,
    version       VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS source (
    source_id            VARCHAR PRIMARY KEY,
    dataset              VARCHAR NOT NULL,
    version              VARCHAR NOT NULL,
    access_route         VARCHAR NOT NULL,
    uri                  VARCHAR NOT NULL,
    native_resolution_m  DOUBLE,
    native_timestep      VARCHAR,
    citation             VARCHAR NOT NULL,
    licence              VARCHAR,
    retrieved            TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS source_set (
    source_set_id VARCHAR PRIMARY KEY,
    created       TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS source_set_member (
    source_set_id VARCHAR NOT NULL,
    source_id     VARCHAR NOT NULL,
    PRIMARY KEY (source_set_id, source_id)
);

CREATE TABLE IF NOT EXISTS run (
    run_id        VARCHAR PRIMARY KEY,
    command       VARCHAR NOT NULL,
    component     VARCHAR,
    method_id     VARCHAR,
    source_set_id VARCHAR,
    parameters    VARCHAR NOT NULL,
    started       TIMESTAMPTZ NOT NULL,
    finished      TIMESTAMPTZ,
    status        VARCHAR NOT NULL,
    error         VARCHAR,
    pipeline_version  VARCHAR NOT NULL,
    algorithm_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS h3_cell (
    h3        VARCHAR PRIMARY KEY,
    res       UTINYINT NOT NULL,
    parent_h3 VARCHAR NOT NULL,
    lat       DOUBLE NOT NULL,
    lon       DOUBLE NOT NULL,
    area_km2  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS partition_index (
    component     VARCHAR NOT NULL,
    year          INTEGER NOT NULL,
    path          VARCHAR NOT NULL,
    rows          BIGINT NOT NULL,
    run_id        VARCHAR NOT NULL,
    written       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (component, year)
);

CREATE TABLE IF NOT EXISTS constraint_event (
    event_id   VARCHAR PRIMARY KEY,
    run_id     VARCHAR NOT NULL,
    h3         VARCHAR NOT NULL,
    period_start DATE NOT NULL,
    rule       VARCHAR NOT NULL,
    outcome    VARCHAR NOT NULL,
    detail     VARCHAR,
    recorded   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    entry_id  VARCHAR PRIMARY KEY,
    tool      VARCHAR NOT NULL,
    request   VARCHAR NOT NULL,
    response_digest VARCHAR NOT NULL,
    rows_returned INTEGER,
    called    TIMESTAMPTZ NOT NULL
);
"""


@dataclass(frozen=True)
class SourceRecord:
    """One external dataset, as it was on the day it was read.

    `access_route` is the thing a reader most often wants and most often cannot find later:
    not just which dataset, but which door we came in through, because the same data behind
    two doors is two different reliability stories.
    """

    dataset: str
    version: str
    access_route: str
    uri: str
    citation: str
    native_resolution_m: float | None = None
    native_timestep: str | None = None
    licence: str | None = None
    retrieved: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def source_id(self) -> str:
        return f"{self.dataset}:{self.version}:{self.uri}"


@dataclass(frozen=True)
class MethodRecord:
    """How a number was produced, stated once and referenced everywhere."""

    method_id: str
    name: str
    citation: str
    version: str
    formula: str | None = None
    doi: str | None = None
    notes: str | None = None


def open_catalog(path: Path) -> duckdb.DuckDBPyConnection:
    """Open, and create if absent, the catalog beside the Parquet archive."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    conn.execute(SCHEMA)
    return conn


def register_method(conn: duckdb.DuckDBPyConnection, method: MethodRecord) -> str:
    conn.execute(
        """
        INSERT INTO method (method_id, name, formula, citation, doi, notes, version)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (method_id) DO UPDATE SET
            name = excluded.name, formula = excluded.formula, citation = excluded.citation,
            doi = excluded.doi, notes = excluded.notes, version = excluded.version
        """,
        [
            method.method_id,
            method.name,
            method.formula,
            method.citation,
            method.doi,
            method.notes,
            method.version,
        ],
    )
    return method.method_id


def register_sources(conn: duckdb.DuckDBPyConnection, sources: list[SourceRecord]) -> str:
    """Record a set of sources and return the id that facts will point at.

    The set id is deterministic in its members, so re-running an ingest against the same
    inputs does not fork the provenance graph.
    """
    if not sources:
        raise ValueError("a source set with no sources cannot support a claim")

    ids = sorted({source.source_id for source in sources})
    set_id = "set_" + _digest(ids)

    for source in sources:
        conn.execute(
            """
            INSERT INTO source (source_id, dataset, version, access_route, uri,
                                native_resolution_m, native_timestep, citation, licence,
                                retrieved)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source_id) DO UPDATE SET retrieved = excluded.retrieved
            """,
            [
                source.source_id,
                source.dataset,
                source.version,
                source.access_route,
                source.uri,
                source.native_resolution_m,
                source.native_timestep,
                source.citation,
                source.licence,
                source.retrieved,
            ],
        )

    conn.execute(
        "INSERT INTO source_set VALUES (?, ?) ON CONFLICT (source_set_id) DO NOTHING",
        [set_id, datetime.now(UTC)],
    )
    for source_id in ids:
        conn.execute(
            "INSERT INTO source_set_member VALUES (?, ?) ON CONFLICT DO NOTHING",
            [set_id, source_id],
        )
    return set_id


def start_run(
    conn: duckdb.DuckDBPyConnection,
    *,
    command: str,
    component: str | None = None,
    method_id: str | None = None,
    source_set_id: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> str:
    from ..version import ALGORITHM_VERSION, PIPELINE_VERSION

    run_id = f"run_{ULID()}"
    conn.execute(
        """
        INSERT INTO run (run_id, command, component, method_id, source_set_id, parameters,
                         started, status, pipeline_version, algorithm_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
        """,
        [
            run_id,
            command,
            component,
            method_id,
            source_set_id,
            json.dumps(parameters or {}, default=str),
            datetime.now(UTC),
            PIPELINE_VERSION,
            ALGORITHM_VERSION,
        ],
    )
    return run_id


def finish_run(
    conn: duckdb.DuckDBPyConnection, run_id: str, *, status: str, error: str | None = None
) -> None:
    conn.execute(
        "UPDATE run SET finished = ?, status = ?, error = ? WHERE run_id = ?",
        [datetime.now(UTC), status, error, run_id],
    )


def write_cells(conn: duckdb.DuckDBPyConnection, cells: pa.Table) -> int:
    """Persist the spine's cell table so a reader can resolve a cell id to a place."""
    conn.execute("CREATE OR REPLACE TEMP VIEW incoming AS SELECT * FROM cells")
    conn.register("cells", cells)
    conn.execute(
        """
        INSERT INTO h3_cell SELECT h3, res, parent_h3, lat, lon, area_km2 FROM cells
        ON CONFLICT (h3) DO NOTHING
        """
    )
    conn.unregister("cells")
    return int(cells.num_rows)


def _null_out_non_finite(facts: pa.Table) -> pa.Table:
    """NaN and infinity become NULL on the way into the archive.

    A NaN is a float's way of saying "not a number here"; NULL is the archive's. Both mean
    unmeasured, and carrying two spellings of it across the boundary makes every consumer
    handle both or handle one and be wrong. The 2023 Component A partition held 460 NaNs
    where it meant NULL, and a reader counting `value IS NOT NULL` scored them, a reader
    sorting on value put them wherever its collation happened to, and JSON turned them into
    `null` anyway — three different answers to one question.

    Only float columns are touched, and only the non-finite entries in them.
    """
    for name in facts.column_names:
        column = facts.column(name)
        if not pa.types.is_floating(column.type):
            continue
        values = np.asarray(column.combine_chunks(), dtype="float64")
        finite = np.isfinite(values)
        if finite.all():
            continue
        facts = facts.set_column(
            facts.column_names.index(name),
            name,
            pa.array(values, type=column.type, mask=~finite),
        )
    return facts


def write_component(
    conn: duckdb.DuckDBPyConnection,
    archive_dir: Path,
    *,
    component: str,
    year: int,
    facts: pa.Table,
    run_id: str,
) -> int:
    """Write one component-year partition, replacing any previous version of it.

    Replacement rather than append: a rebuild of 2023 should leave 2023 as the rebuild found
    it, not as a union of two attempts. v0.1 learned this the hard way when derived cells
    accumulated under an older id scheme.
    """
    missing = [column for column in FACT_COLUMNS if column not in facts.column_names]
    if missing:
        raise ValueError(f"component facts are missing required columns: {missing}")

    if not _run_exists(conn, run_id):
        raise ValueError(f"unknown run_id {run_id}; facts must be attributable to a run")

    directory = archive_dir / f"component={component}" / f"year={year}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "part.parquet"

    pq.write_table(_null_out_non_finite(facts.select(list(FACT_COLUMNS))), path, compression="zstd")

    conn.execute(
        """
        INSERT INTO partition_index (component, year, path, rows, run_id, written)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (component, year) DO UPDATE SET
            path = excluded.path, rows = excluded.rows,
            run_id = excluded.run_id, written = excluded.written
        """,
        [component, year, str(path), facts.num_rows, run_id, datetime.now(UTC)],
    )
    return int(facts.num_rows)


def read_component(
    conn: duckdb.DuckDBPyConnection,
    archive_dir: Path,
    *,
    component: str,
    year: int | None = None,
) -> pa.Table:
    """Read a component back, optionally for one year."""
    pattern = (
        archive_dir
        / f"component={component}"
        / (f"year={year}" if year else "year=*")
        / "*.parquet"
    )
    matches = sorted(pattern.parent.parent.glob(str(pattern.relative_to(pattern.parent.parent))))
    if not matches:
        return pa.table({name: pa.array([], _fact_type(name)) for name in FACT_COLUMNS})

    columns = ", ".join(FACT_COLUMNS)
    return conn.execute(
        f"SELECT {columns} FROM read_parquet(?)", [[str(match) for match in matches]]
    ).fetch_arrow_table()


def resolve_provenance(conn: duckdb.DuckDBPyConnection, run_id: str) -> dict[str, Any]:
    """Assemble the full chain for a run: what produced it, from what, when.

    This is the read side of provenance-by-reference. The output is the same shape v0.1
    serves inline; the storage is the only thing that changed.
    """
    run = conn.execute(
        """
        SELECT command, component, method_id, source_set_id, parameters, started, finished,
               status, pipeline_version, algorithm_version
        FROM run WHERE run_id = ?
        """,
        [run_id],
    ).fetchone()
    if run is None:
        raise KeyError(f"no run {run_id}")

    method = None
    if run[2] is not None:
        row = conn.execute(
            "SELECT method_id, name, formula, citation, doi, notes, version FROM method WHERE method_id = ?",
            [run[2]],
        ).fetchone()
        if row is not None:
            method = dict(
                zip(
                    ["method_id", "name", "formula", "citation", "doi", "notes", "version"],
                    row,
                    strict=True,
                )
            )

    sources = [
        dict(
            zip(
                [
                    "dataset",
                    "version",
                    "access_route",
                    "uri",
                    "native_resolution_m",
                    "native_timestep",
                    "citation",
                    "licence",
                    "retrieved",
                ],
                row,
                strict=True,
            )
        )
        for row in conn.execute(
            """
            SELECT s.dataset, s.version, s.access_route, s.uri, s.native_resolution_m,
                   s.native_timestep, s.citation, s.licence, s.retrieved
            FROM source_set_member m JOIN source s USING (source_id)
            WHERE m.source_set_id = ? ORDER BY s.dataset, s.version
            """,
            [run[3]],
        ).fetchall()
    ]

    return {
        "run_id": run_id,
        "command": run[0],
        "component": run[1],
        "method": method,
        "sources": sources,
        "parameters": json.loads(run[4]),
        "started": run[5],
        "finished": run[6],
        "status": run[7],
        "pipeline_version": run[8],
        "algorithm_version": run[9],
        "prov_o": {
            "wasGeneratedBy": run_id,
            "wasDerivedFrom": [source["uri"] for source in sources],
        },
    }


def record_constraint_event(
    conn: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    h3: str,
    period_start: date,
    rule: str,
    outcome: str,
    detail: str | None = None,
) -> None:
    """Every rule that fires is written down, whether it clamped or only flagged."""
    conn.execute(
        "INSERT INTO constraint_event VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [f"ce_{ULID()}", run_id, h3, period_start, rule, outcome, detail, datetime.now(UTC)],
    )


def _run_exists(conn: duckdb.DuckDBPyConnection, run_id: str) -> bool:
    return conn.execute("SELECT 1 FROM run WHERE run_id = ?", [run_id]).fetchone() is not None


def _digest(parts: list[str]) -> str:
    import hashlib

    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _fact_type(name: str) -> pa.DataType:
    if name in {"period_start", "period_end"}:
        return pa.date32()
    if name in {"value", "uncertainty_value", "valid_fraction"}:
        return pa.float32()
    return pa.string()
