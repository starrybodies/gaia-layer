/**
 * Plain-language summaries, rendered from the numbers by template.
 *
 * These are never model-generated. The whitepaper's second lesson says a language model is
 * not the system of record for a quantitative claim, and a summary that paraphrases the
 * numbers is a quantitative claim wearing a sentence. Every clause below is a direct
 * function of a validated value.
 *
 * The model in the console's playground reads these summaries and the envelopes beside
 * them. It does not write them.
 */

import { INDICATOR_LABELS } from "@gaia/core";
import type { NumericEnvelope, RejectedValue } from "./rows.js";
import type { Trend } from "./trends.js";

function label(indicator: string): string {
  return INDICATOR_LABELS[indicator] ?? indicator;
}

function confidenceWord(confidence: number): string {
  if (confidence >= 0.85) return "high confidence";
  if (confidence >= 0.6) return "moderate confidence";
  if (confidence >= 0.35) return "low confidence";
  return "very low confidence";
}

export function summariseState(
  indicators: NumericEnvelope[],
  trends: Trend[],
  rejected: RejectedValue[],
  period: { start: string; end: string },
): string {
  if (indicators.length === 0) {
    return `No validated indicators for ${period.start} to ${period.end}.`;
  }

  const sentences: string[] = [];

  sentences.push(
    `Over ${period.start} to ${period.end}, ${indicators.length} ` +
      `${indicators.length === 1 ? "indicator was" : "indicators were"} measured for this area.`,
  );

  const readings = indicators
    .map(
      (e) =>
        `${label(e.indicator)} ${e.value.toFixed(3)}${e.unit === "index" ? "" : ` ${e.unit}`}` +
        ` (${confidenceWord(e.confidence)}, ${e.confidence.toFixed(2)})`,
    )
    .join("; ");
  sentences.push(`Period means: ${readings}.`);

  const significant = trends.filter((t) => t.significant);
  if (significant.length > 0) {
    const described = significant
      .map(
        (t) =>
          `${label(t.indicator)} ${t.direction} at ${t.slope_per_month.toFixed(4)} per month ` +
          `(r squared ${t.r_squared.toFixed(2)}, p ${t.p_value.toFixed(3)}, ` +
          `${t.n_observations} observations)`,
      )
      .join("; ");
    sentences.push(`Statistically significant trends: ${described}.`);
  } else if (trends.length > 0) {
    sentences.push(
      `No trend reached significance at p below 0.05 over this period, across ` +
        `${trends.length} ${trends.length === 1 ? "indicator" : "indicators"} tested.`,
    );
  }

  const flagged = indicators.filter((e) => e.validation_status === "flagged");
  if (flagged.length > 0) {
    const codes = [...new Set(flagged.flatMap((e) => e.flags.map((f) => f.code)))].join(", ");
    sentences.push(
      `${flagged.length} ${flagged.length === 1 ? "value carries" : "values carry"} ` +
        `validation flags (${codes}); they are served with the flags attached.`,
    );
  }

  if (rejected.length > 0) {
    const names = [...new Set(rejected.map((r) => label(r.indicator ?? "unknown")))].join(", ");
    sentences.push(
      `${rejected.length} ${rejected.length === 1 ? "value was" : "values were"} rejected by ` +
        `the constraint engine and are not served as measurements: ${names}.`,
    );
  }

  return sentences.join(" ");
}

export function summariseComparison(
  comparisons: {
    indicator: string;
    delta: number;
    percent_change: number | null;
    significant: boolean;
    period_a: NumericEnvelope;
    period_b: NumericEnvelope;
  }[],
  periodA: { start: string; end: string },
  periodB: { start: string; end: string },
): string {
  if (comparisons.length === 0) {
    return `No indicator has validated values in both ${periodA.start}–${periodA.end} and ${periodB.start}–${periodB.end}.`;
  }

  const changed = comparisons.filter((c) => c.significant);
  const unchanged = comparisons.length - changed.length;

  const head =
    `Comparing ${periodB.start}–${periodB.end} against ${periodA.start}–${periodA.end} ` +
    `across ${comparisons.length} ${comparisons.length === 1 ? "indicator" : "indicators"}.`;

  if (changed.length === 0) {
    return (
      `${head} No change reached statistical significance, so the two periods are not ` +
      `distinguishable given the spread within each.`
    );
  }

  const described = changed
    .map((c) => {
      const direction = c.delta > 0 ? "rose" : "fell";
      const pct =
        c.percent_change === null ? "" : ` (${Math.abs(c.percent_change).toFixed(1)}%)`;
      return `${label(c.indicator)} ${direction} by ${Math.abs(c.delta).toFixed(3)}${pct}`;
    })
    .join("; ");

  const tail =
    unchanged > 0
      ? ` The remaining ${unchanged} showed no significant change.`
      : "";

  return `${head} Significant changes: ${described}.${tail}`;
}

export function interpretSubstrateBand(score: number): {
  band: "low" | "moderate" | "elevated" | "high" | "extreme";
  interpretation: string;
} {
  if (score < 20) {
    return {
      band: "low",
      interpretation:
        "Substrate is well hydrated with limited cured fuel. Fire arriving here would find " +
        "conditions that resist spread.",
    };
  }
  if (score < 40) {
    return {
      band: "moderate",
      interpretation:
        "Substrate shows seasonal drying within the normal range for this bioregion.",
    };
  }
  if (score < 60) {
    return {
      band: "elevated",
      interpretation:
        "Fuel moisture is drawn down and the ground is more receptive to fire than typical " +
        "for the period.",
    };
  }
  if (score < 80) {
    return {
      band: "high",
      interpretation:
        "Substrate is substantially cured and dry. Fire arriving here would find conditions " +
        "favourable to spread across most of the area.",
    };
  }
  return {
    band: "extreme",
    interpretation:
      "Substrate is at the dry end of the observed range across effectively the whole area. " +
      "Conditions favour rapid spread wherever ignition occurs.",
  };
}
