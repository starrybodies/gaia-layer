-- Gaia layer data lake — DuckDB schema.
--
-- One source of truth for both sides of the language boundary: the Python pipeline writes
-- these tables, the TypeScript service layer reads them. Neither redefines the DDL.
--
-- Provenance is a first-class column throughout. There is no table here where a value can
-- be stored without the record of where it came from.

INSTALL spatial;
LOAD spatial;

-- Areas the layer maintains coverage for.
CREATE TABLE IF NOT EXISTS aoi (
    aoi_id            TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    geometry_json     TEXT NOT NULL,
    geometry_hash     TEXT NOT NULL,
    west              DOUBLE NOT NULL,
    south             DOUBLE NOT NULL,
    east              DOUBLE NOT NULL,
    north             DOUBLE NOT NULL,
    area_km2          DOUBLE NOT NULL,
    analysis_crs      TEXT NOT NULL,
    grid_resolution_m DOUBLE NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL
);

-- One row per pipeline run. Same inputs and same versions must produce the same
-- outputs_digest; a mismatch means the pipeline stopped being deterministic.
CREATE TABLE IF NOT EXISTS run_manifest (
    run_id            TEXT PRIMARY KEY,
    aoi_id            TEXT NOT NULL,
    command           TEXT NOT NULL,
    parameters_json   TEXT NOT NULL DEFAULT '{}',
    pipeline_version  TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    started_at        TIMESTAMPTZ NOT NULL,
    finished_at       TIMESTAMPTZ,
    status            TEXT NOT NULL,          -- running | ok | failed
    inputs_digest     TEXT,
    outputs_digest    TEXT,
    observation_count INTEGER NOT NULL DEFAULT 0,
    value_count       INTEGER NOT NULL DEFAULT 0,
    error             TEXT
);

-- Source observations consumed by the pipeline: satellite scenes, reanalysis grid cells,
-- DEM tiles. The bottom of every provenance chain resolves to rows in here.
CREATE TABLE IF NOT EXISTS observation (
    observation_id TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    dataset_id     TEXT NOT NULL,
    access_route   TEXT NOT NULL,
    asset_id       TEXT NOT NULL,
    acquired_at    TIMESTAMPTZ,
    ingested_at    TIMESTAMPTZ NOT NULL,
    spatial_ref    TEXT NOT NULL,
    resolution_m   DOUBLE,
    cloud_cover    DOUBLE,
    url            TEXT,
    metadata_json  TEXT NOT NULL DEFAULT '{}'
);

-- The measured values: one row per area, indicator and period.
--
-- A rejected value keeps its row with `value` NULL and its flags intact. The row exists so
-- the absence is auditable; the NULL is what stops it being served as an answer.
CREATE TABLE IF NOT EXISTS indicator_value (
    value_id              TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL,
    aoi_id                TEXT NOT NULL,
    geometry_hash         TEXT NOT NULL,
    indicator             TEXT NOT NULL,
    family                TEXT NOT NULL,
    unit                  TEXT NOT NULL,
    period_start          DATE NOT NULL,
    period_end            DATE NOT NULL,

    value                 DOUBLE,             -- NULL when validation_status = 'rejected'
    mean                  DOUBLE,
    median                DOUBLE,
    std                   DOUBLE,
    p10                   DOUBLE,
    p90                   DOUBLE,
    minimum               DOUBLE,
    maximum               DOUBLE,
    valid_pixels          BIGINT,
    total_pixels          BIGINT,

    validation_status     TEXT NOT NULL,      -- validated | flagged | rejected
    confidence            DOUBLE NOT NULL,
    confidence_basis_json TEXT NOT NULL,
    flags_json            TEXT NOT NULL DEFAULT '[]',
    constraints_checked   TEXT NOT NULL DEFAULT '[]',

    method_json           TEXT NOT NULL,
    provenance_json       TEXT NOT NULL,
    observation_ids       TEXT NOT NULL DEFAULT '[]',
    raster_path           TEXT,

    source                TEXT NOT NULL,
    dataset_id            TEXT NOT NULL,
    access_route          TEXT NOT NULL,
    spatial_ref           TEXT NOT NULL,
    resolution_m          DOUBLE,
    pipeline_version      TEXT NOT NULL,
    algorithm_version     TEXT NOT NULL,
    computed_at           TIMESTAMPTZ NOT NULL,

    UNIQUE (aoi_id, geometry_hash, indicator, period_start, period_end, algorithm_version)
);

CREATE INDEX IF NOT EXISTS idx_indicator_value_lookup
    ON indicator_value (geometry_hash, indicator, period_start);

CREATE INDEX IF NOT EXISTS idx_indicator_value_aoi_period
    ON indicator_value (aoi_id, period_start, period_end);

-- Composite wildfire substrate scores.
--
-- Computed in the pipeline, not at serve time, for the same reason the indicators are: the
-- weighting and normalisation are ecological judgements, and ecological judgements belong
-- in one place, versioned, behind the validation engine. The service layer reads this
-- table and reshapes it; it never re-derives a score.
CREATE TABLE IF NOT EXISTS substrate_score (
    score_id              TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL,
    aoi_id                TEXT NOT NULL,
    geometry_hash         TEXT NOT NULL,
    period_start          DATE NOT NULL,
    period_end            DATE NOT NULL,

    score                 DOUBLE NOT NULL,
    band                  TEXT NOT NULL,
    weighting_scheme      TEXT NOT NULL,
    components_json       TEXT NOT NULL,
    missing_indicators    TEXT NOT NULL DEFAULT '[]',
    interpretation        TEXT NOT NULL,
    caveats_json          TEXT NOT NULL DEFAULT '[]',

    validation_status     TEXT NOT NULL,
    confidence            DOUBLE NOT NULL,
    confidence_basis_json TEXT NOT NULL,
    flags_json            TEXT NOT NULL DEFAULT '[]',

    method_json           TEXT NOT NULL,
    provenance_json       TEXT NOT NULL,
    pipeline_version      TEXT NOT NULL,
    algorithm_version     TEXT NOT NULL,
    computed_at           TIMESTAMPTZ NOT NULL,

    UNIQUE (aoi_id, geometry_hash, period_start, period_end, weighting_scheme, algorithm_version)
);

CREATE INDEX IF NOT EXISTS idx_substrate_lookup
    ON substrate_score (geometry_hash, period_start, period_end);

-- Coarse grid cells backing the console map.
--
-- The full-resolution rasters stay on disk as COGs; this table holds a downsampled cell
-- grid so the map can be drawn, and a click on a cell answered, entirely in SQL. Each cell
-- inherits the provenance of the indicator value it was aggregated from, plus one step
-- recording the aggregation, so a pixel a user clicked is as citable as the area average.
CREATE TABLE IF NOT EXISTS indicator_cell (
    cell_id        TEXT PRIMARY KEY,
    value_id       TEXT NOT NULL,          -- parent indicator_value row
    aoi_id         TEXT NOT NULL,
    indicator      TEXT NOT NULL,
    period_start   DATE NOT NULL,
    period_end     DATE NOT NULL,
    west           DOUBLE NOT NULL,
    south          DOUBLE NOT NULL,
    east           DOUBLE NOT NULL,
    north          DOUBLE NOT NULL,
    value          DOUBLE,
    valid_fraction DOUBLE NOT NULL,
    confidence     DOUBLE NOT NULL,
    cell_size_m    DOUBLE NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cell_lookup
    ON indicator_cell (aoi_id, indicator, period_start);
