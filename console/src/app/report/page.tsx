import { Shell } from "@/components/Shell";
import {
  Citation,
  ConfidenceBar,
  EmptyState,
  FlagList,
  INDICATOR_CODES,
  label,
  Panel,
  ProvenanceChain,
  Reading,
  StatusDot,
} from "@/components/primitives";
import { api, type EcologicalState, type SubstrateResponse } from "@/lib/api";

export const dynamic = "force-dynamic";

const BAND_COLOURS: Record<string, string> = {
  low: "text-signal",
  moderate: "text-signal",
  elevated: "text-amber",
  high: "text-signal",
  extreme: "text-rust",
};

/**
 * The substrate report.
 *
 * This is the artefact for an insurer or conservation call: numbers first, every figure
 * footnoted to its provenance chain, and the things the score does not model stated on the
 * page rather than buried. A report that hid its caveats would be a more persuasive
 * document and a less defensible one.
 */
export default async function ReportPage() {
  let coverage;
  try {
    coverage = await api.coverage();
  } catch (error) {
    return (
      <Shell active="/report">
        <div className="p-8">
          <EmptyState
            title="The layer is not answering."
            detail={error instanceof Error ? error.message : String(error)}
          />
        </div>
      </Shell>
    );
  }

  const aoi = coverage.aois[0];
  if (aoi === undefined) {
    return (
      <Shell active="/report">
        <div className="p-8">
          <EmptyState title="No area is ingested." detail="Run `make seed`." />
        </div>
      </Shell>
    );
  }

  const monthly = aoi.indicators.filter((i) => i.last_period_end < "2099-01-01");
  const end = monthly.reduce((a, i) => (i.last_period_end > a ? i.last_period_end : a), "");
  const start = monthly.reduce(
    (a, i) => (a === "" || i.first_period_start < a ? i.first_period_start : a),
    "",
  );

  let state: EcologicalState | null = null;
  let stateError: string | null = null;
  try {
    state = await api.ecologicalState(aoi.bbox, { start, end });
  } catch (error) {
    stateError = error instanceof Error ? error.message : String(error);
  }

  let substrate: SubstrateResponse | null = null;
  let substrateError: string | null = null;
  try {
    substrate = await api.substrateScore(aoi.bbox, end);
  } catch (error) {
    substrateError = error instanceof Error ? error.message : String(error);
  }

  // Narrowed together so the JSX below can read both without repeated null checks.
  const score = substrate === null ? undefined : substrate.score.value;

  // The chain shown in full is the substrate score's heaviest component, falling back to
  // whatever is available. Showing the alphabetically first indicator instead would put
  // "days since rain" in front of a reader who came to see how the satellite work is done.
  const traced =
    state?.indicators.find((i) => i.indicator === score?.components[0]?.indicator) ??
    state?.indicators.find((i) => i.indicator === "ndmi") ??
    state?.indicators[0];

  return (
    <Shell active="/report">
      <article className="mx-auto max-w-5xl px-6 py-10">
        <header className="border-line border-b pb-6">
          <p className="numeric text-[10px] tracking-[0.22em] text-muted uppercase">
            Ecological condition report
          </p>
          <h1 className="mt-2 text-2xl text-text">{aoi.name}</h1>
          <p className="numeric mt-2 text-[11px] text-muted">
            {start} to {end} ·{" "}
            {aoi.area_km2.toLocaleString(undefined, { maximumFractionDigits: 0 })} km² ·{" "}
            {aoi.analysis_crs} at {aoi.grid_resolution_m} m · geometry{" "}
            {state?.aoi.geometry_hash ?? "—"}
          </p>
          <p className="numeric mt-1 text-[10px] text-faint">
            pipeline {coverage.pipeline_version} · algorithm {coverage.algorithm_version} · last
            ingested {aoi.last_ingested_at?.slice(0, 19).replace("T", " ") ?? "unknown"}
          </p>
        </header>

        {/* ------------------------------------------------------- headline score */}
        <section className="grid gap-px border-b border-line py-8 md:grid-cols-[14rem_1fr] md:gap-8">
          {score === undefined || substrate === null ? (
            <div className="md:col-span-2">
              <EmptyState
                title="No substrate score for this period."
                detail={substrateError ?? undefined}
              />
            </div>
          ) : (
            <>
              <div>
                <p className="numeric text-[10px] tracking-[0.18em] text-muted uppercase">
                  Wildfire substrate
                </p>
                <p className={`numeric mt-1 text-6xl ${BAND_COLOURS[score.band] ?? "text-text"}`}>
                  {score.score.toFixed(1)}
                </p>
                <p className="numeric mt-1 text-xs tracking-[0.18em] text-muted uppercase">
                  {score.band} · of 100
                </p>
                <div className="mt-3">
                  <ConfidenceBar value={substrate.score.confidence} />
                </div>
                <p className="numeric mt-3 text-[10px] break-all text-faint">
                  {substrate.score.claim_id}
                </p>
              </div>

              <div>
                <p className="text-sm leading-relaxed text-text">{score.interpretation}</p>
                <p className="numeric mt-3 text-[11px] text-muted">
                  Scheme {score.weighting_scheme}
                  {substrate.missing_indicators.length > 0 &&
                    ` · missing: ${substrate.missing_indicators.join(", ")}`}
                </p>
                <div className="mt-4 border-l-2 border-line-bright pl-3">
                  <p className="numeric text-[10px] tracking-wider text-muted uppercase">
                    What this score does not model
                  </p>
                  <ul className="mt-1.5 space-y-1">
                    {score.caveats.map((caveat) => (
                      <li key={caveat} className="text-[11px] leading-relaxed text-muted">
                        {caveat}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </>
          )}
        </section>

        {/* ------------------------------------------------------- decomposition */}
        {score !== undefined && substrate !== null && (
          <section className="border-b border-line py-8">
            <h2 className="numeric text-[11px] tracking-[0.18em] text-muted uppercase">
              Score decomposition
            </h2>
            <p className="mt-1 text-xs text-muted">
              Every component that produced the number above, with the points it contributed.
              The score is the sum of the right-hand column.
            </p>

            <table className="mt-5 w-full border-collapse text-left">
              <thead>
                <tr className="border-b border-line">
                  {["Indicator", "Measured", "Normalised", "Weight", "Points"].map((h, i) => (
                    <th
                      key={h}
                      className={`numeric py-2 text-[10px] tracking-wider text-muted uppercase ${
                        i > 0 ? "text-right" : ""
                      }`}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {score.components.map((component) => (
                  <tr key={component.indicator} className="border-b border-line/60 align-top">
                    <td className="py-3 pr-4">
                      <p className="text-sm text-text">{label(component.indicator)}</p>
                      <p className="numeric text-[10px] text-faint">
                        {INDICATOR_CODES[component.indicator] ?? component.indicator}
                      </p>
                      <p className="mt-1.5 max-w-md text-[11px] leading-relaxed text-muted">
                        {component.rationale}
                      </p>
                      <p className="numeric mt-1 text-[10px] text-faint">
                        {component.normalization}
                      </p>
                    </td>
                    <td className="numeric py-3 text-right text-sm text-text">
                      {component.raw.value.toFixed(3)}
                      <span className="block text-[10px] text-faint">
                        {component.raw.unit === "index" ? "" : component.raw.unit}
                      </span>
                    </td>
                    <td className="numeric py-3 text-right text-sm text-dim">
                      {component.normalized.toFixed(3)}
                    </td>
                    <td className="numeric py-3 text-right text-sm text-dim">
                      {component.weight.toFixed(3)}
                    </td>
                    <td className="numeric py-3 text-right text-sm text-text">
                      {component.contribution.toFixed(2)}
                    </td>
                  </tr>
                ))}
                <tr>
                  <td colSpan={4} className="numeric py-3 text-right text-[11px] text-muted">
                    Total
                  </td>
                  <td className="numeric py-3 text-right text-base text-text">
                    {score.score.toFixed(2)}
                  </td>
                </tr>
              </tbody>
            </table>

            <div className="mt-6">
              <Citation method={substrate.score.method} />
            </div>
          </section>
        )}

        {/* ------------------------------------------------------- indicators */}
        <section className="border-b border-line py-8">
          <h2 className="numeric text-[11px] tracking-[0.18em] text-muted uppercase">
            Measured indicators
          </h2>
          {stateError !== null && (
            <p className="mt-3 text-xs text-rust">{stateError}</p>
          )}
          {state !== null && (
            <>
              <p className="mt-2 max-w-3xl text-xs leading-relaxed text-muted">
                {state.summary}
              </p>
              <div className="mt-4 grid gap-x-10 md:grid-cols-2">
                {state.indicators.map((envelope) => (
                  <Reading key={envelope.claim_id} envelope={envelope} />
                ))}
              </div>

              {state.rejected.length > 0 && (
                <div className="mt-6 border border-rust/40 p-4">
                  <p className="numeric text-[10px] tracking-wider text-rust uppercase">
                    Rejected by the constraint engine — not served as measurements
                  </p>
                  <ul className="mt-2 space-y-2">
                    {state.rejected.map((r) => (
                      <li key={r.claim_id} className="text-xs text-dim">
                        <span className="numeric text-muted">
                          {r.indicator} {r.period.start.slice(0, 7)}
                        </span>{" "}
                        — {r.reason}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </section>

        {/* ------------------------------------------------------- trends */}
        {state !== null && state.trends.length > 0 && (
          <section className="border-b border-line py-8">
            <h2 className="numeric text-[11px] tracking-[0.18em] text-muted uppercase">
              Trends over the period
            </h2>
            <table className="mt-4 w-full border-collapse text-left">
              <thead>
                <tr className="border-b border-line">
                  {["Indicator", "Direction", "Slope / month", "r²", "p", "n", "Significant"].map(
                    (h, i) => (
                      <th
                        key={h}
                        className={`numeric py-2 text-[10px] tracking-wider text-muted uppercase ${
                          i > 0 ? "text-right" : ""
                        }`}
                      >
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {state.trends.map((trend) => (
                  <tr key={trend.claim_id} className="border-b border-line/60">
                    <td className="py-2 text-sm text-text">{label(trend.indicator)}</td>
                    <td className="numeric py-2 text-right text-sm text-dim">
                      {trend.value.direction}
                    </td>
                    <td className="numeric py-2 text-right text-sm text-text">
                      {trend.value.slope_per_month.toFixed(4)}
                    </td>
                    <td className="numeric py-2 text-right text-sm text-muted">
                      {trend.value.r_squared.toFixed(2)}
                    </td>
                    <td className="numeric py-2 text-right text-sm text-muted">
                      {trend.value.p_value.toFixed(3)}
                    </td>
                    <td className="numeric py-2 text-right text-sm text-muted">
                      {trend.value.n_observations}
                    </td>
                    <td className="numeric py-2 text-right text-sm">
                      {trend.value.significant ? (
                        <span className="text-signal">yes</span>
                      ) : (
                        <span className="text-muted">no</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {/* ------------------------------------------------------- provenance */}
        {state !== null && traced !== undefined && (
          <section className="py-8">
            <h2 className="numeric text-[11px] tracking-[0.18em] text-muted uppercase">
              Provenance
            </h2>
            <p className="mt-1 text-xs text-muted">
              Every figure above traces to source observations. One chain shown in full;
              the rest are reachable by claim id through <span className="numeric">get_provenance</span>.
            </p>
            <div className="mt-5 grid gap-8 lg:grid-cols-2">
              <Panel title={`Chain for ${label(traced.indicator)}`}>
                <ProvenanceChain steps={traced.provenance} />
                <div className="mt-4">
                  <Citation method={traced.method} />
                </div>
              </Panel>
              <Panel title="Claim ids">
                <ul className="space-y-2">
                  {[...state.indicators, ...state.trends].map((envelope) => (
                    <li key={envelope.claim_id} className="flex items-baseline justify-between gap-3">
                      <span className="text-xs text-dim">
                        {label(envelope.indicator)}
                        {envelope.kind === "trend" && (
                          <span className="text-faint"> trend</span>
                        )}
                      </span>
                      <span className="numeric text-[10px] break-all text-faint">
                        {envelope.claim_id}
                      </span>
                    </li>
                  ))}
                </ul>
                <div className="mt-4 border-t border-line pt-3">
                  <p className="numeric text-[10px] tracking-wider text-muted uppercase">
                    Data quality
                  </p>
                  <ul className="mt-2 space-y-1">
                    {aoi.indicators.map((i) => (
                      <li key={i.indicator} className="flex items-baseline justify-between gap-3">
                        <span className="numeric text-[11px] text-muted">{i.indicator}</span>
                        <span className="numeric text-[11px] text-muted">
                          {i.period_count} periods · mean confidence{" "}
                          {i.mean_confidence.toFixed(2)} · {i.validated_count}/{i.flagged_count}/
                          {i.rejected_count}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <p className="numeric mt-2 text-[10px] text-faint">
                    counts are validated / flagged / rejected
                  </p>
                </div>
              </Panel>
            </div>
          </section>
        )}

        {state !== null && state.indicators[0] !== undefined && (
          <footer className="border-t border-line py-6">
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
              <StatusDot status={state.indicators[0].validation_status} />
              <span className="numeric text-[10px] text-faint">
                generated {state.generated_at.slice(0, 19).replace("T", " ")} UTC
              </span>
            </div>
            <FlagList flags={state.indicators.flatMap((i) => i.flags)} />
          </footer>
        )}
      </article>
    </Shell>
  );
}
