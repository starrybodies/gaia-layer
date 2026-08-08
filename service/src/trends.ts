/**
 * Trends over a series of monthly values.
 *
 * A slope on its own invites the reader to see change in noise, so nothing here is reported
 * without its significance, its fit and the number of observations behind it. Fewer than
 * four points produces a trend explicitly marked not significant rather than a confident
 * line through three dots.
 */

import { ols } from "./stats.js";
import { claimIdFor } from "./claims.js";
import type { IndicatorRow, ProvenanceStep } from "./rows.js";

export interface Trend {
  indicator: string;
  direction: "increasing" | "decreasing" | "stable";
  slope_per_month: number;
  r_squared: number;
  p_value: number;
  significant: boolean;
  n_observations: number;
  first: number;
  last: number;
}

export interface TrendEnvelope {
  kind: "trend";
  claim_id: string;
  indicator: string;
  value: Trend;
  unit: string;
  confidence: number;
  confidence_basis: IndicatorRow["confidenceBasis"];
  validation_status: "validated" | "flagged";
  flags: IndicatorRow["flags"];
  provenance: ProvenanceStep[];
  method: IndicatorRow["method"];
  geometry_hash: string;
  period: { start: string; end: string };
  generated_at: string;
}

const MIN_OBSERVATIONS = 4;

/** Months since epoch, so the slope is per month regardless of gaps in the series. */
function monthIndex(isoDay: string): number {
  const [year, month] = isoDay.split("-").map(Number);
  return (year ?? 0) * 12 + (month ?? 1) - 1;
}

/**
 * A slope smaller than this fraction of the series' own spread is reported as stable.
 *
 * Without it, a slope of 1e-9 with a tiny p-value would be labelled "increasing", which is
 * true and useless.
 */
const STABLE_FRACTION = 0.02;

export function computeTrend(indicator: string, rows: IndicatorRow[]): Trend | null {
  const usable = rows
    .filter((r) => r.value !== null && r.status !== "rejected")
    .sort((a, b) => a.periodStart.localeCompare(b.periodStart));

  if (usable.length < 2) return null;

  const first = usable[0];
  const last = usable[usable.length - 1];
  if (first === undefined || last === undefined) return null;

  const base = monthIndex(first.periodStart);
  const xs = usable.map((r) => monthIndex(r.periodStart) - base);
  const ys = usable.map((r) => r.value as number);

  const fit = ols(xs, ys);
  const span = Math.max(...ys) - Math.min(...ys);
  const significant = fit.pValue < 0.05 && usable.length >= MIN_OBSERVATIONS;

  const negligible = span === 0 || Math.abs(fit.slope) < STABLE_FRACTION * span;
  const direction: Trend["direction"] =
    !significant || negligible ? "stable" : fit.slope > 0 ? "increasing" : "decreasing";

  const lastX = xs[xs.length - 1] ?? 0;

  return {
    indicator,
    direction,
    slope_per_month: fit.slope,
    r_squared: fit.rSquared,
    p_value: fit.pValue,
    significant,
    n_observations: usable.length,
    first: fit.intercept,
    last: fit.intercept + fit.slope * lastX,
  };
}

/**
 * Wrap a trend in an envelope.
 *
 * The provenance chain is the union of the contributing months' chains plus one step
 * recording the regression, so tracing a trend reaches the same satellite scenes as
 * tracing any one of the values it was fitted through.
 */
export function toTrendEnvelope(
  trend: Trend,
  rows: IndicatorRow[],
  period: { start: string; end: string },
): TrendEnvelope {
  const usable = rows.filter((r) => r.value !== null && r.status !== "rejected");
  const first = usable[0];
  if (first === undefined) throw new Error("cannot envelope a trend with no contributing rows");

  const observationSteps: ProvenanceStep[] = [];
  const seen = new Set<string>();
  for (const row of usable) {
    for (const step of row.provenance) {
      if (step.kind !== "observation") continue;
      const key = step.asset_ids.join("|");
      if (seen.has(key)) continue;
      seen.add(key);
      observationSteps.push({ ...step, index: observationSteps.length });
    }
  }

  const chain: ProvenanceStep[] = [
    ...observationSteps,
    {
      index: observationSteps.length,
      kind: "processing",
      description:
        `Ordinary least squares fit of ${usable.length} monthly ${trend.indicator} composites ` +
        `against time, giving a slope per month and its significance.`,
      asset_ids: usable.map((r) => r.valueId),
      processed_at: new Date().toISOString(),
      pipeline_version: first.pipelineVersion,
      algorithm_version: first.algorithmVersion,
      software: "@gaia/service ols",
      spatial_ref: first.provenance[0]?.spatial_ref ?? "EPSG:4326",
      parameters: {
        method: "ordinary_least_squares",
        significance: "two-sided t test on the slope, alpha 0.05",
        minimum_observations_for_significance: MIN_OBSERVATIONS,
        stable_band_fraction_of_range: STABLE_FRACTION,
      },
    },
    {
      index: observationSteps.length + 1,
      kind: "validation",
      description:
        trend.significant
          ? `Slope is significant at p ${trend.p_value.toFixed(4)} across ${trend.n_observations} observations.`
          : `Slope did not reach significance (p ${trend.p_value.toFixed(4)}, ` +
            `${trend.n_observations} observations); reported as stable.`,
      asset_ids: [],
      processed_at: new Date().toISOString(),
      pipeline_version: first.pipelineVersion,
      algorithm_version: first.algorithmVersion,
      software: "@gaia/service",
      spatial_ref: first.provenance[0]?.spatial_ref ?? "EPSG:4326",
      parameters: { significant: trend.significant, p_value: trend.p_value },
    },
  ];

  // A trend is only as trustworthy as the weakest month under it.
  const confidence = Math.min(...usable.map((r) => r.confidence));

  return {
    kind: "trend",
    claim_id: claimIdFor(
      "trend",
      first.geometryHash,
      trend.indicator,
      period.start,
      period.end,
      first.algorithmVersion,
      trend.slope_per_month.toPrecision(6),
    ),
    indicator: trend.indicator,
    value: trend,
    unit: `${first.unit}/month`,
    confidence,
    confidence_basis: {
      ...first.confidenceBasis,
      observation_count: trend.n_observations,
      aggregation:
        "minimum confidence across the contributing monthly composites — a trend is no " +
        "stronger than its weakest month",
    },
    validation_status: "validated",
    flags: [],
    provenance: chain,
    method: {
      name: "Ordinary least squares trend",
      citation:
        "Draper, N.R. and Smith, H. (1998). Applied Regression Analysis, 3rd edition. " +
        "Wiley Series in Probability and Statistics.",
      formula: "value = intercept + slope x months",
      notes:
        "Significance is a two-sided t test on the slope at alpha 0.05, requiring at least " +
        `${MIN_OBSERVATIONS} monthly observations.`,
    },
    geometry_hash: first.geometryHash,
    period,
    generated_at: new Date().toISOString(),
  };
}
