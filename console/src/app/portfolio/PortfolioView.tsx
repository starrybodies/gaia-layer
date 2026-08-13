"use client";

/**
 * Surface C — the portfolio, ranked (C3) and compared across two dates (C2).
 *
 * The same rule as the diligence workbench holds here, in a harder place. Every per-cell
 * index value is precomputed in Python and persisted with its run, method and source set;
 * the ranking and the res-7 rollups are computed once on the server by
 * `@gaia/service`'s `portfolioRanking`, which writes an audit row and returns its own
 * method justification. This component sorts nothing, averages nothing and rounds nothing
 * that has not already been decided upstream — it formats and lays out.
 *
 * **What the client sends.** H3 cell identifiers and their own exposure weights. Nothing
 * else. There is no field in the request for an address, a coordinate or a policy number,
 * because a res-8 cell is about 0.74 km2 and that is the finest thing the layer needs. The
 * demo book below is built the same way, so what is demonstrated is the real shape of the
 * integration rather than a version of it with the privacy bolted on afterwards.
 *
 * **Unmeasured cells are shown, not dropped.** The count sits beside the mean everywhere it
 * appears. A portfolio statistic that improves as coverage falls is the failure mode this
 * surface exists to make visible.
 */

import { useMemo, useState } from "react";
import { Eyebrow, EmptyState, Panel } from "@/components/primitives";
import {
  api,
  type DemoBook,
  type PortfolioChange,
  type PortfolioRanking,
} from "@/lib/api";
import { PortfolioMap } from "./PortfolioMap";

type Mode = "rank" | "change";

function number(value: number | null, places = 3): string {
  return value === null ? "—" : value.toFixed(places);
}

function signed(value: number | null, places = 3): string {
  if (value === null) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(places)}`;
}

function money(value: number): string {
  return value.toLocaleString("en-CA", { maximumFractionDigits: 0 });
}

const DIRECTION_TONE: Record<string, string> = {
  worse: "text-rust",
  better: "text-signal",
  unchanged: "text-muted",
  unmeasurable: "text-faint",
};

export function PortfolioView({ book }: { book: DemoBook }) {
  const [mode, setMode] = useState<Mode>("rank");
  const [weighted, setWeighted] = useState(true);
  const [ranking, setRanking] = useState<PortfolioRanking | null>(null);
  const [change, setChange] = useState<PortfolioChange | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Cell ids and the client's own weights. This is the entire payload that leaves the
  // browser, and it is worth reading it as the contract it is.
  const cells = useMemo(
    () =>
      book.cells.map((cell) => ({
        h3: cell.h3,
        ...(weighted ? { weight: cell.synthetic_insured_value } : {}),
      })),
    [book, weighted],
  );

  async function run(next: Mode) {
    setBusy(true);
    setError(null);
    setMode(next);
    try {
      if (next === "rank") {
        setRanking(await api.portfolioRanking(cells, 2023));
      } else {
        setChange(await api.portfolioChange(cells, 2022, 2023));
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="border-amber/50 bg-surface border p-4">
        <p className="numeric text-amber text-[10px] tracking-[0.16em] uppercase">
          {book.label}
        </p>
        <p className="text-dim mt-2 max-w-4xl text-xs leading-relaxed">{book.warning}</p>
        <p className="text-faint mt-2 max-w-4xl text-[11px] leading-relaxed">{book.privacy}</p>
        <div className="numeric text-faint mt-3 flex flex-wrap gap-x-6 gap-y-1 text-[10px]">
          <span>{book.totals.cells} cells at resolution {book.resolution}</span>
          <span>{book.totals.exposures.toLocaleString("en-CA")} exposures</span>
          <span>notional {money(book.totals.synthetic_insured_value)}</span>
          <span>
            {book.footprint_source["dataset"]} {book.footprint_source["release"]}
          </span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void run("rank")}
          disabled={busy}
          className={`eyebrow border px-3 py-2 transition-colors disabled:opacity-50 ${
            mode === "rank"
              ? "border-signal text-signal"
              : "border-line text-muted hover:text-text"
          }`}
        >
          Rank the book — 2023
        </button>
        <button
          type="button"
          onClick={() => void run("change")}
          disabled={busy}
          className={`eyebrow border px-3 py-2 transition-colors disabled:opacity-50 ${
            mode === "change"
              ? "border-signal text-signal"
              : "border-line text-muted hover:text-text"
          }`}
        >
          What moved — 2022 to 2023
        </button>
        <label className="text-muted flex items-center gap-2 text-[11px]">
          <input
            type="checkbox"
            checked={weighted}
            onChange={(event) => setWeighted(event.target.checked)}
            className="accent-signal"
          />
          weight by the book&rsquo;s own exposure values
        </label>
        {busy && <span className="numeric text-faint text-[10px]">reading the archive…</span>}
      </div>

      {error !== null && (
        <div className="border-rust/50 bg-surface border p-4">
          <p className="text-rust text-xs">{error}</p>
        </div>
      )}

      {mode === "rank" && ranking !== null && <Ranking ranking={ranking} book={book} />}
      {mode === "change" && change !== null && <Change change={change} />}

      {mode === "rank" && ranking === null && !busy && (
        <EmptyState
          title="Nothing scanned yet"
          detail="Ranking sends four hundred H3 cell identifiers and their exposure weights to the layer. Nothing else leaves this page."
        />
      )}
    </div>
  );
}

function Coverage({
  requested,
  scored,
  unmeasured,
}: {
  requested: number;
  scored: number;
  unmeasured: string[];
}) {
  return (
    <div className="border-line border-t pt-3">
      <p className="numeric text-text text-2xl">
        {scored}
        <span className="text-faint text-base"> / {requested}</span>
      </p>
      <p className="eyebrow text-muted mt-1.5">cells scored</p>
      <p className="text-faint mt-1 text-[11px] leading-relaxed">
        {unmeasured.length === 0
          ? "Every cell in the book has a measurement behind it."
          : `${unmeasured.length} cells could not be scored. They are listed below rather than averaged away.`}
      </p>
    </div>
  );
}

function Ranking({ ranking, book }: { ranking: PortfolioRanking; book: DemoBook }) {
  const weights = new Map(book.cells.map((cell) => [cell.h3, cell]));
  const worst = ranking.cells.filter((cell) => cell.rank !== null).slice(0, 25);

  return (
    <div className="space-y-6">
      <PortfolioMap cells={ranking.cells} mode="index" />

      <Panel title="The book, at resolution 8">
        <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
          <Coverage
            requested={ranking.requested}
            scored={ranking.scored}
            unmeasured={ranking.unmeasured}
          />
          <div className="border-line border-t pt-3">
            <p className="numeric text-text text-2xl">{number(ranking.weighted_index)}</p>
            <p className="eyebrow text-muted mt-1.5">exposure-weighted index</p>
            <p className="text-faint mt-1 text-[11px] leading-relaxed">
              {ranking.weighted_index === null
                ? "No weights were sent, so no weighted figure is shown rather than an unweighted one wearing the name."
                : "Weighted by the book's own values, which in this demo are invented."}
            </p>
          </div>
          <div className="border-line border-t pt-3">
            <p className="numeric text-text text-2xl">{number(ranking.cells[0]?.value ?? null)}</p>
            <p className="eyebrow text-muted mt-1.5">worst cell</p>
            <p className="numeric text-faint mt-1 text-[10px] break-all">
              {ranking.cells[0]?.h3 ?? "—"}
            </p>
          </div>
          <div className="border-line border-t pt-3">
            <p className="numeric text-text text-2xl">{ranking.parents.length}</p>
            <p className="eyebrow text-muted mt-1.5">resolution-7 parents</p>
            <p className="numeric text-faint mt-1 text-[10px]">as of {ranking.as_of ?? "—"}</p>
          </div>
        </div>
        <p className="text-faint mt-4 max-w-4xl text-[11px] leading-relaxed">
          {ranking.method_justification}
        </p>
        <p className="text-faint mt-2 max-w-4xl text-[11px] leading-relaxed italic">
          {ranking.orientation}
        </p>
        <p className="numeric text-faint mt-2 text-[10px] break-all">
          audit {ranking.audit.entry_id}
        </p>
      </Panel>

      <Panel title="Aggregated to resolution 7">
        <div className="-mx-4 overflow-x-auto px-4">
          <table className="w-full min-w-[46rem] border-collapse text-left">
            <thead>
              <tr className="border-line border-b">
                {[
                  "parent cell",
                  "children",
                  "scored",
                  "unmeasured",
                  "mean index",
                  "weighted",
                  "worst child",
                ].map((column) => (
                  <th key={column} className="eyebrow text-faint py-2 pr-4 font-normal">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ranking.parents.slice(0, 30).map((parent) => (
                <tr key={parent.h3_parent} className="border-line/60 border-b last:border-b-0">
                  <td className="numeric text-text py-1.5 pr-4 text-[11px]">
                    {parent.h3_parent}
                  </td>
                  <td className="numeric text-dim py-1.5 pr-4 text-xs">{parent.cells}</td>
                  <td className="numeric text-dim py-1.5 pr-4 text-xs">{parent.scored}</td>
                  <td
                    className={`numeric py-1.5 pr-4 text-xs ${
                      parent.unmeasured > 0 ? "text-amber" : "text-faint"
                    }`}
                  >
                    {parent.unmeasured}
                  </td>
                  <td className="numeric text-text py-1.5 pr-4 text-xs">
                    {number(parent.mean_index)}
                  </td>
                  <td className="numeric text-dim py-1.5 pr-4 text-xs">
                    {number(parent.weighted_index)}
                  </td>
                  <td className="numeric text-faint py-1.5 pr-4 text-[11px]">
                    {parent.worst_cell ?? "—"} {number(parent.worst_value, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="The twenty-five worst cells">
        <div className="-mx-4 overflow-x-auto px-4">
          <table className="w-full min-w-[46rem] border-collapse text-left">
            <thead>
              <tr className="border-line border-b">
                {[
                  "rank",
                  "cell",
                  "index",
                  "uncertainty",
                  "exposures",
                  "notional value",
                  "run",
                ].map((column) => (
                  <th key={column} className="eyebrow text-faint py-2 pr-4 font-normal">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {worst.map((cell) => (
                <tr key={cell.h3} className="border-line/60 border-b last:border-b-0">
                  <td className="numeric text-faint py-1.5 pr-4 text-xs">{cell.rank}</td>
                  <td className="numeric text-text py-1.5 pr-4 text-[11px]">{cell.h3}</td>
                  <td className="numeric text-text py-1.5 pr-4 text-xs">{number(cell.value)}</td>
                  <td className="numeric text-dim py-1.5 pr-4 text-xs">
                    ± {number(cell.uncertainty_value, 2)}
                  </td>
                  <td className="numeric text-dim py-1.5 pr-4 text-xs">
                    {weights.get(cell.h3)?.exposures ?? "—"}
                  </td>
                  <td className="numeric text-dim py-1.5 pr-4 text-xs">
                    {cell.weight === null ? "—" : money(cell.weight)}
                  </td>
                  <td className="numeric text-faint py-1.5 pr-4 text-[10px] break-all">
                    {cell.run_id}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      {ranking.unmeasured.length > 0 && (
        <Panel title={`${ranking.unmeasured.length} cells the archive could not score`}>
          <p className="text-dim text-xs leading-relaxed">
            These are named rather than dropped. A book mean computed over the cells that
            happened to have data gets better as coverage gets worse, and nothing in the
            number says so.
          </p>
          <p className="numeric text-faint mt-3 text-[10px] leading-relaxed break-all">
            {ranking.unmeasured.join(" · ")}
          </p>
        </Panel>
      )}
    </div>
  );
}

function Change({ change }: { change: PortfolioChange }) {
  const moved = change.cells.filter((cell) => cell.change !== null);
  return (
    <div className="space-y-6">
      <PortfolioMap cells={change.cells} mode="change" />

      <Panel title={`${change.before.year} to ${change.after.year}`}>
        <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
          <Coverage
            requested={change.requested}
            scored={change.comparable}
            unmeasured={change.not_comparable}
          />
          <div className="border-line border-t pt-3">
            <p className="numeric text-text text-2xl">{signed(change.mean_change)}</p>
            <p className="eyebrow text-muted mt-1.5">mean change</p>
            <p className="text-faint mt-1 text-[11px] leading-relaxed">
              Positive is a move further in the direction associated with more severe fire.
            </p>
          </div>
          <div className="border-line border-t pt-3">
            <p className="numeric text-text text-2xl">{signed(change.weighted_change)}</p>
            <p className="eyebrow text-muted mt-1.5">exposure-weighted change</p>
          </div>
          <div className="border-line border-t pt-3">
            <p className="numeric text-2xl">
              <span className="text-rust">{change.worsened}</span>
              <span className="text-faint text-base"> / </span>
              <span className="text-signal">{change.improved}</span>
            </p>
            <p className="eyebrow text-muted mt-1.5">worsened / improved</p>
            <p className="numeric text-faint mt-1 text-[10px]">
              {change.before.as_of ?? "—"} → {change.after.as_of ?? "—"}
            </p>
          </div>
        </div>
        <p className="text-faint mt-4 max-w-4xl text-[11px] leading-relaxed">
          {change.method_justification}
        </p>
        <p className="numeric text-faint mt-2 text-[10px] break-all">
          audit {change.audit.entry_id}
        </p>
      </Panel>

      <Panel title="The twenty-five largest moves">
        <div className="-mx-4 overflow-x-auto px-4">
          <table className="w-full min-w-[46rem] border-collapse text-left">
            <thead>
              <tr className="border-line border-b">
                {[
                  "cell",
                  change.before.year,
                  change.after.year,
                  "change",
                  "direction",
                  "runs compared",
                ].map((column) => (
                  <th key={String(column)} className="eyebrow text-faint py-2 pr-4 font-normal">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {moved.slice(0, 25).map((cell) => (
                <tr key={cell.h3} className="border-line/60 border-b last:border-b-0">
                  <td className="numeric text-text py-1.5 pr-4 text-[11px]">{cell.h3}</td>
                  <td className="numeric text-dim py-1.5 pr-4 text-xs">{number(cell.before)}</td>
                  <td className="numeric text-dim py-1.5 pr-4 text-xs">{number(cell.after)}</td>
                  <td className="numeric text-text py-1.5 pr-4 text-xs">
                    {signed(cell.change)}
                  </td>
                  <td
                    className={`numeric py-1.5 pr-4 text-[10px] tracking-wider uppercase ${
                      DIRECTION_TONE[cell.direction] ?? "text-muted"
                    }`}
                  >
                    {cell.direction}
                  </td>
                  <td className="numeric text-faint py-1.5 pr-4 text-[10px] break-all">
                    {cell.before_run_id === cell.after_run_id
                      ? cell.before_run_id
                      : `${cell.before_run_id} → ${cell.after_run_id}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      {change.not_comparable.length > 0 && (
        <Panel title={`${change.not_comparable.length} cells with nothing to compare`}>
          <p className="text-dim text-xs leading-relaxed">
            Scored in one of the two periods and not the other. They contribute nothing to the
            mean: a change computed against a missing value is a change invented by the
            arithmetic.
          </p>
          <p className="numeric text-faint mt-3 text-[10px] leading-relaxed break-all">
            {change.not_comparable.join(" · ")}
          </p>
        </Panel>
      )}
    </div>
  );
}

export function PortfolioIntro() {
  return (
    <div>
      <Eyebrow>Surface C · portfolio</Eyebrow>
      <h1 className="display text-text mt-2 text-2xl">A book of cells, ranked and compared</h1>
    </div>
  );
}
