/**
 * Surface B — the diligence workbench.
 *
 * Built for a model-validation team trying to break it, which sets two rules.
 *
 * The first is architectural: **this page computes nothing.** Every figure on it, every
 * ordering, every sentence of interpretation was worked out in Python by
 * `validate/dossier.py`, written to `data/eii/dossier.json` with the run id, method record
 * and source set that produced it, and is reproduced here verbatim. There is no arithmetic
 * in this file. A number an analyst screenshots off this page can be traced to a run; a
 * number computed in a browser cannot, and that is the whole difference between evidence and
 * a slide.
 *
 * The second is editorial: **the unwelcome findings render first, and render marked.** The
 * dossier orders its own sections and flags three of them as disclosures. This page respects
 * that order rather than laying out a grid of its own, because moving a bad finding below a
 * good one is how a diligence surface degrades without anyone deciding to degrade it.
 */

import { Shell } from "@/components/Shell";
import { Eyebrow, EmptyState, Panel } from "@/components/primitives";
import { api, type Dossier, type DossierFigure, type DossierSection } from "@/lib/api";

export const dynamic = "force-dynamic";

async function loadDossier(): Promise<Dossier | null> {
  try {
    return await api.dossier();
  } catch {
    return null;
  }
}

function Figure({ figure }: { figure: DossierFigure }) {
  return (
    <div className="border-line border-t pt-3">
      <p className="numeric text-text text-2xl">{figure.display}</p>
      <p className="eyebrow text-muted mt-1.5">{figure.label}</p>
      {figure.interval !== null && (
        <p className="numeric text-dim mt-1 text-[11px]">
          95% {figure.interval.display}
          <span className={figure.interval.excludes_zero ? "text-signal" : "text-amber"}>
            {" "}
            · {figure.interval.excludes_zero ? "excludes zero" : "includes zero"}
          </span>
        </p>
      )}
      {figure.note !== "" && (
        <p className="text-faint mt-1 text-[11px] leading-relaxed">{figure.note}</p>
      )}
      <p className="numeric text-faint mt-1 text-[10px] break-all">{figure.source}</p>
    </div>
  );
}

function Table({ section }: { section: DossierSection }) {
  return (
    <div className="-mx-4 overflow-x-auto px-4">
      <table className="w-full min-w-[36rem] border-collapse text-left">
        <thead>
          <tr className="border-line border-b">
            {section.columns.map((column) => (
              <th key={column} className="eyebrow text-faint py-2 pr-4 font-normal">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {section.rows.map((row, index) => (
            <tr key={index} className="border-line/60 border-b last:border-b-0">
              {row.map((cell, position) => (
                <td
                  key={position}
                  className={`py-1.5 pr-4 text-xs ${
                    position === 0 ? "text-text" : "numeric text-dim"
                  }`}
                >
                  {String(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Section({ section }: { section: DossierSection }) {
  return (
    <section
      id={section.id}
      className={`border bg-surface ${
        section.disclosure ? "border-amber/50" : "border-line"
      }`}
    >
      <header
        className={`flex flex-wrap items-baseline justify-between gap-3 border-b px-4 py-2.5 ${
          section.disclosure ? "border-amber/40" : "border-line"
        }`}
      >
        <h2 className={`text-sm ${section.disclosure ? "text-amber" : "text-text"}`}>
          {section.title}
        </h2>
        {section.disclosure && (
          <span className="numeric text-amber text-[10px] tracking-[0.16em] uppercase">
            disclosure
          </span>
        )}
      </header>

      <div className="space-y-4 p-4">
        <p className="text-dim max-w-4xl text-sm leading-relaxed">{section.statement}</p>

        {section.figures.length > 0 && (
          <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
            {section.figures.map((figure) => (
              <Figure key={figure.label} figure={figure} />
            ))}
          </div>
        )}

        {section.rows.length > 0 && <Table section={section} />}

        {section.caveat !== "" && (
          <p className="border-line text-faint max-w-4xl border-l-2 pl-3 text-[11px] leading-relaxed">
            {section.caveat}
          </p>
        )}
      </div>
    </section>
  );
}

export default async function DiligencePage() {
  const dossier = await loadDossier();

  return (
    <Shell active="/diligence">
      <div className="mx-auto max-w-[100rem] space-y-6 px-5 py-8">
        <div>
          <Eyebrow>Surface B · model validation</Eyebrow>
          <h1 className="display text-text mt-2 text-2xl">The evidence, including against</h1>
          <p className="text-dim mt-3 max-w-3xl text-sm leading-relaxed">
            Everything below was computed in the pipeline and persisted with the run that
            produced it. This page renders it. It does not calculate, round, re-order or
            summarise, so a figure taken off this screen resolves to a run id rather than to a
            screenshot.
          </p>
        </div>

        {dossier === null ? (
          <EmptyState
            title="The dossier has not been built in this deployment"
            detail="It is written by pipeline/src/gaia_pipeline/validate/dossier.py from the gate's validation.json and the diagnostics run. Until that has run there is nothing here to show, and showing a partial version would be worse than showing none."
          />
        ) : (
          <>
            <Panel title="Provenance of this page">
              <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
                {[
                  ["Verdict", dossier.verdict ?? "unknown"],
                  ["Run", dossier.run_id],
                  ["Method", `${dossier.method_id} v${dossier.method_version}`],
                  ["Source set", dossier.source_set_id],
                  ["Generated", dossier.generated],
                  ["Disclosures", `${dossier.disclosure_count} rendered first`],
                ].map(([term, value]) => (
                  <div key={term} className="border-line border-t pt-2">
                    <dt className="eyebrow text-faint">{term}</dt>
                    <dd className="numeric text-dim mt-1 text-[11px] break-all">{value}</dd>
                  </div>
                ))}
              </dl>
              {dossier.gate_statement !== null && (
                <p className="text-faint mt-4 max-w-4xl text-[11px] leading-relaxed">
                  <span className="eyebrow text-muted">The gate, as written before fitting:</span>{" "}
                  {dossier.gate_statement}
                </p>
              )}
            </Panel>

            {dossier.sections.map((section) => (
              <Section key={section.id} section={section} />
            ))}
          </>
        )}
      </div>
    </Shell>
  );
}
