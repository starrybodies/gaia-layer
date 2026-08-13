/**
 * A book of cells, ranked and rolled up — and the change in that ranking between two dates.
 *
 * `portfolioScan` answers "how bad is this book". These two answer the questions an
 * underwriter asks next: *which* cells, and *what moved*.
 *
 * Nothing here computes an index. Every per-cell value is read from the archive exactly as
 * the pipeline persisted it, carrying the run, method and source set that produced it. What
 * this module adds is ordering and summation over a book the caller supplied, which cannot
 * be precomputed because the book is theirs — so it is done here, once, on the server, with
 * the method stated and an audit row written. It is never done in a browser.
 *
 * **The privacy shape is the API shape.** These functions take H3 identifiers. There is no
 * parameter for an address, a coordinate or a policy number, and there is no code path that
 * would use one. A res-8 cell is about 0.74 km2; that is the finest thing this layer needs
 * to know about an exposure, so it is the only thing it accepts.
 *
 * **Unmeasured cells are named, never dropped.** A book statistic computed over the cells
 * that happened to have data gets better as coverage gets worse, and the caller has no way
 * to see it happening. Every function here reports what it could not score, by cell id.
 */

import { recordCall, type AuditEntry } from "./eii-audit.js";
import { ServiceError } from "./errors.js";
import { COMPOSITE, ORIENTATION, factsFor, type FactRow } from "./eii.js";

/**
 * One line of a book.
 *
 * `weight` is the caller's own exposure measure — insured value, replacement cost, count of
 * risks, whatever they price on. The layer does not interpret it beyond weighting a mean by
 * it, and does not store it.
 */
export interface BookCell {
  h3: string;
  weight?: number;
}

export interface RankedCell {
  h3: string;
  h3_parent: string;
  rank: number | null;
  value: number | null;
  uncertainty_value: number | null;
  valid_fraction: number;
  constraint_flags: string;
  weight: number | null;
  period_start: string;
  period_end: string;
  method_id: string;
  run_id: string;
  source_set_id: string;
}

export interface ParentRollup {
  h3_parent: string;
  cells: number;
  scored: number;
  unmeasured: number;
  mean_index: number | null;
  weighted_index: number | null;
  worst_cell: string | null;
  worst_value: number | null;
  weight: number;
}

export interface PortfolioRanking {
  as_of: string | null;
  component: string;
  requested: number;
  scored: number;
  unmeasured: string[];
  cells: RankedCell[];
  parents: ParentRollup[];
  weighted_index: number | null;
  orientation: string;
  method_justification: string;
  audit: AuditEntry;
}

/** H3 resolution-7 parent, derived from the cell id rather than looked up. */
function parentOf(h3: string): string {
  // An H3 index carries its resolution in bits 52-55 and its child digits three bits at a
  // time below that. Stepping up one resolution is: decrement the resolution field, and set
  // the digit for the old resolution to 7, which is the "unused" marker. This is the same
  // arithmetic `h3ToParent` performs, done here so the service does not take an H3 binding
  // for one field of one response.
  const index = BigInt(`0x${h3}`);
  const resolution = Number((index >> 52n) & 0xfn);
  if (resolution < 1) {
    throw new ServiceError("invalid_request", `cell ${h3} has no parent at resolution 7`);
  }
  const parentResolution = BigInt(resolution - 1);
  const shift = BigInt(3 * (15 - resolution));
  const withResolution = (index & ~(0xfn << 52n)) | (parentResolution << 52n);
  return (withResolution | (0x7n << shift)).toString(16);
}

function dedupe(cells: BookCell[]): Map<string, number | null> {
  const book = new Map<string, number | null>();
  for (const entry of cells) {
    const h3 = entry.h3?.trim().toLowerCase();
    if (h3 === undefined || h3 === "") {
      throw new ServiceError("invalid_request", "every book line needs an h3 cell id");
    }
    if (!/^[0-9a-f]{15,16}$/.test(h3)) {
      throw new ServiceError(
        "invalid_request",
        `${entry.h3} is not an H3 cell id. This endpoint takes cell identifiers only — ` +
          "never an address or a coordinate.",
      );
    }
    const weight = entry.weight ?? null;
    // A cell listed twice is one cell with the combined exposure behind it, not two.
    book.set(h3, weight === null ? (book.get(h3) ?? null) : (book.get(h3) ?? 0) + weight);
  }
  return book;
}

function weightedMean(rows: { value: number; weight: number | null }[]): number | null {
  const usable = rows.filter((row) => row.weight !== null && row.weight > 0);
  if (usable.length === 0) return null;
  const total = usable.reduce((sum, row) => sum + (row.weight as number), 0);
  if (total <= 0) return null;
  return usable.reduce((sum, row) => sum + row.value * (row.weight as number), 0) / total;
}

export interface PortfolioRankingRequest {
  cells: BookCell[];
  year?: number;
  component?: string;
}

/**
 * Rank the cells of a book, and roll the ranking up to the res-7 parents.
 *
 * Ranking is dense and descending, so rank 1 is the cell furthest in the direction
 * associated with more severe fire. Unmeasured cells carry a null rank rather than a rank at
 * the bottom of the list: an unmeasured cell is not a good cell.
 */
export async function portfolioRanking(
  request: PortfolioRankingRequest,
): Promise<PortfolioRanking> {
  const component = request.component ?? COMPOSITE;
  const book = dedupe(request.cells);
  if (book.size === 0) {
    throw new ServiceError("invalid_request", "no cells were given to rank");
  }

  const rows = await factsFor(component, [...book.keys()], request.year);
  const byCell = new Map<string, FactRow>(rows.map((row) => [row.h3, row]));

  const ranked: RankedCell[] = [...book.keys()].map((h3) => {
    const row = byCell.get(h3);
    return {
      h3,
      h3_parent: parentOf(h3),
      rank: null,
      value: row?.value ?? null,
      uncertainty_value: row?.uncertainty_value ?? null,
      valid_fraction: row?.valid_fraction ?? 0,
      constraint_flags: row?.constraint_flags ?? "",
      weight: book.get(h3) ?? null,
      period_start: row?.period_start ?? "",
      period_end: row?.period_end ?? "",
      method_id: row?.method_id ?? "",
      run_id: row?.run_id ?? "",
      source_set_id: row?.source_set_id ?? "",
    };
  });

  const scored = ranked.filter((cell) => cell.value !== null);
  [...scored]
    .sort((a, b) => (b.value as number) - (a.value as number))
    .forEach((cell, index) => {
      cell.rank = index + 1;
    });
  ranked.sort((a, b) => {
    if (a.rank === null && b.rank === null) return a.h3 < b.h3 ? -1 : 1;
    if (a.rank === null) return 1;
    if (b.rank === null) return -1;
    return a.rank - b.rank;
  });

  const parents = new Map<string, RankedCell[]>();
  for (const cell of ranked) {
    const bucket = parents.get(cell.h3_parent) ?? [];
    bucket.push(cell);
    parents.set(cell.h3_parent, bucket);
  }

  const rollups: ParentRollup[] = [...parents.entries()]
    .map(([h3_parent, children]) => {
      const measured = children.filter((cell) => cell.value !== null);
      const worst = measured.reduce<RankedCell | null>(
        (best, cell) =>
          best === null || (cell.value as number) > (best.value as number) ? cell : best,
        null,
      );
      return {
        h3_parent,
        cells: children.length,
        scored: measured.length,
        unmeasured: children.length - measured.length,
        mean_index:
          measured.length === 0
            ? null
            : measured.reduce((sum, cell) => sum + (cell.value as number), 0) / measured.length,
        weighted_index: weightedMean(
          measured.map((cell) => ({ value: cell.value as number, weight: cell.weight })),
        ),
        worst_cell: worst?.h3 ?? null,
        worst_value: worst?.value ?? null,
        weight: children.reduce((sum, cell) => sum + (cell.weight ?? 0), 0),
      };
    })
    .sort((a, b) => (b.mean_index ?? -Infinity) - (a.mean_index ?? -Infinity));

  const payload = {
    as_of: scored[0]?.period_end ?? null,
    component,
    requested: book.size,
    scored: scored.length,
    unmeasured: ranked.filter((cell) => cell.value === null).map((cell) => cell.h3),
    cells: ranked,
    parents: rollups,
    weighted_index: weightedMean(
      scored.map((cell) => ({ value: cell.value as number, weight: cell.weight })),
    ),
    orientation: ORIENTATION,
    method_justification:
      `Ranked ${scored.length} of ${book.size} cells on ${component}, descending, so rank 1 ` +
      "is the cell furthest in the direction associated with more severe fire. Cells the " +
      "archive cannot score carry a null rank and are listed by id rather than ranked last, " +
      "because an unmeasured cell is not a good cell. Resolution-7 rollups average the " +
      "scored children only and report how many children were unmeasured beside the mean. " +
      "Every per-cell value is read as the pipeline persisted it and carries its own run.",
  };
  const audit = await recordCall("portfolio_ranking", request, payload, scored.length);
  return { ...payload, audit };
}

export interface CellChange {
  h3: string;
  h3_parent: string;
  before: number | null;
  after: number | null;
  change: number | null;
  direction: "worse" | "better" | "unchanged" | "unmeasurable";
  weight: number | null;
  before_run_id: string;
  after_run_id: string;
}

export interface PortfolioChange {
  component: string;
  before: { year: number; as_of: string | null; scored: number };
  after: { year: number; as_of: string | null; scored: number };
  comparable: number;
  requested: number;
  not_comparable: string[];
  mean_change: number | null;
  weighted_change: number | null;
  worsened: number;
  improved: number;
  cells: CellChange[];
  orientation: string;
  method_justification: string;
  audit: AuditEntry;
}

export interface PortfolioChangeRequest {
  cells: BookCell[];
  before: number;
  after: number;
  component?: string;
}

/**
 * What moved in a book between two as-of dates.
 *
 * A cell is comparable only if both dates scored it. A cell measured once and not the other
 * time contributes nothing to the mean and is listed as not comparable, because a change
 * computed against a missing value is a change invented by the arithmetic.
 *
 * The two archives are separate runs over separate windows. This function does not check
 * that they used the same method version — it reports both run ids per cell so that a
 * reviewer can. A change across a method change is a real thing to want to see, and hiding
 * it behind a refusal would be worse than showing it with the run ids attached.
 */
export async function portfolioChange(
  request: PortfolioChangeRequest,
): Promise<PortfolioChange> {
  const component = request.component ?? COMPOSITE;
  if (request.before === request.after) {
    throw new ServiceError(
      "invalid_request",
      "the two periods are the same; there is nothing to compare",
    );
  }
  const book = dedupe(request.cells);
  if (book.size === 0) {
    throw new ServiceError("invalid_request", "no cells were given to compare");
  }
  const ids = [...book.keys()];

  const [beforeRows, afterRows] = await Promise.all([
    factsFor(component, ids, request.before),
    factsFor(component, ids, request.after),
  ]);
  const beforeBy = new Map(beforeRows.map((row) => [row.h3, row]));
  const afterBy = new Map(afterRows.map((row) => [row.h3, row]));

  const cells: CellChange[] = ids.map((h3) => {
    const before = beforeBy.get(h3)?.value ?? null;
    const after = afterBy.get(h3)?.value ?? null;
    const change = before === null || after === null ? null : after - before;
    return {
      h3,
      h3_parent: parentOf(h3),
      before,
      after,
      change,
      direction:
        change === null
          ? "unmeasurable"
          : change > 0
            ? "worse"
            : change < 0
              ? "better"
              : "unchanged",
      weight: book.get(h3) ?? null,
      before_run_id: beforeBy.get(h3)?.run_id ?? "",
      after_run_id: afterBy.get(h3)?.run_id ?? "",
    };
  });
  cells.sort((a, b) => (b.change ?? -Infinity) - (a.change ?? -Infinity));

  const comparable = cells.filter((cell) => cell.change !== null);
  const oneWay =
    comparable.length > 0 &&
    Math.max(
      comparable.filter((cell) => cell.direction === "worse").length,
      comparable.filter((cell) => cell.direction === "better").length,
    ) /
      comparable.length >=
      0.95;
  // A book that moves almost entirely one way is usually the climate lattice rather than the
  // book. Weather reaches these cells from an 0.25 degree reanalysis, so several hundred
  // sub-kilometre cells inside a handful of lattice nodes share a season, and a dry year
  // moves all of them together. Said here rather than left for a reviewer to ask about.
  const uniformity = oneWay
    ? " Nearly every comparable cell moved the same way. Before reading that as a portfolio " +
      "effect, note that the weather terms reach these cells from a 0.25 degree reanalysis " +
      "lattice: several hundred sub-kilometre cells sit inside a handful of lattice nodes " +
      "and share a season, so a dry year moves them together. What this comparison " +
      "distinguishes well is one cell against another; what it distinguishes poorly is one " +
      "cell against the region it sits in."
    : "";

  const payload = {
    component,
    before: {
      year: request.before,
      as_of: beforeRows[0]?.period_end ?? null,
      scored: beforeRows.filter((row) => row.value !== null).length,
    },
    after: {
      year: request.after,
      as_of: afterRows[0]?.period_end ?? null,
      scored: afterRows.filter((row) => row.value !== null).length,
    },
    requested: book.size,
    comparable: comparable.length,
    not_comparable: cells.filter((cell) => cell.change === null).map((cell) => cell.h3),
    mean_change:
      comparable.length === 0
        ? null
        : comparable.reduce((sum, cell) => sum + (cell.change as number), 0) / comparable.length,
    weighted_change: weightedMean(
      comparable.map((cell) => ({ value: cell.change as number, weight: cell.weight })),
    ),
    worsened: comparable.filter((cell) => cell.direction === "worse").length,
    improved: comparable.filter((cell) => cell.direction === "better").length,
    cells,
    orientation: ORIENTATION,
    method_justification:
      `Compared ${comparable.length} of ${book.size} cells between ${request.before} and ` +
      `${request.after}. A cell scored in only one of the two periods is listed as not ` +
      "comparable and contributes nothing to the mean; a change computed against a missing " +
      "value is a change invented by the arithmetic. Positive change means the cell moved " +
      "further in the direction associated with more severe fire. Both run ids are reported " +
      "per cell so that a change spanning a method change can be seen rather than assumed " +
      "away." +
      uniformity,
  };
  const audit = await recordCall("portfolio_change", request, payload, comparable.length);
  return { ...payload, audit };
}
