/**
 * Read-only access to the DuckDB data lake.
 *
 * The service layer never writes. Values are produced by the pipeline, under a run
 * manifest, and only read here — with one exception, the `claim` table, which records what
 * has been served so `get_provenance` can answer for it later.
 */

import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { DuckDBInstance, type DuckDBConnection } from "@duckdb/node-api";
import { LakeUnavailableError } from "./errors.js";

let instance: DuckDBInstance | undefined;
let connection: DuckDBConnection | undefined;

export function lakePath(): string {
  const explicit = process.env["GAIA_DUCKDB_PATH"];
  if (explicit !== undefined && explicit !== "") return resolve(explicit);
  const dataDir = process.env["GAIA_DATA_DIR"] ?? resolve(process.cwd(), "..", "data");
  return resolve(dataDir, "gaia.duckdb");
}

export async function connect(): Promise<DuckDBConnection> {
  if (connection !== undefined) return connection;

  const path = lakePath();
  if (!existsSync(path)) {
    throw new LakeUnavailableError(path, "file does not exist");
  }
  try {
    instance = await DuckDBInstance.create(path, { access_mode: "READ_WRITE" });
    connection = await instance.connect();
    await connection.run("INSTALL spatial; LOAD spatial;");
    return connection;
  } catch (cause) {
    throw new LakeUnavailableError(path, cause instanceof Error ? cause.message : String(cause));
  }
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

export async function execute(sql: string, params: unknown[] = []): Promise<void> {
  const conn = await connect();
  if (params.length > 0) {
    await conn.run(sql, params as never[]);
  } else {
    await conn.run(sql);
  }
}

/** True when the lake exists and has at least one ingested area. */
export async function isPopulated(): Promise<boolean> {
  try {
    const row = await queryOne("SELECT count(*) AS n FROM aoi");
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
