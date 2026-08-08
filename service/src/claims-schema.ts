/**
 * DDL for the claim ledger.
 *
 * Held in TypeScript rather than a `.sql` file next to `lake.sql` because the service is
 * the only thing that ever touches this table — a shared file would be a runtime file
 * dependency with no second reader, and one that a bundled deployment would have to be
 * taught to carry.
 *
 * `lake.sql` stays a shared file, because Python writes it and TypeScript reads it.
 */

export const CLAIMS_DDL = `
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
`;
