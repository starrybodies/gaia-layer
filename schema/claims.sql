-- The claim ledger.
--
-- Separate from the measurement lake, and for a structural reason rather than a tidiness
-- one: DuckDB permits a single writer per file. The pipeline holds the write lock on the
-- lake for the length of an ingest, so if claims lived there the API could not record what
-- it served while data was arriving. Splitting them lets the service own one file outright
-- and read the lake alongside it.
--
-- Claim ids are derived from the claim's content, not minted at random, so asking the same
-- question twice returns the same id and this table converges instead of growing forever.
-- Re-ingesting a period and getting a different number produces a new id, leaving the old
-- claim intact — a figure someone cited last month still resolves to what they were shown.

CREATE TABLE IF NOT EXISTS claim (
    claim_id        TEXT PRIMARY KEY,
    claim_kind      TEXT NOT NULL,            -- numeric | trend | substrate_score
    indicator       TEXT,
    aoi_id          TEXT,
    geometry_hash   TEXT NOT NULL,
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    value_repr      TEXT NOT NULL,
    unit            TEXT NOT NULL,
    confidence      DOUBLE NOT NULL,
    validation_json TEXT NOT NULL,
    method_json     TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    source_ids      TEXT NOT NULL DEFAULT '[]',
    payload_json    TEXT NOT NULL,
    served_at       TIMESTAMPTZ NOT NULL,
    last_served_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_claim_geometry ON claim (geometry_hash, period_start);

