/**
 * Client for the layer's REST API.
 *
 * The console is a consumer of the same interface an agent uses. It gets no privileged
 * path into the data, which is the point: what a visitor sees on screen is exactly what an
 * agent would receive, envelopes and all.
 */

/**
 * Where the console reaches the layer.
 *
 * Defaults to `/api` — same origin — because the deployed console mounts the REST API
 * itself. Point it at a host to talk to a standalone API instead.
 */
export const API_BASE = process.env["NEXT_PUBLIC_API_BASE"] ?? "/api";

/**
 * Absolute form of {@link API_BASE}, for server-side fetches.
 *
 * A relative URL is fine in a browser and meaningless in a server component, so this
 * resolves one against the deployment's own origin.
 */
export function absoluteApiBase(): string {
  if (API_BASE.startsWith("http")) return API_BASE;
  const explicit = process.env["NEXT_PUBLIC_SITE_URL"];
  if (explicit !== undefined && explicit !== "") return `${explicit}${API_BASE}`;
  const vercel = process.env["VERCEL_URL"];
  if (vercel !== undefined && vercel !== "") return `https://${vercel}${API_BASE}`;
  return `http://127.0.0.1:${process.env["CONSOLE_PORT"] ?? 3311}${API_BASE}`;
}

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

export interface ValidationFlag {
  code: string;
  constraint: string;
  severity: "warn" | "error";
  message: string;
  observed?: number | null;
  expected?: string | null;
  confidence_penalty: number;
}

export interface Method {
  name: string;
  citation: string;
  formula?: string | null;
  doi?: string | null;
  notes?: string | null;
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

export interface Trend {
  indicator: string;
  direction: "increasing" | "decreasing" | "stable";
  slope_per_month: number;
  r_squared: number;
  p_value: number;
  significant: boolean;
  n_observations: number;
}

export interface TrendEnvelope extends Omit<NumericEnvelope, "kind" | "value" | "spatial_stats"> {
  kind: "trend";
  value: Trend;
}

export interface RejectedValue {
  claim_id: string;
  indicator: string | null;
  validation_status: "rejected";
  reason: string;
  flags: ValidationFlag[];
  period: { start: string; end: string };
}

export interface ResolvedGeometry {
  geometry_hash: string;
  bbox: { west: number; south: number; east: number; north: number };
  area_km2: number;
  analysis_crs: string;
  grid_resolution_m: number;
  aoi_id: string | null;
}

export interface EcologicalState {
  aoi: ResolvedGeometry;
  period: { start: string; end: string };
  indicators: NumericEnvelope[];
  trends: TrendEnvelope[];
  rejected: RejectedValue[];
  summary: string;
  generated_at: string;
}

export interface SubstrateComponent {
  indicator: string;
  raw: NumericEnvelope;
  normalized: number;
  normalization: string;
  weight: number;
  contribution: number;
  rationale: string;
}

export interface SubstrateResponse {
  aoi: ResolvedGeometry;
  period: { start: string; end: string };
  score: {
    claim_id: string;
    value: {
      score: number;
      band: string;
      components: SubstrateComponent[];
      weighting_scheme: string;
      interpretation: string;
      caveats: string[];
    };
    confidence: number;
    validation_status: string;
    provenance: ProvenanceStep[];
    method: Method;
    period: { start: string; end: string };
  };
  missing_indicators: string[];
}

export interface Coverage {
  aois: {
    aoi_id: string;
    name: string;
    bbox: { west: number; south: number; east: number; north: number };
    area_km2: number;
    analysis_crs: string;
    grid_resolution_m: number;
    indicators: {
      indicator: string;
      family: string;
      unit: string;
      first_period_start: string;
      last_period_end: string;
      period_count: number;
      mean_confidence: number;
      validated_count: number;
      flagged_count: number;
      rejected_count: number;
      source: string;
    }[];
    last_ingested_at: string | null;
  }[];
  pipeline_version: string;
  algorithm_version: string;
}

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly detail?: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // Server components need an absolute URL; the browser is happy with either.
  const base = typeof window === "undefined" ? absoluteApiBase() : API_BASE;
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });

  const body: unknown = await response.json().catch(() => null);

  if (!response.ok) {
    const err = body as { error?: string; message?: string; detail?: string } | null;
    throw new ApiError(
      err?.error ?? "unknown",
      err?.message ?? `Request failed with ${response.status}.`,
      err?.detail,
    );
  }
  return body as T;
}

export const api = {
  coverage: () => request<Coverage>("/v1/coverage"),

  periods: (aoiId: string) => request<{ start: string; end: string }[]>(`/v1/periods/${aoiId}`),

  ecologicalState: (
    geometry: unknown,
    dateRange: { start: string; end: string },
    indicators?: string[],
  ) =>
    request<EcologicalState>("/v1/ecological-state", {
      method: "POST",
      body: JSON.stringify({ geometry, date_range: dateRange, indicators }),
    }),

  substrateScore: (geometry: unknown, date: string) =>
    request<SubstrateResponse>("/v1/wildfire-substrate-score", {
      method: "POST",
      body: JSON.stringify({ geometry, date }),
    }),

  provenance: (claimId: string) => request<unknown>(`/v1/provenance/${claimId}`),

  cells: (aoiId: string, indicator: string, periodStart: string) =>
    request<{
      type: "FeatureCollection";
      parent: NumericEnvelope;
      features: {
        type: "Feature";
        id: string;
        geometry: { type: "Polygon"; coordinates: number[][][] };
        properties: {
          cell_id: string;
          reading: number | null;
          valid_fraction: number;
          confidence: number;
          parent_claim_id: string;
          period_start: string;
        };
      }[];
    }>(`/v1/cells/${aoiId}/${indicator}/${periodStart}`),
};
