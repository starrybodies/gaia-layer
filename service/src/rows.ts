/**
 * Turning lake rows into envelopes.
 *
 * The single place where a stored measurement becomes something the layer will hand out.
 * Every path to a served number goes through `toNumericEnvelope` or one of its siblings,
 * which is what makes "no value without provenance" enforceable rather than aspirational —
 * there is no other constructor.
 */

import type { Row } from "./db.js";
import { isoDate, isoTimestamp, json, num, numOrNull, str } from "./db.js";
import { claimIdFor, type ClaimRecord } from "./claims.js";

export interface ProvenanceStep {
  index: number;
  kind: "observation" | "processing" | "validation";
  description: string;
  source?: string | null;
  dataset_id?: string | null;
  access_route?: string | null;
  asset_ids: string[];
  acquired_at?: string | null;
  processed_at: string;
  pipeline_version: string;
  algorithm_version: string;
  software?: string | null;
  spatial_ref: string;
  resolution_m?: number | null;
  parameters: Record<string, unknown>;
}

export interface Method {
  name: string;
  citation: string;
  formula?: string | null;
  doi?: string | null;
  url?: string | null;
  notes?: string | null;
}

export interface ValidationFlag {
  code: string;
  constraint: string;
  severity: "warn" | "error";
  message: string;
  observed?: number | null;
  expected?: string | null;
  confidence_penalty: number;
}

export interface ConfidenceBasis {
  observation_count: number;
  cloud_fraction?: number | null;
  revisit_gap_days?: number | null;
  spatial_coverage: number;
  components: { name: string; value: number; weight: number; description: string }[];
  aggregation: string;
}

export interface SpatialStats {
  mean: number;
  median: number;
  std: number;
  p10: number;
  p90: number;
  minimum: number;
  maximum: number;
  valid_pixels: number;
  total_pixels: number;
}

export interface NumericEnvelope {
  kind: "numeric";
  claim_id: string;
  indicator: string;
  value: number;
  unit: string;
  confidence: number;
  confidence_basis: ConfidenceBasis;
  validation_status: "validated" | "flagged";
  flags: ValidationFlag[];
  provenance: ProvenanceStep[];
  method: Method;
  geometry_hash: string;
  period: { start: string; end: string };
  spatial_stats: SpatialStats | null;
  generated_at: string;
}

export interface RejectedValue {
  claim_id: string;
  indicator: string | null;
  validation_status: "rejected";
  reason: string;
  flags: ValidationFlag[];
  provenance: ProvenanceStep[];
  geometry_hash: string;
  period: { start: string; end: string };
  generated_at: string;
}

/** A stored row, decoded but not yet shaped into a response. */
export interface IndicatorRow {
  valueId: string;
  aoiId: string;
  geometryHash: string;
  indicator: string;
  family: string;
  unit: string;
  periodStart: string;
  periodEnd: string;
  value: number | null;
  stats: SpatialStats | null;
  status: "validated" | "flagged" | "rejected";
  confidence: number;
  confidenceBasis: ConfidenceBasis;
  flags: ValidationFlag[];
  constraintsChecked: string[];
  method: Method;
  provenance: ProvenanceStep[];
  observationIds: string[];
  algorithmVersion: string;
  pipelineVersion: string;
  computedAt: string;
}

export function decodeIndicatorRow(row: Row): IndicatorRow {
  const validPixels = numOrNull(row["valid_pixels"]);
  const totalPixels = numOrNull(row["total_pixels"]);

  const stats: SpatialStats | null =
    validPixels !== null && totalPixels !== null && totalPixels > 0
      ? {
          mean: num(row["mean"]),
          median: num(row["median"]),
          std: num(row["std"]),
          p10: num(row["p10"]),
          p90: num(row["p90"]),
          minimum: num(row["minimum"]),
          maximum: num(row["maximum"]),
          valid_pixels: validPixels,
          total_pixels: totalPixels,
        }
      : null;

  return {
    valueId: str(row["value_id"]),
    aoiId: str(row["aoi_id"]),
    geometryHash: str(row["geometry_hash"]),
    indicator: str(row["indicator"]),
    family: str(row["family"]),
    unit: str(row["unit"]),
    periodStart: isoDate(row["period_start"]),
    periodEnd: isoDate(row["period_end"]),
    value: numOrNull(row["value"]),
    stats,
    status: str(row["validation_status"]) as IndicatorRow["status"],
    confidence: num(row["confidence"]),
    confidenceBasis: json<ConfidenceBasis>(row["confidence_basis_json"], {
      observation_count: 0,
      spatial_coverage: 0,
      components: [],
      aggregation: "unknown",
    }),
    flags: json<ValidationFlag[]>(row["flags_json"], []),
    constraintsChecked: json<string[]>(row["constraints_checked"], []),
    method: json<Method>(row["method_json"], { name: "unknown", citation: "unknown" }),
    provenance: json<ProvenanceStep[]>(row["provenance_json"], []),
    observationIds: json<string[]>(row["observation_ids"], []),
    algorithmVersion: str(row["algorithm_version"]),
    pipelineVersion: str(row["pipeline_version"]),
    computedAt: isoTimestamp(row["computed_at"]),
  };
}

/** Six significant figures. Enough to reproduce, few enough not to imply false precision. */
export function formatValue(value: number): string {
  return value.toPrecision(6);
}

export function claimForNumeric(row: IndicatorRow): string {
  return claimIdFor(
    "numeric",
    row.geometryHash,
    row.indicator,
    row.periodStart,
    row.periodEnd,
    row.algorithmVersion,
    formatValue(row.value ?? Number.NaN),
  );
}

/**
 * Shape a validated or flagged row into a served envelope.
 *
 * Throws on a rejected row. That is deliberate: the caller must route rejections to
 * `toRejectedValue` and cannot accidentally serve one as an answer.
 */
export function toNumericEnvelope(row: IndicatorRow): NumericEnvelope {
  if (row.status === "rejected" || row.value === null) {
    throw new Error(
      `refusing to build an envelope for a rejected value (${row.indicator}, ${row.periodStart})`,
    );
  }
  return {
    kind: "numeric",
    claim_id: claimForNumeric(row),
    indicator: row.indicator,
    value: row.value,
    unit: row.unit,
    confidence: row.confidence,
    confidence_basis: row.confidenceBasis,
    validation_status: row.status,
    flags: row.flags,
    provenance: row.provenance,
    method: row.method,
    geometry_hash: row.geometryHash,
    period: { start: row.periodStart, end: row.periodEnd },
    spatial_stats: row.stats,
    generated_at: new Date().toISOString(),
  };
}

export function toRejectedValue(row: IndicatorRow): RejectedValue {
  const reason =
    row.flags.find((f) => f.severity === "error")?.message ??
    "The constraint engine rejected this value.";
  return {
    claim_id: claimIdFor(
      "rejected",
      row.geometryHash,
      row.indicator,
      row.periodStart,
      row.periodEnd,
      row.algorithmVersion,
    ),
    indicator: row.indicator,
    validation_status: "rejected",
    reason,
    flags: row.flags,
    provenance: row.provenance,
    geometry_hash: row.geometryHash,
    period: { start: row.periodStart, end: row.periodEnd },
    generated_at: new Date().toISOString(),
  };
}

export function claimRecordFor(
  envelope: NumericEnvelope,
  row: IndicatorRow,
): ClaimRecord {
  return {
    claim_id: envelope.claim_id,
    claim_kind: "numeric",
    indicator: row.indicator,
    aoi_id: row.aoiId,
    geometry_hash: row.geometryHash,
    period_start: row.periodStart,
    period_end: row.periodEnd,
    value_repr: `${formatValue(envelope.value)} ${envelope.unit}`,
    unit: envelope.unit,
    confidence: envelope.confidence,
    validation: {
      status: row.status,
      flags: row.flags,
      constraints_checked: row.constraintsChecked,
      confidence: row.confidence,
      confidence_basis: row.confidenceBasis,
    },
    method: envelope.method,
    provenance: envelope.provenance,
    source_ids: row.observationIds,
    payload: envelope,
  };
}
