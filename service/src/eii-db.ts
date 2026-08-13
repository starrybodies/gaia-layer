/**
 * The v0.2 archive, opened beside the v0.1 lake rather than inside it.
 *
 * Two spines, one query interface. v0.1 measures a 20 m projected grid over the coastal
 * pilot; v0.2 measures H3 resolution-8 hexes over the interior. Forcing either onto the
 * other damages whichever one loses, so they are separate stores and this module owns the
 * second one.
 *
 * The archive is Parquet partitioned by component and year, with a small DuckDB catalog
 * holding the dimension tables that provenance resolves against. DuckDB reads the Parquet
 * directly through `read_parquet`, so nothing has to be loaded into the database first and
 * a rebuilt year is visible the moment the pipeline writes it.
 *
 * The connection is in-memory with the catalog attached read-only, for the same reason the
 * v0.1 lake is: a read-only attach takes a shared lock, so any number of API and MCP
 * processes can serve the same archive at once, and a pipeline run that holds the write
 * lock surfaces as a retryable error rather than a corrupt read.
 */

import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { DuckDBInstance, type DuckDBConnection } from "@duckdb/node-api";
import { LakeUnavailableError } from "./errors.js";

let instance: DuckDBInstance | undefined;
let connection: DuckDBConnection | undefined;

function dataDir(): string {
  return resolve(process.env["GAIA_DATA_DIR"] ?? resolve(process.cwd(), "..", "data"));
}

/** Where the v0.2 archive lives. `GAIA_EII_DIR` short-circuits the search. */
export function archiveDir(): string {
  const explicit = process.env["GAIA_EII_DIR"];
  if (explicit !== undefined && explicit !== "") return resolve(explicit);

  const candidates = [
    resolve(dataDir(), "eii"),
    resolve(process.cwd(), "data", "eii"),
    resolve(process.cwd(), "..", "data", "eii"),
  ];
  return candidates.find((candidate) => existsSync(candidate)) ?? (candidates[0] as string);
}

export function catalogPath(): string {
  return resolve(archiveDir(), "catalog.duckdb");
}

/** The glob a component's facts live under, escaped for inlining into SQL. */
export function partitionGlob(component: string, year?: number): string {
  const yearPart = year === undefined ? "year=*" : `year=${year}`;
  return resolve(archiveDir(), `component=${component}`, yearPart, "*.parquet").replace(
    /'/g,
    "''",
  );
}

export async function connectArchive(): Promise<DuckDBConnection> {
  if (connection !== undefined) return connection;

  const catalog = catalogPath();
  if (!existsSync(catalog)) {
    throw new LakeUnavailableError(catalog, "the EII archive has not been built");
  }

  try {
    instance = await DuckDBInstance.create(":memory:");
    connection = await instance.connect();
    await connection.run(`ATTACH '${catalog.replace(/'/g, "''")}' AS eii (READ_ONLY)`);
    return connection;
  } catch (error) {
    instance = undefined;
    connection = undefined;
    throw new LakeUnavailableError(catalog, error instanceof Error ? error.message : String(error));
  }
}

export async function closeArchive(): Promise<void> {
  connection?.closeSync();
  instance?.closeSync();
  connection = undefined;
  instance = undefined;
}

export type Row = Record<string, unknown>;

export async function queryArchive(sql: string, params: unknown[] = []): Promise<Row[]> {
  const conn = await connectArchive();
  const prepared = await conn.prepare(sql);
  params.forEach((value, index) => prepared.bindValue(index + 1, value as never));
  const result = await prepared.runAndReadAll();
  return result.getRowObjects() as Row[];
}

export async function archiveExists(): Promise<boolean> {
  return existsSync(catalogPath());
}

/**
 * Whether a component has any partition on disk at all.
 *
 * "Not built" and "built and unmeasurable here" are different facts, and only the second is
 * about the cell. A component the pipeline has never run has no directory, and asking DuckDB
 * to glob it raises an IO error that would surface to an agent as a failure of the whole
 * call rather than as the absence it is.
 */
export function componentBuilt(component: string): boolean {
  return existsSync(resolve(archiveDir(), `component=${component}`));
}
