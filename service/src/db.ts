/**
 * Database access: the claim ledger, with the measurement lake attached read-only.
 *
 * The service never writes a measurement. Values are produced by the pipeline under a run
 * manifest and only read here. The one thing the service does write is the claim ledger —
 * a record of what it has served, so `get_provenance` can answer for a number later — and
 * that lives in its own file so the two writers never contend.
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { DuckDBInstance, type DuckDBConnection } from "@duckdb/node-api";
import { LakeUnavailableError } from "./errors.js";

let instance: DuckDBInstance | undefined;
let connection: DuckDBConnection | undefined;

function dataDir(): string {
  return resolve(process.env["GAIA_DATA_DIR"] ?? resolve(process.cwd(), "..", "data"));
}

/** The measurement lake, written by the pipeline and read here. */
export function lakePath(): string {
  const explicit = process.env["GAIA_DUCKDB_PATH"];
  if (explicit !== undefined && explicit !== "") return resolve(explicit);
  return resolve(dataDir(), "gaia.duckdb");
}

/** The claim ledger, written here and by nothing else. */
export function claimsPath(): string {
  const explicit = process.env["GAIA_CLAIMS_PATH"];
  if (explicit !== undefined && explicit !== "") return resolve(explicit);
  return resolve(dataDir(), "claims.duckdb");
}

function schemaFile(name: string): string {
  const here = dirname(fileURLToPath(import.meta.url));
  // dist/ at runtime, src/ under tsx; the repository root is two levels up from either.
  return readFileSync(resolve(here, "..", "..", "schema", name), "utf8");
}

/**
 * Open a read-only view of the measurement lake.
 *
 * The connection itself is in-memory with the lake attached read-only, deliberately. An
 * in-memory database takes no file lock of its own, and a read-only attach takes a shared
 * one, so any number of API and MCP processes can serve the same lake at once. Opening the
 * lake directly read-write would let the first process to start lock out every other.
 *
 * While an ingest is running, the pipeline holds the lake's exclusive lock and this fails.
 * That surfaces as a retryable `lake_unavailable`, which is the honest answer: the data is
 * mid-flight, ask again shortly.
 */
export async function connect(): Promise<DuckDBConnection> {
  if (connection !== undefined) return connection;

  const lake = lakePath();
  if (!existsSync(lake)) {
    throw new LakeUnavailableError(lake, "file does not exist");
  }

  try {
    instance = await DuckDBInstance.create(":memory:");
    connection = await instance.connect();
    await connection.run(`ATTACH '${lake.replace(/'/g, "''")}' AS lake (READ_ONLY)`);
    return connection;
  } catch (cause) {
    connection = undefined;
    instance = undefined;
    throw new LakeUnavailableError(lake, cause instanceof Error ? cause.message : String(cause));
  }
}

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

/**
 * Run a unit of work against the claim ledger on a short-lived connection.
 *
 * The ledger is the one thing the service writes, and DuckDB allows a single writer per
 * file — so an API process and an MCP process holding it open would lock each other out.
 * They are both expected to run at once, so the connection is opened for the duration of
 * the write and closed again, shrinking the contention window from the process lifetime to
 * a few milliseconds. Collisions inside that window retry with backoff.
 */
export async function withClaims<T>(fn: (conn: DuckDBConnection) => Promise<T>): Promise<T> {
  const attempts = 5;
  let lastError: unknown;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    let claimsInstance: DuckDBInstance | undefined;
    let claimsConnection: DuckDBConnection | undefined;
    try {
      claimsInstance = await DuckDBInstance.create(claimsPath(), { access_mode: "READ_WRITE" });
      claimsConnection = await claimsInstance.connect();
      await claimsConnection.run(schemaFile("claims.sql"));
      return await fn(claimsConnection);
    } catch (cause) {
      lastError = cause;
      const message = cause instanceof Error ? cause.message : String(cause);
      if (!message.includes("lock") || attempt === attempts - 1) break;
      await sleep(25 * 2 ** attempt);
    } finally {
      claimsConnection?.closeSync();
      claimsInstance?.closeSync();
    }
  }

  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

export async function close(): Promise<void> {
  connection?.closeSync();
  instance?.closeSync();
  connection = undefined;
  instance = undefined;
}

export type Row = Record<string, unknown>;

/** Run a parameterised query and return plain JS objects. */
export async function query(sql: string, params: unknown[] = []): Promise<Row[]> {
  const conn = await connect();
  const reader =
    params.length > 0
      ? await conn.runAndReadAll(sql, params as never[])
      : await conn.runAndReadAll(sql);
  return reader.getRowObjects() as Row[];
}

export async function queryOne(sql: string, params: unknown[] = []): Promise<Row | undefined> {
  const rows = await query(sql, params);
  return rows[0];
}

/** Write to the claim ledger. */
export async function execute(sql: string, params: unknown[] = []): Promise<void> {
  await withClaims(async (conn) => {
    if (params.length > 0) await conn.run(sql, params as never[]);
    else await conn.run(sql);
  });
}

/** Read from the claim ledger. */
export async function queryClaims(sql: string, params: unknown[] = []): Promise<Row[]> {
  return withClaims(async (conn) => {
    const reader =
      params.length > 0
        ? await conn.runAndReadAll(sql, params as never[])
        : await conn.runAndReadAll(sql);
    return reader.getRowObjects() as Row[];
  });
}

/** True when the lake exists and has at least one ingested area. */
export async function isPopulated(): Promise<boolean> {
  try {
    const row = await queryOne("SELECT count(*) AS n FROM lake.aoi");
    return Number(row?.["n"] ?? 0) > 0;
  } catch {
    return false;
  }
}

// --------------------------------------------------------------- value coercion

/**
 * DuckDB returns DECIMAL, BIGINT and TIMESTAMP as wrapper objects rather than JS
 * primitives. Everything crossing into a response envelope goes through these, so a
 * `17n` never reaches JSON.stringify and throws.
 */

export function num(value: unknown): number {
  if (value === null || value === undefined) return Number.NaN;
  if (typeof value === "number") return value;
  if (typeof value === "bigint") return Number(value);
  return Number(String(value));
}

export function numOrNull(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const n = num(value);
  return Number.isNaN(n) ? null : n;
}

export function str(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

export function strOrNull(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  return String(value);
}

/** DuckDB DATE values arrive as `{ days }` or as a string depending on the path taken. */
export function isoDate(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.slice(0, 10);
  if (typeof value === "object" && value !== null && "days" in value) {
    const days = Number((value as { days: unknown }).days);
    return new Date(days * 86_400_000).toISOString().slice(0, 10);
  }
  if (value instanceof Date) return value.toISOString().slice(0, 10);
  return String(value).slice(0, 10);
}

export function isoTimestamp(value: unknown): string {
  if (value === null || value === undefined) return new Date(0).toISOString();
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "string") return new Date(value).toISOString();
  if (typeof value === "object" && value !== null && "micros" in value) {
    const micros = Number((value as { micros: unknown }).micros);
    return new Date(micros / 1000).toISOString();
  }
  return new Date(String(value)).toISOString();
}

export function json<T>(value: unknown, fallback: T): T {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "object") return value as T;
  try {
    return JSON.parse(String(value)) as T;
  } catch {
    return fallback;
  }
}
