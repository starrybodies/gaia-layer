/**
 * The five agent-facing capabilities.
 *
 * Both transports bind here. The MCP server calls these functions; so does the REST API.
 * Neither adds logic of its own, which is the only way two interfaces can be guaranteed to
 * say the same thing about the same area.
 */

import { assertProvenanced } from "@gaia/core";
import { claimIdFor, recordClaims, type ClaimRecord } from "./claims.js";
import {
  isoDate,
  isoTimestamp,
  json,
  num,
  numOrNull,
  query,
  queryClaims,
  queryOne,
  str,
} from "./db.js";
import {
  AoiNotIngestedError,
  ClaimNotFoundError,
  NoDataForPeriodError,
  ServiceError,
} from "./errors.js";
import { geometryHash, type GeometryInput } from "./geometry.js";
import {
  claimForNumeric,
  claimRecordFor,
  decodeIndicatorRow,
  formatValue,
  toNumericEnvelope,
  toRejectedValue,
  type IndicatorRow,
  type NumericEnvelope,
  type RejectedValue,
} from "./rows.js";
import { welchTTest } from "./stats.js";
import { interpretSubstrateBand, summariseComparison, summariseState } from "./summary.js";
import { computeTrend, toTrendEnvelope, type TrendEnvelope } from "./trends.js";

export const TOOL_NAMES = [
  "get_ecological_state",
  "get_wildfire_substrate_score",
  "get_provenance",
  "compare_periods",
  "list_coverage",
] as const;

export type ToolName = (typeof TOOL_NAMES)[number];

export interface DateRange {
  start: string;
  end: string;
}

export interface ResolvedGeometry {
  geometry_hash: string;
  bbox: { west: number; south: number; east: number; north: number };
  area_km2: number;
  analysis_crs: string;
  grid_resolution_m: number;
  aoi_id: string | null;
}

function isRecord(node: unknown): node is Record<string, unknown> {
  return typeof node === "object" && node !== null && !Array.isArray(node);
}

const INDICATOR_COLUMNS = `
  value_id, aoi_id, geometry_hash, indicator, family, unit, period_start, period_end,
  value, mean, median, std, p10, p90, minimum, maximum, valid_pixels, total_pixels,
  validation_status, confidence, confidence_basis_json, flags_json, constraints_checked,
  method_json, provenance_json, observation_ids, algorithm_version, pipeline_version,
  computed_at`;

/**
 * Find the ingested area a request refers to.
 *
 * A geometry with no ingested coverage is an error, not an approximation. Serving an
 * enclosing area's average as though it described the requested parcel would be precisely
 * the undefendable number this layer exists to refuse — the caller would have no way to
 * tell it apart from a measurement.
 */
export async function resolveGeometry(geometry: GeometryInput): Promise<ResolvedGeometry> {
  const hash = geometryHash(geometry);

  const row = await queryOne(
    `SELECT aoi_id, west, south, east, north, area_km2, analysis_crs, grid_resolution_m
     FROM lake.aoi WHERE geometry_hash = $1`,
    [hash],
  );

  if (row === undefined) {
    const available = await query("SELECT aoi_id FROM lake.aoi ORDER BY aoi_id");
    throw new AoiNotIngestedError(
      hash,
      available.map((r) => str(r["aoi_id"])),
    );
  }

  return {
    geometry_hash: hash,
    bbox: {
      west: num(row["west"]),
      south: num(row["south"]),
      east: num(row["east"]),
      north: num(row["north"]),
    },
    area_km2: num(row["area_km2"]),
    analysis_crs: str(row["analysis_crs"]),
    grid_resolution_m: num(row["grid_resolution_m"]),
    aoi_id: str(row["aoi_id"]),
  };
}

async function rowsInRange(
  geometryHashValue: string,
  range: DateRange,
  indicators?: string[],
): Promise<IndicatorRow[]> {
  const filter =
    indicators !== undefined && indicators.length > 0
      ? ` AND indicator IN (${indicators.map((_, i) => `$${i + 4}`).join(", ")})`
      : "";

  const rows = await query(
    `SELECT ${INDICATOR_COLUMNS} FROM lake.indicator_value
     WHERE geometry_hash = $1 AND period_start >= $2 AND period_end <= $3${filter}
     ORDER BY indicator, period_start`,
    [geometryHashValue, range.start, range.end, ...(indicators ?? [])],
  );
  return rows.map(decodeIndicatorRow);
}

function groupByIndicator(rows: IndicatorRow[]): Map<string, IndicatorRow[]> {
  const out = new Map<string, IndicatorRow[]>();
  for (const row of rows) {
    const bucket = out.get(row.indicator);
    if (bucket === undefined) out.set(row.indicator, [row]);
    else bucket.push(row);
  }
  return out;
}

/**
 * Aggregate a series of monthly values into one figure for the requested period.
 *
 * Weighted by valid pixel count, so a month observed across a quarter of the area does not
 * carry the same weight as one observed across all of it.
 */
function aggregate(rows: IndicatorRow[]): IndicatorRow | null {
  const usable = rows.filter((r) => r.value !== null && r.status !== "rejected");
  if (usable.length === 0) return null;
  if (usable.length === 1) return usable[0] ?? null;

  const weights = usable.map((r) => Math.max(1, r.stats?.valid_pixels ?? 1));
  const totalWeight = weights.reduce((a, b) => a + b, 0);
  const weighted = usable.reduce(
    (acc, r, i) => acc + (r.value as number) * (weights[i] ?? 1),
    0,
  );

  const first = usable[0] as IndicatorRow;
  const last = usable[usable.length - 1] as IndicatorRow;
  const allFlags = usable.flatMap((r) => r.flags);

  const stats = first.stats;
  const merged: IndicatorRow = {
    ...last,
    periodStart: first.periodStart,
    periodEnd: last.periodEnd,
    value: weighted / totalWeight,
    // Confidence of a period aggregate is the mean of its months, not the best of them.
    confidence: usable.reduce((a, r) => a + r.confidence, 0) / usable.length,
    flags: allFlags,
    status: allFlags.length > 0 ? "flagged" : "validated",
    stats:
      stats === null
        ? null
        : {
            ...stats,
            mean: weighted / totalWeight,
            valid_pixels: Math.round(totalWeight / usable.length),
            minimum: Math.min(...usable.map((r) => r.stats?.minimum ?? Number.POSITIVE_INFINITY)),
            maximum: Math.max(...usable.map((r) => r.stats?.maximum ?? Number.NEGATIVE_INFINITY)),
            median: weighted / totalWeight,
          },
    provenance: [
      ...usable.flatMap((r) => r.provenance.filter((s) => s.kind === "observation")),
      ...last.provenance.filter((s) => s.kind !== "observation"),
    ].map((step, index) => ({ ...step, index })),
    observationIds: [...new Set(usable.flatMap((r) => r.observationIds))],
  };
  return merged;
}

// ------------------------------------------------------------------- 1. ecological state

export interface EcologicalStateResponse {
  aoi: ResolvedGeometry;
  period: DateRange;
  indicators: NumericEnvelope[];
  trends: TrendEnvelope[];
  rejected: RejectedValue[];
  summary: string;
  generated_at: string;
}

export async function getEcologicalState(
  geometry: GeometryInput,
  dateRange: DateRange,
  indicators?: string[],
): Promise<EcologicalStateResponse> {
  const aoi = await resolveGeometry(geometry);
  const rows = await rowsInRange(aoi.geometry_hash, dateRange, indicators);

  if (rows.length === 0) {
    const span = await queryOne(
      "SELECT min(period_start) AS s, max(period_end) AS e FROM lake.indicator_value WHERE geometry_hash = $1",
      [aoi.geometry_hash],
    );
    const available =
      span?.["s"] == null ? undefined : `${isoDate(span["s"])} to ${isoDate(span["e"])}`;
    throw new NoDataForPeriodError(`${dateRange.start} to ${dateRange.end}`, available);
  }

  const grouped = groupByIndicator(rows);
  const envelopes: NumericEnvelope[] = [];
  const trendEnvelopes: TrendEnvelope[] = [];
  const rejected: RejectedValue[] = [];
  const claims: ClaimRecord[] = [];

  for (const [indicator, series] of grouped) {
    for (const row of series.filter((r) => r.status === "rejected" || r.value === null)) {
      rejected.push(toRejectedValue(row));
    }

    const merged = aggregate(series);
    if (merged !== null) {
      const envelope = toNumericEnvelope(merged);
      envelopes.push(envelope);
      claims.push(claimRecordFor(envelope, merged));
    }

    const trend = computeTrend(indicator, series);
    if (trend !== null) {
      const envelope = toTrendEnvelope(trend, series, dateRange);
      trendEnvelopes.push(envelope);
      claims.push({
        claim_id: envelope.claim_id,
        claim_kind: "trend",
        indicator,
        aoi_id: aoi.aoi_id,
        geometry_hash: aoi.geometry_hash,
        period_start: dateRange.start,
        period_end: dateRange.end,
        value_repr: `${trend.slope_per_month.toPrecision(4)} ${envelope.unit} (${trend.direction})`,
        unit: envelope.unit,
        confidence: envelope.confidence,
        validation: { status: "validated", significant: trend.significant, p_value: trend.p_value },
        method: envelope.method,
        provenance: envelope.provenance,
        source_ids: [...new Set(series.flatMap((r) => r.observationIds))],
        payload: envelope,
      });
    }
  }

  const response: EcologicalStateResponse = {
    aoi,
    period: dateRange,
    indicators: envelopes.sort((a, b) => a.indicator.localeCompare(b.indicator)),
    trends: trendEnvelopes.sort((a, b) => a.indicator.localeCompare(b.indicator)),
    rejected,
    summary: summariseState(
      envelopes,
      trendEnvelopes.map((t) => t.value),
      rejected,
      dateRange,
    ),
    generated_at: new Date().toISOString(),
  };

  guard(response, "get_ecological_state");
  await recordClaims(claims);
  return response;
}

// ------------------------------------------------------------------ 2. substrate score

export async function getWildfireSubstrateScore(
  geometry: GeometryInput,
  onDate: string,
): Promise<unknown> {
  const aoi = await resolveGeometry(geometry);

  const row = await queryOne(
    `SELECT * FROM lake.substrate_score
     WHERE geometry_hash = $1 AND period_start <= $2 AND period_end >= $2
     ORDER BY computed_at DESC LIMIT 1`,
    [aoi.geometry_hash, onDate],
  );

  if (row === undefined) {
    const span = await queryOne(
      "SELECT min(period_start) AS s, max(period_end) AS e FROM lake.substrate_score WHERE geometry_hash = $1",
      [aoi.geometry_hash],
    );
    throw new NoDataForPeriodError(
      onDate,
      span?.["s"] == null ? undefined : `${isoDate(span["s"])} to ${isoDate(span["e"])}`,
    );
  }

  const period = { start: isoDate(row["period_start"]), end: isoDate(row["period_end"]) };
  const score = num(row["score"]);
  const algorithmVersion = str(row["algorithm_version"]);

  // The pipeline stores each component's underlying envelope, but a claim id is minted at
  // serve time from the value's own content, so it is filled in here. Without it a reader
  // could trace the composite but not the measurement that drove it.
  const components = json<Record<string, unknown>[]>(row["components_json"], []).map((component) => {
    const raw = component["raw"];
    if (!isRecord(raw)) return component;
    const rawPeriod = isRecord(raw["period"]) ? raw["period"] : {};
    return {
      ...component,
      raw: {
        ...raw,
        claim_id: claimIdFor(
          "numeric",
          str(raw["geometry_hash"]),
          str(raw["indicator"]),
          str(rawPeriod["start"]),
          str(rawPeriod["end"]),
          algorithmVersion,
          formatValue(num(raw["value"])),
        ),
      },
    };
  });
  const { band, interpretation } = interpretSubstrateBand(score);

  const claimId = claimIdFor(
    "substrate_score",
    aoi.geometry_hash,
    period.start,
    period.end,
    str(row["weighting_scheme"]),
    str(row["algorithm_version"]),
    formatValue(score),
  );

  const envelope = {
    kind: "substrate_score" as const,
    claim_id: claimId,
    value: {
      score,
      band,
      components,
      weighting_scheme: str(row["weighting_scheme"]),
      interpretation: str(row["interpretation"]) || interpretation,
      caveats: json<string[]>(row["caveats_json"], []),
    },
    unit: "score_0_100",
    confidence: num(row["confidence"]),
    confidence_basis: json(row["confidence_basis_json"], {}),
    validation_status: str(row["validation_status"]) as "validated" | "flagged",
    flags: json<unknown[]>(row["flags_json"], []),
    provenance: json<unknown[]>(row["provenance_json"], []),
    method: json(row["method_json"], {}),
    geometry_hash: aoi.geometry_hash,
    period,
    generated_at: new Date().toISOString(),
  };

  const response = {
    aoi,
    period,
    score: envelope,
    missing_indicators: json<string[]>(row["missing_indicators"], []),
    generated_at: new Date().toISOString(),
  };

  guard(response, "get_wildfire_substrate_score");
  await recordClaims([
    {
      claim_id: claimId,
      claim_kind: "substrate_score",
      indicator: null,
      aoi_id: aoi.aoi_id,
      geometry_hash: aoi.geometry_hash,
      period_start: period.start,
      period_end: period.end,
      value_repr: `${score.toFixed(1)} / 100 (${band})`,
      unit: "score_0_100",
      confidence: envelope.confidence,
      validation: { status: envelope.validation_status, flags: envelope.flags },
      method: envelope.method,
      provenance: envelope.provenance,
      source_ids: [],
      payload: envelope,
    },
  ]);
  return response;
}

// ---------------------------------------------------------------------- 3. provenance

/**
 * Rebuild a claim from the lake when the ledger has no record of it.
 *
 * Claim ids are derived from claim content, so a served value can always be re-identified
 * by recomputing ids over the stored measurements and looking for a match. The lake holds
 * a few hundred rows, so the scan is cheap.
 *
 * This is what lets `get_provenance` work with no writable storage at all — on a read-only
 * deployment, or after a ledger is lost. The ledger becomes an index rather than the system
 * of record, which is the right relationship: the measurements are the system of record.
 *
 * Trends are not reconstructible this way, because a trend claim is keyed by the arbitrary
 * date range the caller asked for. Those resolve only from the ledger.
 */
async function reconstructClaim(claimId: string): Promise<unknown | undefined> {
  const rows = await query(
    `SELECT ${INDICATOR_COLUMNS} FROM lake.indicator_value WHERE value IS NOT NULL`,
  );

  for (const raw of rows) {
    const decoded = decodeIndicatorRow(raw);
    if (claimForNumeric(decoded) !== claimId) continue;

    const envelope = toNumericEnvelope(decoded);
    const sources = await sourcesFor(decoded.observationIds, envelope.provenance);
    return {
      claim_id: claimId,
      claim_kind: "numeric",
      indicator: decoded.indicator,
      value_repr: `${formatValue(envelope.value)} ${envelope.unit}`,
      unit: envelope.unit,
      confidence: envelope.confidence,
      validation: {
        status: decoded.status,
        flags: decoded.flags,
        constraints_checked: decoded.constraintsChecked,
        confidence: decoded.confidence,
        confidence_basis: decoded.confidenceBasis,
      },
      method: envelope.method,
      provenance: envelope.provenance,
      sources,
      served_at: decoded.computedAt,
      reconstructed_from_lake: true,
      generated_at: new Date().toISOString(),
    };
  }
  return undefined;
}

/** Resolve source observations, falling back to the chain's own observation steps. */
async function sourcesFor(
  observationIds: string[],
  provenance: { kind: string; source?: string | null; dataset_id?: string | null; asset_ids: string[]; acquired_at?: string | null; access_route?: string | null; spatial_ref: string }[],
): Promise<unknown[]> {
  if (observationIds.length > 0) {
    const rows = await query(
      `SELECT source, dataset_id, asset_id, acquired_at, access_route, url, spatial_ref
       FROM lake.observation WHERE observation_id IN (${observationIds.map((_, i) => `$${i + 1}`).join(", ")})
       ORDER BY acquired_at`,
      observationIds,
    );
    if (rows.length > 0) {
      return rows.map((s) => ({
        source: str(s["source"]),
        dataset_id: str(s["dataset_id"]),
        asset_id: str(s["asset_id"]),
        acquired_at: s["acquired_at"] === null ? null : isoTimestamp(s["acquired_at"]),
        access_route: s["access_route"] === null ? null : str(s["access_route"]),
        url: s["url"] === null ? null : str(s["url"]),
        spatial_ref: str(s["spatial_ref"]),
      }));
    }
  }
  return provenance
    .filter((s) => s.kind === "observation")
    .map((s) => ({
      source: s.source ?? "unknown",
      dataset_id: s.dataset_id ?? "unknown",
      asset_id: s.asset_ids[0] ?? "unknown",
      acquired_at: s.acquired_at ?? null,
      access_route: s.access_route ?? null,
      url: s.asset_ids[0] ?? null,
      spatial_ref: s.spatial_ref,
    }));
}

export async function getProvenance(claimId: string): Promise<unknown> {
  let row: Record<string, unknown> | undefined;
  try {
    row = (await queryClaims("SELECT * FROM claim WHERE claim_id = $1", [claimId]))[0];
  } catch {
    // The ledger is optional. On a read-only filesystem it cannot be opened at all, and
    // that must not make provenance unavailable.
    row = undefined;
  }

  if (row === undefined) {
    const reconstructed = await reconstructClaim(claimId);
    if (reconstructed !== undefined) return reconstructed;
    throw new ClaimNotFoundError(claimId);
  }

  const sourceIds = json<string[]>(row["source_ids"], []);
  const sources =
    sourceIds.length === 0
      ? []
      : await query(
          `SELECT source, dataset_id, asset_id, acquired_at, access_route, url, spatial_ref
           FROM lake.observation WHERE observation_id IN (${sourceIds.map((_, i) => `$${i + 1}`).join(", ")})
           ORDER BY acquired_at`,
          sourceIds,
        );

  const provenance = json<{ kind: string; source?: string; dataset_id?: string; asset_ids: string[]; acquired_at?: string; access_route?: string; spatial_ref: string }[]>(
    row["provenance_json"],
    [],
  );

  // If the observation table has no rows for this claim, fall back to the observation
  // steps in the chain itself. A provenance response with no sources would be an answer
  // that cannot be checked, which is worse than a partial one.
  const fallbackSources = provenance
    .filter((s) => s.kind === "observation")
    .map((s) => ({
      source: s.source ?? "unknown",
      dataset_id: s.dataset_id ?? "unknown",
      asset_id: s.asset_ids[0] ?? "unknown",
      acquired_at: s.acquired_at ?? null,
      access_route: s.access_route ?? null,
      url: s.asset_ids[0] ?? null,
      spatial_ref: s.spatial_ref,
    }));

  const response = {
    claim_id: claimId,
    claim_kind: str(row["claim_kind"]),
    indicator: row["indicator"] === null ? null : str(row["indicator"]),
    value_repr: str(row["value_repr"]),
    unit: str(row["unit"]),
    confidence: num(row["confidence"]),
    validation: json(row["validation_json"], {}),
    method: json(row["method_json"], {}),
    provenance,
    sources:
      sources.length > 0
        ? sources.map((s) => ({
            source: str(s["source"]),
            dataset_id: str(s["dataset_id"]),
            asset_id: str(s["asset_id"]),
            acquired_at: s["acquired_at"] === null ? null : isoTimestamp(s["acquired_at"]),
            access_route: s["access_route"] === null ? null : str(s["access_route"]),
            url: s["url"] === null ? null : str(s["url"]),
            spatial_ref: str(s["spatial_ref"]),
          }))
        : fallbackSources,
    served_at: isoTimestamp(row["served_at"]),
    generated_at: new Date().toISOString(),
  };

  return response;
}

// ------------------------------------------------------------------ 4. compare periods

export async function comparePeriods(
  geometry: GeometryInput,
  periodA: DateRange,
  periodB: DateRange,
  indicators?: string[],
): Promise<unknown> {
  const aoi = await resolveGeometry(geometry);
  const [rowsA, rowsB] = await Promise.all([
    rowsInRange(aoi.geometry_hash, periodA, indicators),
    rowsInRange(aoi.geometry_hash, periodB, indicators),
  ]);

  const groupedA = groupByIndicator(rowsA);
  const groupedB = groupByIndicator(rowsB);

  const comparisons: {
    indicator: string;
    period_a: NumericEnvelope;
    period_b: NumericEnvelope;
    delta: number;
    percent_change: number | null;
    significant: boolean;
    significance_method: string;
    p_value: number | null;
    interpretation: string;
  }[] = [];
  const claims: ClaimRecord[] = [];

  for (const [indicator, seriesA] of groupedA) {
    const seriesB = groupedB.get(indicator);
    if (seriesB === undefined) continue;

    const mergedA = aggregate(seriesA);
    const mergedB = aggregate(seriesB);
    if (mergedA === null || mergedB === null) continue;

    const envelopeA = toNumericEnvelope(mergedA);
    const envelopeB = toNumericEnvelope(mergedB);
    claims.push(claimRecordFor(envelopeA, mergedA), claimRecordFor(envelopeB, mergedB));

    const delta = envelopeB.value - envelopeA.value;

    // Significance is tested between the two periods' *monthly series*, not between their
    // pixels.
    //
    // Testing on pixels was wrong in both directions and both showed up immediately. A
    // spectral composite has 5.8 million pixels, so a 0.006 difference in NDVI came back
    // overwhelmingly significant — true given that n, and ecologically meaningless. A
    // climate value is a point estimate with no spatial spread at all, so its stored
    // standard deviation is zero, and soil moisture falling by two thirds across a season
    // came back as no change.
    //
    // The question being asked is whether the area's monthly mean differs between two
    // periods. The variance that belongs in that test is the month-to-month variance of
    // that mean, and n is the number of months.
    const monthlyA = seriesA.filter((r) => r.value !== null && r.status !== "rejected");
    const monthlyB = seriesB.filter((r) => r.value !== null && r.status !== "rejected");

    const spread = (rows: IndicatorRow[]): { mean: number; sd: number; n: number } => {
      const values = rows.map((r) => r.value as number);
      const n = values.length;
      const mean = values.reduce((a, b) => a + b, 0) / Math.max(1, n);
      const variance =
        n < 2 ? 0 : values.reduce((a, v) => a + (v - mean) ** 2, 0) / (n - 1);
      return { mean, sd: Math.sqrt(variance), n };
    };

    const statsA = spread(monthlyA);
    const statsB = spread(monthlyB);

    let significant = false;
    let pValue: number | null = null;
    let method: string;
    let interpretation: string;

    if (statsA.n < 2 || statsB.n < 2) {
      // One month against one month is a difference, not evidence of one. Saying so is
      // better than running a test that cannot fail to be inconclusive and reporting
      // "not significant" as though it meant no change.
      method = "not_tested_insufficient_months";
      interpretation =
        `${indicator} differs by ${delta.toFixed(4)} ${envelopeA.unit}, but one or both ` +
        `periods contain fewer than two monthly observations ` +
        `(${statsA.n} and ${statsB.n}), so no significance test was run.`;
    } else {
      const test = welchTTest(statsA.mean, statsA.sd, statsA.n, statsB.mean, statsB.sd, statsB.n);
      significant = test.significant;
      pValue = test.pValue;
      method = "welch_t_test_alpha_0.05_on_monthly_means";
      interpretation = test.significant
        ? `${indicator} changed by ${delta.toFixed(4)} ${envelopeA.unit} between the two ` +
          `periods, significant at p ${test.pValue.toFixed(4)} across ${statsA.n} and ` +
          `${statsB.n} monthly observations.`
        : `${indicator} differs by ${delta.toFixed(4)} ${envelopeA.unit}, which is not ` +
          `distinguishable from month-to-month variation within each period ` +
          `(p ${test.pValue.toFixed(3)}, ${statsA.n} and ${statsB.n} months).`;
    }

    const percentChange =
      Math.abs(envelopeA.value) < 1e-6 ? null : (delta / Math.abs(envelopeA.value)) * 100;

    comparisons.push({
      indicator,
      period_a: envelopeA,
      period_b: envelopeB,
      delta,
      percent_change: percentChange,
      significant,
      significance_method: method,
      p_value: pValue,
      interpretation,
    });
  }

  const response = {
    aoi,
    period_a: periodA,
    period_b: periodB,
    comparisons: comparisons.sort((a, b) => a.indicator.localeCompare(b.indicator)),
    summary: summariseComparison(comparisons, periodA, periodB),
    generated_at: new Date().toISOString(),
  };

  guard(response, "compare_periods");
  await recordClaims(claims);
  return response;
}

// -------------------------------------------------------------------- 5. list coverage

export async function listCoverage(aoiId?: string): Promise<unknown> {
  const areas = await query(
    aoiId === undefined
      ? "SELECT * FROM lake.aoi ORDER BY aoi_id"
      : "SELECT * FROM lake.aoi WHERE aoi_id = $1",
    aoiId === undefined ? [] : [aoiId],
  );

  const aois = [];
  for (const area of areas) {
    const id = str(area["aoi_id"]);
    const indicators = await query(
      `SELECT indicator, family, unit,
              min(period_start) AS first_start,
              max(period_end) AS last_end,
              count(*) AS periods,
              avg(confidence) AS mean_confidence,
              sum(CASE WHEN validation_status = 'validated' THEN 1 ELSE 0 END) AS validated,
              sum(CASE WHEN validation_status = 'flagged' THEN 1 ELSE 0 END) AS flagged,
              sum(CASE WHEN validation_status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
              any_value(source) AS source
       FROM lake.indicator_value WHERE aoi_id = $1
       GROUP BY indicator, family, unit ORDER BY indicator`,
      [id],
    );

    const lastRun = await queryOne(
      "SELECT max(finished_at) AS last FROM lake.run_manifest WHERE aoi_id = $1 AND status = 'ok'",
      [id],
    );

    aois.push({
      aoi_id: id,
      name: str(area["name"]),
      bbox: {
        west: num(area["west"]),
        south: num(area["south"]),
        east: num(area["east"]),
        north: num(area["north"]),
      },
      area_km2: num(area["area_km2"]),
      analysis_crs: str(area["analysis_crs"]),
      grid_resolution_m: num(area["grid_resolution_m"]),
      indicators: indicators.map((r) => ({
        indicator: str(r["indicator"]),
        family: str(r["family"]),
        unit: str(r["unit"]),
        first_period_start: isoDate(r["first_start"]),
        last_period_end: isoDate(r["last_end"]),
        period_count: num(r["periods"]),
        mean_confidence: num(r["mean_confidence"]),
        validated_count: num(r["validated"]),
        flagged_count: num(r["flagged"]),
        rejected_count: num(r["rejected"]),
        source: str(r["source"]),
      })),
      last_ingested_at: lastRun?.["last"] == null ? null : isoTimestamp(lastRun["last"]),
    });
  }

  const versions = await queryOne(
    "SELECT any_value(pipeline_version) AS p, any_value(algorithm_version) AS a FROM lake.indicator_value",
  );

  return {
    aois,
    pipeline_version: str(versions?.["p"] ?? "0.1.0"),
    algorithm_version: str(versions?.["a"] ?? "unknown"),
    generated_at: new Date().toISOString(),
  };
}

// ---------------------------------------------------------------------- map cells

/**
 * Cell grid for one indicator and period, as GeoJSON, plus the envelope it came from.
 *
 * The per-cell `reading` is a display aggregate, not an independent claim — which is why
 * the parent envelope travels with it. Clicking a cell in the console shows the cell's own
 * number and the full provenance of the area value it was aggregated from, so a pixel a
 * user pointed at is as citable as the area average.
 */
export async function getCells(
  aoiId: string,
  indicator: string,
  periodStart: string,
): Promise<unknown> {
  const rows = await query(
    `SELECT cell_id, value_id, west, south, east, north, value, valid_fraction, confidence,
            cell_size_m, period_start, period_end
     FROM lake.indicator_cell
     WHERE aoi_id = $1 AND indicator = $2 AND period_start = $3`,
    [aoiId, indicator, periodStart],
  );

  if (rows.length === 0) {
    throw new NoDataForPeriodError(`${indicator} at ${periodStart}`);
  }

  const parentRow = await queryOne(
    `SELECT ${INDICATOR_COLUMNS} FROM lake.indicator_value WHERE value_id = $1`,
    [str(rows[0]?.["value_id"])],
  );
  if (parentRow === undefined) {
    throw new ServiceError(
      "internal",
      "Map cells exist with no parent value, so their provenance cannot be established.",
    );
  }
  const parent = toNumericEnvelope(decodeIndicatorRow(parentRow));

  const response = {
    type: "FeatureCollection",
    parent,
    features: rows.map((r) => {
      const west = num(r["west"]);
      const south = num(r["south"]);
      const east = num(r["east"]);
      const north = num(r["north"]);
      return {
        type: "Feature",
        id: str(r["cell_id"]),
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [west, south],
              [east, south],
              [east, north],
              [west, north],
              [west, south],
            ],
          ],
        },
        properties: {
          cell_id: str(r["cell_id"]),
          value_id: str(r["value_id"]),
          parent_claim_id: parent.claim_id,
          indicator,
          reading: numOrNull(r["value"]),
          valid_fraction: num(r["valid_fraction"]),
          confidence: num(r["confidence"]),
          cell_size_m: num(r["cell_size_m"]),
          period_start: isoDate(r["period_start"]),
          period_end: isoDate(r["period_end"]),
        },
      };
    }),
  };

  guard(response, "get_cells");
  await recordClaims([claimRecordFor(parent, decodeIndicatorRow(parentRow))]);
  return response;
}

/** Available periods for an area, so the console can build its timeline. */
export async function listPeriods(aoiId: string): Promise<unknown> {
  const rows = await query(
    `SELECT DISTINCT period_start, period_end FROM lake.indicator_value
     WHERE aoi_id = $1 ORDER BY period_start`,
    [aoiId],
  );
  return rows.map((r) => ({
    start: isoDate(r["period_start"]),
    end: isoDate(r["period_end"]),
  }));
}

// --------------------------------------------------------------------------- guard

/**
 * Last line of defence before a response leaves the service layer.
 *
 * Even if something upstream produced a bare number, it does not get served. Disabled only
 * by setting GAIA_STRICT_GUARD=0, which the runbook says not to do in a deployment.
 */
function guard(payload: unknown, context: string): void {
  if (process.env["GAIA_STRICT_GUARD"] === "0") return;
  try {
    assertProvenanced(payload, context);
  } catch (error) {
    throw new ServiceError(
      "internal",
      "A response failed the provenance guard and was withheld.",
      error instanceof Error ? error.message : String(error),
    );
  }
}
