/**
 * The EII agent surface: four resources and five tools.
 *
 * Both transports bind here, the same way they bind to `tools.ts` for v0.1. Neither adds
 * logic of its own, which is the only way an MCP client and an HTTP client can be
 * guaranteed to get the same answer about the same cell.
 *
 * Three things every response carries, and the reasons are not decoration.
 *
 * **Provenance**, resolved from the archive's dimension tables rather than stored on each
 * row. What a caller receives is the full chain — datasets, versions, access routes, native
 * resolutions, citations, and the moment each was retrieved. Twenty-five million copies of
 * that paragraph on disk would be several gigabytes to say one thing repeatedly.
 *
 * **Uncertainty**, as the component's own standard error rather than a confidence badge. A
 * value with no uncertainty is a value nobody can price against.
 *
 * **`method_justification`**, which is the field that says what the number cannot support.
 * A cell is 0.74 km2 and the reanalysis behind two of its five components is 25 km. An agent
 * that reads the value without reading that will put a parcel-level claim on a
 * landscape-level measurement, and nothing else in the response would stop it.
 *
 * `explain_score` reads precomputed per-component contributions rather than invoking a live
 * model, the same way v0.1 serves its substrate decomposition. An explanation regenerated
 * per call is an explanation that can differ from the one in last week's file.
 */

import { assertProvenanced } from "@gaia/core";
import { recordCall, type AuditEntry } from "./eii-audit.js";
import { archiveDir, componentBuilt, partitionGlob, queryArchive } from "./eii-db.js";
// Imported into the dispatcher rather than duplicated in each transport. The rule this file
// is held to is that a behaviour present in one transport and absent from the other is in
// the wrong place, and one dispatcher is how that stays true.
import { readDossier } from "./eii-artifacts.js";
import {
  portfolioChange,
  portfolioRanking,
  type PortfolioChangeRequest,
  type PortfolioRankingRequest,
} from "./eii-portfolio.js";
import { NoDataForPeriodError, ServiceError } from "./errors.js";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

export const EII_TOOL_NAMES = [
  "get_eii",
  "get_component",
  "explain_score",
  "compare_baseline",
  "portfolio_scan",
  "portfolio_ranking",
  "portfolio_change",
  "read_dossier",
] as const;

export type EiiToolName = (typeof EII_TOOL_NAMES)[number];

export const EII_RESOURCE_URIS = [
  "eii://schema",
  "eii://catalog",
  "eii://methodology",
  "eii://validation",
] as const;

export type EiiResourceUri = (typeof EII_RESOURCE_URIS)[number];

/** The composite, and the five components that make it. */
export const COMPONENTS = [
  "a_structure",
  "b_water",
  "c_riparian",
  "d_moisture",
  "e_drought",
] as const;

export const COMPOSITE = "eii";

/**
 * What the index is and is not, in one place, attached to everything that leaves here.
 *
 * The orientation matters more than any other sentence in this file. "Ecosystem Integrity
 * Index" reads as a scale where higher is healthier. It is not one: it is a departure scale
 * where higher is the direction associated with more severe fire. An agent that assumes the
 * intuitive reading gets every conclusion backwards, so it is stated on every response
 * rather than in documentation somebody may not have loaded.
 */
export const ORIENTATION =
  "Higher is worse. Every component is a departure oriented so that positive is the " +
  "direction associated with more severe fire: more structure than its biogeoclimatic " +
  "context, drier than this place's own normal, less riparian influence than normal, fire " +
  "weather codes above their own seasonal distribution, deeper drought. The name is " +
  "inherited from the v0.2 specification; the scale is a departure scale, not a health score.";

const CELL_AREA_KM2 = 0.74;

function justification(component: string, validFraction: number): string {
  const base =
    `Measured on an H3 resolution-8 cell of about ${CELL_AREA_KM2} km2. This cannot support ` +
    "a parcel-level or building-level claim: it is landscape condition, not property " +
    "vulnerability.";

  const coarse =
    component === "b_water" || component === "d_moisture" || component === "e_drought"
      ? " The reanalysis behind this component is 9-25 km and is interpolated to the cell; " +
        "the cell-to-cell variation it shows within a single reanalysis node is interpolation, " +
        "not measurement."
      : "";

  const thin =
    validFraction < 1
      ? " Part of this cell carried no measurement; see valid_fraction."
      : "";

  return `${base}${coarse}${thin}`;
}

export interface EiiProvenanceStep {
  dataset: string;
  version: string;
  access_route: string;
  uri: string;
  native_resolution_m: number | null;
  native_timestep: string | null;
  citation: string;
  licence: string | null;
  retrieved: string;
}

export interface EiiValue {
  value: number | null;
  /**
   * Flat, not nested under an `uncertainty` object, and the reason is the guard rather than
   * taste. The provenance guard treats any object carrying a `value` key as a served claim
   * and requires a chain beside it, so `uncertainty: { value }` reads as a second claim with
   * no provenance. Carving an exception into the guard to allow it would weaken the one
   * mechanism this layer is actually about; the flat shape is also what v0.1's envelope uses.
   */
  uncertainty_type: string;
  uncertainty_value: number | null;
  valid_fraction: number;
  confidence: number;
  validation_status: string;
  constraint_flags: string[];
  method: { method_id: string; name: string; version: string; formula: string | null };
  method_justification: string;
  provenance: EiiProvenanceStep[];
  claim_id: string;
}

export interface FactRow {
  h3: string;
  component: string;
  value: number | null;
  uncertainty_value: number | null;
  uncertainty_type: string;
  valid_fraction: number;
  constraint_flags: string;
  method_id: string;
  run_id: string;
  source_set_id: string;
  period_start: string;
  period_end: string;
}

function isoDay(value: unknown): string {
  if (value instanceof Date) return value.toISOString().slice(0, 10);
  return String(value).slice(0, 10);
}

function numberOrNull(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Confidence from the component's own standard error, on nought to one.
 *
 * A departure is in z units, so a standard error of 1 means the doubt is the size of the
 * spread the departure is measured against — a value that says nothing. Mapping through
 * `1 / (1 + se)` puts that at 0.5 and a tenth of a standard deviation at 0.91, which is the
 * shape the v0.1 layer already uses. A value with no uncertainty behind it is not confident,
 * it is unmeasured, and gets zero.
 */
function confidenceFrom(uncertainty: number | null, value: number | null): number {
  if (value === null) return 0;
  if (uncertainty === null || !Number.isFinite(uncertainty)) return 0.25;
  return Math.max(0, Math.min(1, 1 / (1 + Math.abs(uncertainty))));
}

async function provenanceFor(runId: string): Promise<EiiProvenanceStep[]> {
  const rows = await queryArchive(
    `SELECT s.dataset, s.version, s.access_route, s.uri, s.native_resolution_m,
            s.native_timestep, s.citation, s.licence, s.retrieved
       FROM eii.run r
       JOIN eii.source_set_member m ON m.source_set_id = r.source_set_id
       JOIN eii.source s ON s.source_id = m.source_id
      WHERE r.run_id = $1
      ORDER BY s.dataset`,
    [runId],
  );

  return rows.map((row) => ({
    dataset: String(row["dataset"]),
    version: String(row["version"]),
    access_route: String(row["access_route"]),
    uri: String(row["uri"]),
    native_resolution_m: numberOrNull(row["native_resolution_m"]),
    native_timestep: row["native_timestep"] === null ? null : String(row["native_timestep"]),
    citation: String(row["citation"]),
    licence: row["licence"] === null ? null : String(row["licence"]),
    retrieved: new Date(String(row["retrieved"])).toISOString(),
  }));
}

async function methodFor(methodId: string): Promise<EiiValue["method"]> {
  const rows = await queryArchive(
    "SELECT method_id, name, version, formula FROM eii.method WHERE method_id = $1",
    [methodId],
  );
  const row = rows[0];
  if (row === undefined) {
    throw new ServiceError("internal", `no method record for ${methodId}`);
  }
  return {
    method_id: String(row["method_id"]),
    name: String(row["name"]),
    version: String(row["version"]),
    formula: row["formula"] === null ? null : String(row["formula"]),
  };
}

export async function factsFor(
  component: string,
  cells: string[],
  year?: number,
): Promise<FactRow[]> {
  if (cells.length === 0) return [];
  // A component the pipeline has not run yet is absent, not broken.
  if (!componentBuilt(component)) return [];
  const glob = partitionGlob(component, year);
  const placeholders = cells.map((_, index) => `$${index + 1}`).join(", ");
  const rows = await queryArchive(
    `SELECT * FROM read_parquet('${glob}') WHERE h3 IN (${placeholders})`,
    cells,
  );
  return rows.map((row) => ({
    h3: String(row["h3"]),
    component: String(row["component"]),
    value: numberOrNull(row["value"]),
    uncertainty_value: numberOrNull(row["uncertainty_value"]),
    uncertainty_type: String(row["uncertainty_type"]),
    valid_fraction: Number(row["valid_fraction"] ?? 0),
    constraint_flags: String(row["constraint_flags"] ?? ""),
    method_id: String(row["method_id"]),
    run_id: String(row["run_id"]),
    source_set_id: String(row["source_set_id"]),
    period_start: isoDay(row["period_start"]),
    period_end: isoDay(row["period_end"]),
  }));
}

async function toValue(row: FactRow): Promise<EiiValue> {
  const [method, provenance] = await Promise.all([
    methodFor(row.method_id),
    provenanceFor(row.run_id),
  ]);

  const flags = row.constraint_flags
    .split("|")
    .map((flag) => flag.trim())
    .filter((flag) => flag.length > 0);

  return {
    value: row.value,
    uncertainty_type: row.uncertainty_type,
    uncertainty_value: row.uncertainty_value,
    valid_fraction: row.valid_fraction,
    confidence: confidenceFrom(row.uncertainty_value, row.value),
    // A value that was clamped by the constraint layer is served, and is served flagged.
    validation_status: row.value === null ? "unmeasured" : flags.length > 0 ? "flagged" : "ok",
    constraint_flags: flags,
    method,
    method_justification: justification(row.component, row.valid_fraction),
    provenance,
    claim_id: `eii:${row.component}:${row.h3}:${row.period_end}:${row.run_id}`,
  };
}

function guard<T>(payload: T): T {
  if (process.env["GAIA_STRICT_GUARD"] === "1") assertProvenanced(payload);
  return payload;
}

// ------------------------------------------------------------------------------- resources

export interface Resource {
  uri: string;
  name: string;
  description: string;
  mimeType: string;
  text: string;
}

const SCHEMA_RESOURCE = {
  fact_columns: {
    h3: "H3 resolution-8 cell id, about 0.74 km2.",
    period_start: "First day the value describes.",
    period_end: "The as-of date. Components are departures taken as of a day, not a year.",
    component: "One of the five components, or 'eii' for the composite.",
    value: "The departure, in z units. Null where the cell could not be measured.",
    uncertainty_type: "Always 'standard_error' in this release.",
    uncertainty_value: "Standard error of the value, in the same z units.",
    valid_fraction: "Share of the cell that carried a measurement.",
    method_id: "Resolves against the method table.",
    run_id: "Resolves against the run table, and through it to the source set.",
    source_set_id: "Resolves against source_set_member and source.",
    constraint_flags: "Pipe-separated flags. Empty means no rule fired.",
  },
  orientation: ORIENTATION,
  missing_values:
    "Null means not measured. It never means zero. Zero is the middle of a departure " +
    "scale and is the strongest available claim of ordinariness.",
  aggregation:
    "Resolution 7 is the portfolio aggregation, about 5.2 km2 and seven resolution-8 " +
    "cells to a parent.",
};

async function catalogResource(): Promise<Record<string, unknown>> {
  const rows = await queryArchive(
    "SELECT component, year, rows, run_id, written FROM eii.partition_index ORDER BY component, year",
  );
  const cells = await queryArchive("SELECT count(*) AS n FROM eii.h3_cell");

  return {
    archive_dir: archiveDir(),
    cells: Number(cells[0]?.["n"] ?? 0),
    partitions: rows.map((row) => ({
      component: String(row["component"]),
      year: Number(row["year"]),
      rows: Number(row["rows"]),
      run_id: String(row["run_id"]),
      written: new Date(String(row["written"])).toISOString(),
    })),
  };
}

async function methodologyResource(): Promise<Record<string, unknown>> {
  const rows = await queryArchive(
    "SELECT method_id, name, version, formula, citation, doi, notes FROM eii.method ORDER BY method_id",
  );
  return {
    orientation: ORIENTATION,
    weighting:
      "The composite is an equal-weighted mean of whichever components a cell has. Equal " +
      "weights are an admission that nothing in this build establishes a ranking among " +
      "them, not a claim that they matter equally. Only Component A has been through a " +
      "validation gate.",
    methods: rows.map((row) => ({
      method_id: String(row["method_id"]),
      name: String(row["name"]),
      version: String(row["version"]),
      formula: row["formula"] === null ? null : String(row["formula"]),
      citation: String(row["citation"]),
      doi: row["doi"] === null ? null : String(row["doi"]),
      notes: row["notes"] === null ? null : String(row["notes"]),
    })),
  };
}

function validationResource(): Record<string, unknown> {
  const path = resolve(archiveDir(), "validation.json");
  if (!existsSync(path)) {
    return {
      available: false,
      reason: "the validation experiment has not been run against this archive",
    };
  }
  return { available: true, ...(JSON.parse(readFileSync(path, "utf8")) as object) };
}

export async function readResource(uri: string): Promise<Resource> {
  const payload = await (async (): Promise<Record<string, unknown>> => {
    switch (uri) {
      case "eii://schema":
        return SCHEMA_RESOURCE;
      case "eii://catalog":
        return catalogResource();
      case "eii://methodology":
        return methodologyResource();
      case "eii://validation":
        return validationResource();
      default:
        throw new ServiceError("invalid_request", `no resource at ${uri}`);
    }
  })();

  return {
    uri,
    name: uri.replace("eii://", ""),
    description: `EII ${uri.replace("eii://", "")}`,
    mimeType: "application/json",
    text: JSON.stringify(payload, null, 2),
  };
}

export const RESOURCE_DEFINITIONS = [
  {
    uri: "eii://schema",
    name: "schema",
    description:
      "What a fact row means, what null means, and which way the scale runs. Read this " +
      "before interpreting any value: higher is worse, not better.",
    mimeType: "application/json",
  },
  {
    uri: "eii://catalog",
    name: "catalog",
    description: "Which components and years the archive holds, and when each was written.",
    mimeType: "application/json",
  },
  {
    uri: "eii://methodology",
    name: "methodology",
    description:
      "Every method record: formula, citation, and the notes stating what each component " +
      "cannot establish. Includes the weighting and why it is equal.",
    mimeType: "application/json",
  },
  {
    uri: "eii://validation",
    name: "validation",
    description:
      "The pre-registered gate, its verdict, and every model's metrics. Reports a negative " +
      "result as plainly as a positive one.",
    mimeType: "application/json",
  },
] as const;

// ----------------------------------------------------------------------------------- tools

export interface CellRequest {
  h3: string;
  year?: number;
}

async function requireFact(component: string, h3: string, year?: number): Promise<FactRow> {
  const rows = await factsFor(component, [h3], year);
  const row = rows[0];
  if (row === undefined) {
    throw new NoDataForPeriodError(
      `no ${component} for cell ${h3}${year === undefined ? "" : ` in ${year}`}`,
    );
  }
  return row;
}

export interface EiiResponse {
  h3: string;
  as_of: string;
  index: EiiValue;
  orientation: string;
  audit: AuditEntry;
}

export async function getEii(request: CellRequest): Promise<EiiResponse> {
  const row = await requireFact(COMPOSITE, request.h3, request.year);
  const payload = {
    h3: row.h3,
    as_of: row.period_end,
    index: await toValue(row),
    orientation: ORIENTATION,
  };
  guard(payload);
  const audit = await recordCall("get_eii", request, payload, 1);
  return { ...payload, audit };
}

export interface ComponentResponse {
  h3: string;
  as_of: string;
  component: string;
  measurement: EiiValue;
  orientation: string;
  audit: AuditEntry;
}

export async function getComponent(
  request: CellRequest & { component: string },
): Promise<ComponentResponse> {
  if (!COMPONENTS.includes(request.component as (typeof COMPONENTS)[number])) {
    throw new ServiceError(
      "invalid_request",
      `component must be one of ${COMPONENTS.join(", ")}`,
    );
  }
  const row = await requireFact(request.component, request.h3, request.year);
  const payload = {
    h3: row.h3,
    as_of: row.period_end,
    component: request.component,
    measurement: await toValue(row),
    orientation: ORIENTATION,
  };
  guard(payload);
  const audit = await recordCall("get_component", request, payload, 1);
  return { ...payload, audit };
}

export interface Explanation {
  h3: string;
  as_of: string;
  index: EiiValue;
  contributions: {
    component: string;
    measurement: EiiValue;
    share_of_index: number | null;
  }[];
  missing_components: string[];
  /** Components the pipeline has not run at all, which is not a fact about this cell. */
  components_not_built: string[];
  reading: string;
  orientation: string;
  audit: AuditEntry;
}

/**
 * Why this cell scored what it scored, from the stored components rather than a live model.
 *
 * The share is each component's signed contribution to the equal-weighted mean, so the
 * shares of the components present sum to one. A component the cell does not have is named
 * in `missing_components` rather than given a share of zero, which would read as a component
 * that contributed nothing rather than one that was never measured.
 */
export async function explainScore(request: CellRequest): Promise<Explanation> {
  const index = await requireFact(COMPOSITE, request.h3, request.year);
  const rows = await Promise.all(
    COMPONENTS.map(async (component) => {
      const found = await factsFor(component, [request.h3], request.year);
      return { component, row: found[0] };
    }),
  );

  const present = rows.filter((entry) => entry.row !== undefined && entry.row.value !== null);
  const total = present.reduce((sum, entry) => sum + Math.abs(entry.row?.value ?? 0), 0);

  const contributions = await Promise.all(
    present.map(async (entry) => ({
      component: entry.component,
      measurement: await toValue(entry.row as FactRow),
      share_of_index: total === 0 ? null : Math.abs((entry.row as FactRow).value ?? 0) / total,
    })),
  );

  const missing = rows
    .filter((entry) => entry.row === undefined || entry.row.value === null)
    .map((entry) => entry.component);
  const notBuilt = COMPONENTS.filter((component) => !componentBuilt(component));

  const worst = contributions.reduce<(typeof contributions)[number] | undefined>(
    (best, entry) =>
      best === undefined || (entry.measurement.value ?? -Infinity) > (best.measurement.value ?? -Infinity)
        ? entry
        : best,
    undefined,
  );

  const payload = {
    h3: index.h3,
    as_of: index.period_end,
    index: await toValue(index),
    contributions,
    missing_components: missing,
    components_not_built: notBuilt,
    reading:
      worst === undefined
        ? "No component was measurable for this cell."
        : `The largest departure is ${worst.component} at ${(worst.measurement.value ?? 0).toFixed(
            2,
          )} z. ${
            missing.length > 0
              ? `Scored without ${missing.join(", ")}${
                  notBuilt.length > 0
                    ? ` (${notBuilt.join(", ")} ${notBuilt.length === 1 ? "has" : "have"} not been built for this archive)`
                    : ""
                }.`
              : "All five components contributed."
          }`,
    orientation: ORIENTATION,
  };
  guard(payload);
  const audit = await recordCall("explain_score", request, payload, contributions.length);
  return { ...payload, audit };
}

export interface BaselineComparison {
  gate_statement: string;
  verdict: string;
  gate_delta: unknown;
  attribution_delta: unknown;
  calibration_delta: unknown;
  models: unknown;
  caveat: string;
  audit: AuditEntry;
}

/**
 * What the index adds over fire weather and a fuel map, in the gate's own words.
 *
 * Served from the same `validation.json` the pipeline writes, so this cannot drift from the
 * report an actuary was shown. If the gate had failed, this would say so in the same place
 * and with the same prominence.
 */
export async function compareBaseline(): Promise<BaselineComparison> {
  const validation = validationResource();
  if (validation["available"] !== true) {
    throw new NoDataForPeriodError(
      "no validation run is recorded for this archive, so there is nothing to compare",
    );
  }

  // Which of the others actually exist here, so the caveat describes this archive rather
  // than the one the specification imagined. Claiming a component is "built and served"
  // when it is not is the same class of overstatement this whole layer exists to avoid.
  const others = COMPONENTS.filter((component) => component !== "a_structure");
  const built = others.filter((component) => componentBuilt(component));
  const absent = others.filter((component) => !componentBuilt(component));

  const payload = {
    gate_statement: String(validation["gate_statement"] ?? ""),
    verdict: String(validation["verdict"] ?? "NOT EVALUATED"),
    gate_delta: validation["gate_delta"],
    attribution_delta: validation["attribution_delta"],
    calibration_delta: validation["calibration_delta"],
    models: validation["models"],
    caveat:
      "Only Component A has been through this gate. " +
      (built.length > 0
        ? `${built.join(", ")} ${built.length === 1 ? "is" : "are"} built and served but ` +
          "unvalidated against a held-out target. "
        : "") +
      (absent.length > 0
        ? `${absent.join(", ")} ${absent.length === 1 ? "is" : "are"} not built in this ` +
          "archive at all, so no cell here carries them. "
        : "") +
      "The composite inherits both: its equal weights are a stated ignorance, not a fitted " +
      "result.",
  };
  const audit = await recordCall("compare_baseline", {}, payload, 1);
  return { ...payload, audit };
}

export interface PortfolioRequest {
  cells: string[];
  year?: number;
  threshold?: number;
}

export interface PortfolioScan {
  as_of: string | null;
  requested: number;
  scored: number;
  unmeasured: string[];
  mean_index: number | null;
  worst: { h3: string; value: number }[];
  above_threshold: { threshold: number; count: number; share: number };
  orientation: string;
  method_justification: string;
  audit: AuditEntry;
}

/**
 * The index across a set of cells, for a book of exposures rather than one location.
 *
 * Cells with no measurement are named, not dropped. A portfolio scan that quietly reports a
 * mean over the cells it could measure is a scan that gets better-looking as coverage gets
 * worse, and the caller has no way to see it happening.
 */
export async function portfolioScan(request: PortfolioRequest): Promise<PortfolioScan> {
  const cells = [...new Set(request.cells)];
  if (cells.length === 0) {
    throw new ServiceError("invalid_request", "no cells were given to scan");
  }

  const rows = await factsFor(COMPOSITE, cells, request.year);
  const byCell = new Map(rows.map((row) => [row.h3, row]));

  const scored = rows.filter((row) => row.value !== null);
  const unmeasured = cells.filter((cell) => (byCell.get(cell)?.value ?? null) === null);

  const threshold = request.threshold ?? 1.0;
  const above = scored.filter((row) => (row.value as number) >= threshold);
  const ordered = [...scored].sort((a, b) => (b.value as number) - (a.value as number));

  const payload = {
    as_of: rows[0]?.period_end ?? null,
    requested: cells.length,
    scored: scored.length,
    unmeasured,
    mean_index:
      scored.length === 0
        ? null
        : scored.reduce((sum, row) => sum + (row.value as number), 0) / scored.length,
    worst: ordered.slice(0, 10).map((row) => ({ h3: row.h3, value: row.value as number })),
    above_threshold: {
      threshold,
      count: above.length,
      share: scored.length === 0 ? 0 : above.length / scored.length,
    },
    orientation: ORIENTATION,
    method_justification:
      `Scored ${scored.length} of ${cells.length} cells. The mean is over the scored cells ` +
      "only; the unmeasured ones are listed rather than averaged away, because a mean that " +
      "improves as coverage falls is not a portfolio statistic.",
  };
  const audit = await recordCall("portfolio_scan", request, payload, scored.length);
  return { ...payload, audit };
}

export async function callEiiTool(name: string, args: Record<string, unknown>): Promise<unknown> {
  switch (name) {
    case "get_eii":
      return getEii(args as unknown as CellRequest);
    case "get_component":
      return getComponent(args as unknown as CellRequest & { component: string });
    case "explain_score":
      return explainScore(args as unknown as CellRequest);
    case "compare_baseline":
      return compareBaseline();
    case "portfolio_scan":
      return portfolioScan(args as unknown as PortfolioRequest);
    case "portfolio_ranking":
      return portfolioRanking(args as unknown as PortfolioRankingRequest);
    case "portfolio_change":
      return portfolioChange(args as unknown as PortfolioChangeRequest);
    case "read_dossier":
      return readDossier();
    default:
      throw new ServiceError("invalid_request", `no EII tool named ${name}`);
  }
}
