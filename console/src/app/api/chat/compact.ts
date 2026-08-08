/**
 * Compacting tool results for the model.
 *
 * A full `get_ecological_state` response runs to about 20,000 tokens — nine indicators,
 * each carrying a provenance chain of forty-five steps naming every contributing satellite
 * scene. That is the right payload for an auditor and the wrong one for a context window,
 * and on Groq's free tier it exceeds the per-minute token budget outright.
 *
 * So the model is given a summary: every number, its confidence, its validation status, its
 * claim id, its method, and a count of the provenance steps and sources behind it. What it
 * loses is the enumeration of individual scenes — which it can still fetch on demand with
 * `get_provenance` when a reader asks where a figure came from.
 *
 * This does not weaken the citation guarantee, for two reasons. The claim id survives, so
 * every figure the model states stays traceable. And the **transcript rendered to the
 * visitor keeps the raw, uncompacted response**, so the prose can still be checked against
 * what the layer actually said — which is the whole point of showing the transcript.
 */

interface Envelope {
  claim_id?: string;
  indicator?: string;
  value?: unknown;
  unit?: string;
  confidence?: number;
  validation_status?: string;
  flags?: { code: string; message: string }[];
  method?: { name?: string };
  provenance?: unknown[];
  period?: { start: string; end: string };
  spatial_stats?: { p10?: number; p90?: number; valid_pixels?: number; total_pixels?: number };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function compactEnvelope(envelope: Envelope): Record<string, unknown> {
  const stats = envelope.spatial_stats;
  return {
    indicator: envelope.indicator,
    value: envelope.value,
    unit: envelope.unit,
    confidence: envelope.confidence,
    validation_status: envelope.validation_status,
    claim_id: envelope.claim_id,
    method: envelope.method?.name,
    flags: (envelope.flags ?? []).map((f) => `${f.code}: ${f.message}`),
    provenance_steps: envelope.provenance?.length ?? 0,
    ...(stats === undefined
      ? {}
      : {
          spread_p10_p90: [stats.p10, stats.p90],
          observed_fraction:
            stats.valid_pixels !== undefined && stats.total_pixels
              ? Number((stats.valid_pixels / stats.total_pixels).toFixed(3))
              : undefined,
        }),
  };
}

const NOTE =
  "Provenance chains are summarised here as step counts. Call get_provenance with a " +
  "claim_id for the full chain and the source scenes. The reader is shown the complete " +
  "untruncated response alongside your answer.";

/** Reduce a tool result to what the model needs to answer and cite. */
export function compactForModel(tool: string, payload: unknown): unknown {
  if (!isRecord(payload)) return payload;
  if ("error" in payload) return payload;

  switch (tool) {
    case "get_ecological_state": {
      const indicators = (payload["indicators"] as Envelope[] | undefined) ?? [];
      const trends = (payload["trends"] as { indicator?: string; claim_id?: string; confidence?: number; value?: Record<string, unknown> }[] | undefined) ?? [];
      const rejected = (payload["rejected"] as { indicator?: string; reason?: string }[] | undefined) ?? [];
      return {
        area: payload["aoi"],
        period: payload["period"],
        summary: payload["summary"],
        indicators: indicators.map(compactEnvelope),
        trends: trends.map((t) => ({
          indicator: t.indicator,
          claim_id: t.claim_id,
          confidence: t.confidence,
          direction: t.value?.["direction"],
          slope_per_month: t.value?.["slope_per_month"],
          r_squared: t.value?.["r_squared"],
          p_value: t.value?.["p_value"],
          significant: t.value?.["significant"],
          n_observations: t.value?.["n_observations"],
        })),
        rejected: rejected.map((r) => ({ indicator: r.indicator, reason: r.reason })),
        note: NOTE,
      };
    }

    case "get_wildfire_substrate_score": {
      const wrapper = payload["score"];
      if (!isRecord(wrapper)) return payload;
      const score = wrapper["value"];
      if (!isRecord(score)) return payload;
      const components =
        (score["components"] as
          | { indicator?: string; normalized?: number; weight?: number; contribution?: number; raw?: Envelope; rationale?: string }[]
          | undefined) ?? [];
      return {
        area: payload["aoi"],
        period: payload["period"],
        score: score["score"],
        band: score["band"],
        interpretation: score["interpretation"],
        weighting_scheme: score["weighting_scheme"],
        confidence: wrapper["confidence"],
        validation_status: wrapper["validation_status"],
        claim_id: wrapper["claim_id"],
        components: components.map((c) => ({
          indicator: c.indicator,
          measured: c.raw?.value,
          unit: c.raw?.unit,
          measured_confidence: c.raw?.confidence,
          measured_claim_id: c.raw?.claim_id,
          normalized: c.normalized,
          weight: c.weight,
          points: c.contribution,
          rationale: c.rationale,
        })),
        missing_indicators: payload["missing_indicators"],
        caveats: score["caveats"],
        note: NOTE,
      };
    }

    case "compare_periods": {
      const comparisons =
        (payload["comparisons"] as
          | {
              indicator?: string;
              period_a?: Envelope;
              period_b?: Envelope;
              delta?: number;
              percent_change?: number | null;
              significant?: boolean;
              p_value?: number | null;
              significance_method?: string;
              interpretation?: string;
            }[]
          | undefined) ?? [];
      return {
        area: payload["aoi"],
        period_a: payload["period_a"],
        period_b: payload["period_b"],
        summary: payload["summary"],
        comparisons: comparisons.map((c) => ({
          indicator: c.indicator,
          period_a_value: c.period_a?.value,
          period_a_claim_id: c.period_a?.claim_id,
          period_a_confidence: c.period_a?.confidence,
          period_b_value: c.period_b?.value,
          period_b_claim_id: c.period_b?.claim_id,
          period_b_confidence: c.period_b?.confidence,
          unit: c.period_a?.unit,
          delta: c.delta,
          percent_change: c.percent_change,
          significant: c.significant,
          p_value: c.p_value,
          significance_method: c.significance_method,
          interpretation: c.interpretation,
        })),
        note: NOTE,
      };
    }

    case "get_provenance": {
      const steps =
        (payload["provenance"] as { kind?: string; description?: string }[] | undefined) ?? [];
      const sources =
        (payload["sources"] as
          | { source?: string; dataset_id?: string; asset_id?: string; acquired_at?: string | null }[]
          | undefined) ?? [];
      return {
        claim_id: payload["claim_id"],
        claim_kind: payload["claim_kind"],
        indicator: payload["indicator"],
        value: payload["value_repr"],
        unit: payload["unit"],
        confidence: payload["confidence"],
        validation: payload["validation"],
        method: payload["method"],
        served_at: payload["served_at"],
        processing_and_validation_steps: steps
          .filter((s) => s.kind !== "observation")
          .map((s) => s.description),
        source_count: sources.length,
        // Enough named scenes to make the citation concrete without listing all of them.
        sources_sample: sources.slice(0, 8).map((s) => ({
          source: s.source,
          dataset: s.dataset_id,
          scene: s.asset_id,
          acquired: s.acquired_at?.slice(0, 10),
        })),
      };
    }

    case "list_coverage": {
      const aois =
        (payload["aois"] as
          | {
              aoi_id?: string;
              name?: string;
              bbox?: unknown;
              area_km2?: number;
              indicators?: {
                indicator?: string;
                unit?: string;
                first_period_start?: string;
                last_period_end?: string;
                period_count?: number;
                mean_confidence?: number;
                validated_count?: number;
                flagged_count?: number;
                rejected_count?: number;
              }[];
            }[]
          | undefined) ?? [];
      return {
        areas: aois.map((a) => ({
          aoi_id: a.aoi_id,
          name: a.name,
          geometry_to_pass_to_other_tools: a.bbox,
          area_km2: a.area_km2,
          indicators: (a.indicators ?? []).map((i) => ({
            indicator: i.indicator,
            unit: i.unit,
            from: i.first_period_start,
            to: i.last_period_end,
            periods: i.period_count,
            mean_confidence: i.mean_confidence,
            validated: i.validated_count,
            flagged: i.flagged_count,
            rejected: i.rejected_count,
          })),
        })),
        pipeline_version: payload["pipeline_version"],
        algorithm_version: payload["algorithm_version"],
      };
    }

    default:
      return payload;
  }
}
