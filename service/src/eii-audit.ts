/**
 * The append-only audit log.
 *
 * The specification asks for every tool call and response to be appended to an audit table.
 * The reason is not compliance theatre: an underwriting file cites a number, and a year
 * later somebody has to establish what this service actually said on the day the file was
 * written. A claim id proves what the number was; the audit log proves it was asked for.
 *
 * Append-only means append-only. There is no update and no delete on this path — not
 * because a delete is hard to write, but because a log that can be edited answers no
 * question worth asking. The response is stored as a digest rather than a copy: the payload
 * can be large and can contain the caller's own geometry, and the digest is enough to prove
 * that a served response matches a recorded one.
 *
 * A failure to write the audit row must not fail the call. An agent that cannot get an
 * answer because the log is unwritable is worse off than one that gets an answer nobody
 * recorded, so the failure is logged to stderr and the answer goes out.
 */

import { createHash } from "node:crypto";
import { existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { DuckDBInstance, type DuckDBConnection } from "@duckdb/node-api";
import { archiveDir } from "./eii-db.js";

const AUDIT_DDL = `
CREATE TABLE IF NOT EXISTS audit_log (
    entry_id  VARCHAR PRIMARY KEY,
    tool      VARCHAR NOT NULL,
    request   VARCHAR NOT NULL,
    response_digest VARCHAR NOT NULL,
    rows_returned INTEGER,
    called    TIMESTAMPTZ NOT NULL
);
`;

let instance: DuckDBInstance | undefined;
let connection: DuckDBConnection | undefined;

export function auditPath(): string {
  const explicit = process.env["GAIA_EII_AUDIT_PATH"];
  if (explicit !== undefined && explicit !== "") return resolve(explicit);
  return resolve(archiveDir(), "audit.duckdb");
}

async function auditConnection(): Promise<DuckDBConnection> {
  if (connection !== undefined) return connection;

  const path = auditPath();
  const directory = dirname(path);
  if (!existsSync(directory)) mkdirSync(directory, { recursive: true });

  instance = await DuckDBInstance.create(path);
  connection = await instance.connect();
  await connection.run(AUDIT_DDL);
  return connection;
}

export async function closeAudit(): Promise<void> {
  connection?.closeSync();
  instance?.closeSync();
  connection = undefined;
  instance = undefined;
}

/** The digest a caller can recompute to prove a response is the one that was served. */
export function responseDigest(payload: unknown): string {
  return createHash("sha256").update(JSON.stringify(payload)).digest("hex");
}

export interface AuditEntry {
  entry_id: string;
  tool: string;
  response_digest: string;
  rows_returned: number;
  called: string;
}

/**
 * Append one call to the log and return what was written.
 *
 * Returns the entry even when the write failed, so the caller can still hand the digest
 * back to the agent. What it cannot do is claim the row exists when it does not, which is
 * why the failure is announced on stderr rather than swallowed.
 */
export async function recordCall(
  tool: string,
  request: unknown,
  response: unknown,
  rowsReturned: number,
): Promise<AuditEntry> {
  const digest = responseDigest(response);
  const called = new Date().toISOString();
  const entry: AuditEntry = {
    entry_id: createHash("sha256")
      .update(`${tool}:${JSON.stringify(request)}:${digest}:${called}`)
      .digest("hex")
      .slice(0, 32),
    tool,
    response_digest: digest,
    rows_returned: rowsReturned,
    called,
  };

  try {
    const conn = await auditConnection();
    const prepared = await conn.prepare(
      "INSERT INTO audit_log VALUES ($1, $2, $3, $4, $5, $6::TIMESTAMPTZ)",
    );
    prepared.bindValue(1, entry.entry_id);
    prepared.bindValue(2, entry.tool);
    prepared.bindValue(3, JSON.stringify(request));
    prepared.bindValue(4, entry.response_digest);
    prepared.bindValue(5, entry.rows_returned);
    prepared.bindValue(6, entry.called);
    await prepared.run();
  } catch (error) {
    // An unwritable log is not a reason to withhold an answer.
    console.error(
      `[gaia-eii] audit write failed for ${tool}: ${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  }

  return entry;
}

/** Read the log back, newest first. For the operator, not for the agent. */
export async function readAudit(limit = 100): Promise<AuditEntry[]> {
  const conn = await auditConnection();
  const prepared = await conn.prepare(
    "SELECT entry_id, tool, response_digest, rows_returned, called FROM audit_log " +
      "ORDER BY called DESC LIMIT $1",
  );
  prepared.bindValue(1, limit);
  const result = await prepared.runAndReadAll();
  return result.getRowObjects().map((row) => ({
    entry_id: String(row["entry_id"]),
    tool: String(row["tool"]),
    response_digest: String(row["response_digest"]),
    rows_returned: Number(row["rows_returned"] ?? 0),
    called: new Date(String(row["called"])).toISOString(),
  }));
}
