"use client";

/**
 * The read on a layer.
 *
 * A choropleth tells you there is variation. This tells you what the variation is made of:
 * how the values are distributed, how they break down by elevation, aspect and slope, what
 * terrain they track, and where the extreme decile actually sits on the ground.
 *
 * Every sentence and every number here was computed in SQL over validated cells. No model
 * wrote any of it — an interpretation of a measurement is a claim about that measurement,
 * and it is held to the same standard.
 */

import type { Interpretation, ZonalBand } from "@/lib/api";
import { Eyebrow } from "@/components/primitives";

function Bars({
  bands,
  format,
  invert,
}: {
  bands: ZonalBand[];
  format: (n: number) => string;
  invert: boolean;
}) {
  if (bands.length === 0) return null;
  const means = bands.map((b) => b.mean);
  const lo = Math.min(...means);
  const hi = Math.max(...means);
  const span = hi - lo || 1;

  return (
    <ul className="space-y-1.5">
      {bands.map((band) => {
        const t = (band.mean - lo) / span;
        // Colour by position within this breakdown's own range, oriented so the
        // fire-prone end is always warm.
        const heat = invert ? 1 - t : t;
        const colour = heat > 0.66 ? "bg-rust" : heat > 0.33 ? "bg-amber" : "bg-signal";
        return (
          <li key={band.band} className="grid grid-cols-[6.5rem_1fr_3.2rem] items-center gap-2">
            <span className="numeric text-muted truncate text-[10px]">{band.band}</span>
            <span className="bg-raised relative block h-2">
              <span
                className={`absolute inset-y-0 left-0 ${colour}`}
                style={{ width: `${Math.max(3, 6 + t * 94)}%` }}
              />
            </span>
            <span className="numeric text-dim text-right text-[10px]">{format(band.mean)}</span>
          </li>
        );
      })}
    </ul>
  );
}

export function InterpretationPanel({
  data,
  loading,
}: {
  data: Interpretation | null;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="border-line border-b p-4">
        <div className="flex items-center gap-2">
          <span className="bg-signal pulse inline-block h-1.5 w-1.5 rounded-full" />
          <span className="numeric text-muted text-[11px]">Reading the landscape…</span>
        </div>
      </div>
    );
  }
  if (data === null) return null;

  const isScore = data.indicator === "substrate_score";
  const fmt = (n: number): string => (isScore ? n.toFixed(1) : n.toFixed(3));
  // For most indicators a low number is the dry end; for the score and slope it is a high one.
  const invert = !(isScore || data.indicator === "slope_deg");

  const d = data.distribution;

  return (
    <>
      <section className="border-line border-b p-4">
        <Eyebrow>What this layer shows</Eyebrow>
        <ul className="mt-3 space-y-2.5">
          {data.readings.map((reading, i) => (
            <li key={i} className="flex gap-2.5">
              <span className="bg-signal mt-[7px] h-1 w-1 shrink-0 rounded-full" />
              <span className="text-dim text-[12px] leading-relaxed">{reading}</span>
            </li>
          ))}
        </ul>
        <p className="numeric text-faint mt-3 text-[10px] leading-relaxed">
          Computed from {d.count.toLocaleString()} validated cells. Template-assembled from
          the numbers below — not model-generated.
        </p>
      </section>

      <section className="border-line border-b p-4">
        <Eyebrow>Distribution</Eyebrow>
        <div className="mt-3 grid grid-cols-5 gap-1 text-center">
          {(
            [
              ["min", d.min],
              ["p10", d.p10],
              ["median", d.median],
              ["p90", d.p90],
              ["max", d.max],
            ] as [string, number][]
          ).map(([k, v]) => (
            <div key={k}>
              <p className="numeric text-text text-[11px]">{fmt(v)}</p>
              <p className="numeric text-faint mt-0.5 text-[9px] uppercase">{k}</p>
            </div>
          ))}
        </div>
        {/* The interquartile box against the full range, so spread is visible at a glance. */}
        <div className="bg-raised relative mt-3 h-2">
          <span
            className="bg-signal-dim absolute inset-y-0"
            style={{
              left: `${((d.p25 - d.min) / (d.max - d.min || 1)) * 100}%`,
              width: `${((d.p75 - d.p25) / (d.max - d.min || 1)) * 100}%`,
            }}
          />
          <span
            className="bg-text absolute inset-y-0 w-px"
            style={{ left: `${((d.median - d.min) / (d.max - d.min || 1)) * 100}%` }}
          />
        </div>
        <p className="numeric text-faint mt-1.5 text-[10px]">
          interquartile range · σ {fmt(d.std)}
        </p>
      </section>

      <section className="border-line border-b p-4">
        <Eyebrow>By elevation</Eyebrow>
        <div className="mt-3">
          <Bars bands={data.by_elevation} format={fmt} invert={invert} />
        </div>
      </section>

      <section className="border-line border-b p-4">
        <Eyebrow>By aspect</Eyebrow>
        <div className="mt-3">
          <Bars
            bands={data.by_aspect.filter((b) => b.band !== "flat")}
            format={fmt}
            invert={invert}
          />
        </div>
      </section>

      <section className="border-line border-b p-4">
        <Eyebrow>By slope</Eyebrow>
        <div className="mt-3">
          <Bars bands={data.by_slope} format={fmt} invert={invert} />
        </div>
      </section>

      {data.correlations.length > 0 && (
        <section className="border-line border-b p-4">
          <Eyebrow>Terrain correlation</Eyebrow>
          <ul className="mt-3 space-y-2">
            {data.correlations.map((c) => (
              <li key={c.against} className="flex items-center justify-between gap-3">
                <span className="text-muted text-[11px]">{c.against}</span>
                <span className="flex items-center gap-2">
                  <span className="bg-raised relative block h-1.5 w-20">
                    {/* Centred at zero: left of centre is negative, right is positive. */}
                    <span
                      className={`absolute inset-y-0 ${c.r >= 0 ? "bg-signal left-1/2" : "bg-amber right-1/2"}`}
                      style={{ width: `${Math.min(50, Math.abs(c.r) * 50)}%` }}
                    />
                    <span className="bg-line-bright absolute inset-y-0 left-1/2 w-px" />
                  </span>
                  <span className="numeric text-dim w-10 text-right text-[10px]">
                    {c.r.toFixed(2)}
                  </span>
                </span>
              </li>
            ))}
          </ul>
          <p className="numeric text-faint mt-2 text-[10px]">
            Pearson r across {data.correlations[0]?.n.toLocaleString()} cells
          </p>
        </section>
      )}

      <section className="border-line border-b p-4">
        <Eyebrow>Most fire-prone decile</Eyebrow>
        <p className="numeric text-text mt-2.5 text-lg">
          {data.extremes.cells.toLocaleString()}
          <span className="text-muted ml-1.5 text-[11px]">
            cells · {(data.extremes.share * 100).toFixed(0)}% of area
          </span>
        </p>
        <dl className="mt-3 space-y-1.5">
          {(
            [
              ["beyond", fmt(data.extremes.threshold)],
              [
                "mean elevation",
                data.extremes.mean_elevation_m === null
                  ? "—"
                  : `${data.extremes.mean_elevation_m.toFixed(0)} m`,
              ],
              [
                "mean slope",
                data.extremes.mean_slope_deg === null
                  ? "—"
                  : `${data.extremes.mean_slope_deg.toFixed(0)}°`,
              ],
              ["dominant aspect", data.extremes.dominant_aspect ?? "—"],
            ] as [string, string][]
          ).map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3">
              <dt className="text-muted text-[11px]">{k}</dt>
              <dd className="numeric text-dim text-[11px]">{v}</dd>
            </div>
          ))}
        </dl>
      </section>

      {data.departure !== null && (
        <section className="border-line border-b p-4">
          <Eyebrow>Against its own normal</Eyebrow>
          <p className="numeric text-text mt-2.5 text-lg">
            {(data.departure.drier_share * 100).toFixed(0)}%
            <span className="text-muted ml-1.5 text-[11px]">of the area below normal</span>
          </p>
          <p className="text-muted mt-2 text-[11px] leading-relaxed">
            {(data.departure.strongly_drier_share * 100).toFixed(0)}% is substantially below
            its twelve-month median. Being dry and being drier than usual are different
            claims; this is the second one.
          </p>
        </section>
      )}
    </>
  );
}
