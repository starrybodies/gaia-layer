/**
 * Ranking a book, and comparing two years of one.
 *
 * Three things here can go wrong quietly and all three are what the tests are for.
 *
 * A book statistic can improve as coverage falls. If an unmeasured cell is dropped rather
 * than named, a portfolio over half-covered ground reports the mean of the half that
 * happened to have data, and nothing in the response says so.
 *
 * A change can be invented by the arithmetic. A cell scored in one year and not the other
 * has no change; treating the missing side as zero produces a large move in whichever
 * direction the measured side sits.
 *
 * A res-7 parent can be computed wrong and still look right. The bit arithmetic that walks
 * an H3 index up a resolution produces a well-formed cell id whatever it does, so it is
 * checked against ids whose parents are known rather than against itself.
 *
 * The fixture is a real archive written by DuckDB in the shape the pipeline writes, with two
 * years, cells that share a parent, cells that do not, and cells the pipeline reached and
 * could not measure.
 */

import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DuckDBInstance } from "@duckdb/node-api";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

let directory: string;

// Four res-8 cells. The first two share a res-7 parent; the third and fourth do not.
const A = "8812d0232bfffff";
const B = "8812d02321fffff";
const C = "8812d03029fffff";
const UNMEASURED = "8812d0340bfffff";

// Verified against the h3 library rather than against this module's own arithmetic.
const PARENT_OF_A = "8712d0232ffffff";
const PARENT_OF_C = "8712d0302ffffff";

const VALUES: Record<string, Record<number, number | null>> = {
  [A]: { 2022: 0.4, 2023: 1.6 },
  [B]: { 2022: 1.1, 2023: 0.9 },
  [C]: { 2022: 2.0, 2023: 2.0 },
  [UNMEASURED]: { 2022: null, 2023: null },
};

async function buildFixtureArchive(root: string): Promise<void> {
  const instance = await DuckDBInstance.create(join(root, "catalog.duckdb"));
  const conn = await instance.connect();
  await conn.run(`
    CREATE TABLE method (method_id VARCHAR PRIMARY KEY, name VARCHAR, formula VARCHAR,
                         citation VARCHAR, doi VARCHAR, notes VARCHAR, version VARCHAR);
    CREATE TABLE run (run_id VARCHAR PRIMARY KEY, command VARCHAR, component VARCHAR,
                      method_id VARCHAR, source_set_id VARCHAR, parameters VARCHAR,
                      started TIMESTAMPTZ, finished TIMESTAMPTZ, status VARCHAR, error VARCHAR,
                      pipeline_version VARCHAR, algorithm_version VARCHAR);
    CREATE TABLE partition_index (component VARCHAR, year INTEGER, path VARCHAR, rows BIGINT,
                                  run_id VARCHAR, written TIMESTAMPTZ);
  `);

  for (const year of [2022, 2023]) {
    await conn.run(`
      INSERT INTO method VALUES ('m_eii_${year}', 'composite', NULL, 'A citation (2024).',
        NULL, NULL, '1.0');
      INSERT INTO run VALUES ('run_${year}', 'build', 'eii', 'm_eii_${year}', 'set_${year}',
        '{}', TIMESTAMPTZ '2026-08-11 00:00:00+00', TIMESTAMPTZ '2026-08-11 00:01:00+00',
        'succeeded', NULL, '0.2.0', '1');
      INSERT INTO partition_index VALUES ('eii', ${year}, 'path', 4, 'run_${year}',
        TIMESTAMPTZ '2026-08-11 00:01:00+00');
    `);

    const rows = Object.entries(VALUES)
      .map(([h3, byYear]) => {
        const value = byYear[year];
        return (
          `('${h3}', DATE '${year}-01-01', DATE '${year}-08-14', 'eii', ` +
          `${value === null ? "NULL" : value}, 'standard_error', ` +
          `${value === null ? "NULL" : 0.3}, ${value === null ? 0.0 : 1.0}, ` +
          `'m_eii_${year}', 'run_${year}', 'set_${year}', '')`
        );
      })
      .join(",\n");

    const partition = join(root, "component=eii", `year=${year}`);
    mkdirSync(partition, { recursive: true });
    await conn.run(`
      COPY (
        SELECT * FROM (VALUES ${rows}) AS t(h3, period_start, period_end, component, value,
          uncertainty_type, uncertainty_value, valid_fraction, method_id, run_id,
          source_set_id, constraint_flags)
      ) TO '${join(partition, "part.parquet").replace(/'/g, "''")}' (FORMAT PARQUET);
    `);
  }

  conn.closeSync();
  instance.closeSync();
}

beforeAll(async () => {
  directory = mkdtempSync(join(tmpdir(), "gaia-portfolio-"));
  process.env["GAIA_EII_DIR"] = directory;
  process.env["GAIA_EII_AUDIT_PATH"] = join(directory, "audit.duckdb");
  await buildFixtureArchive(directory);
}, 60_000);

afterAll(async () => {
  const { closeArchive } = await import("./eii-db.js");
  const { closeAudit } = await import("./eii-audit.js");
  await closeArchive();
  await closeAudit();
  rmSync(directory, { recursive: true, force: true });
});

const book = (weights = false) =>
  [A, B, C, UNMEASURED].map((h3, index) => ({
    h3,
    ...(weights ? { weight: (index + 1) * 100 } : {}),
  }));

describe("portfolio_ranking", () => {
  it("ranks worst first, because higher is the direction associated with more severe fire", async () => {
    const { portfolioRanking } = await import("./eii-portfolio.js");
    const answer = await portfolioRanking({ cells: book(), year: 2023 });

    expect(answer.cells.map((cell) => cell.h3).slice(0, 3)).toEqual([C, A, B]);
    expect(answer.cells[0]?.rank).toBe(1);
  });

  it("names the cells it could not score rather than ranking them last", async () => {
    const { portfolioRanking } = await import("./eii-portfolio.js");
    const answer = await portfolioRanking({ cells: book(), year: 2023 });

    expect(answer.unmeasured).toEqual([UNMEASURED]);
    expect(answer.scored).toBe(3);
    expect(answer.requested).toBe(4);
    const unmeasured = answer.cells.find((cell) => cell.h3 === UNMEASURED);
    expect(unmeasured?.rank).toBeNull();
    expect(unmeasured?.value).toBeNull();
  });

  it("says in its own justification how much of the book it scored", async () => {
    const { portfolioRanking } = await import("./eii-portfolio.js");
    const answer = await portfolioRanking({ cells: book(), year: 2023 });

    expect(answer.method_justification).toContain("3 of 4");
    expect(answer.method_justification).toContain("an unmeasured cell is not a good cell");
  });

  it("carries the run, method and source set of every value it shows", async () => {
    const { portfolioRanking } = await import("./eii-portfolio.js");
    const answer = await portfolioRanking({ cells: book(), year: 2023 });

    for (const cell of answer.cells.filter((entry) => entry.value !== null)) {
      expect(cell.run_id).toBe("run_2023");
      expect(cell.method_id).toBe("m_eii_2023");
      expect(cell.period_end).toBe("2023-08-14");
    }
  });

  it("rolls up to the res-7 parent the H3 library would give", async () => {
    const { portfolioRanking } = await import("./eii-portfolio.js");
    const answer = await portfolioRanking({ cells: book(), year: 2023 });
    const parents = new Map(answer.parents.map((row) => [row.h3_parent, row]));

    expect(parents.get(PARENT_OF_A)?.cells).toBe(2);
    expect(parents.get(PARENT_OF_A)?.mean_index).toBeCloseTo((1.6 + 0.9) / 2, 6);
    expect(parents.get(PARENT_OF_C)?.worst_cell).toBe(C);
  });

  it("reports how many children a parent could not measure beside the parent's mean", async () => {
    const { portfolioRanking } = await import("./eii-portfolio.js");
    const answer = await portfolioRanking({ cells: book(), year: 2023 });
    const withGap = answer.parents.find((row) => row.unmeasured > 0);

    expect(withGap).toBeDefined();
    expect(withGap?.scored).toBeLessThan(withGap?.cells ?? 0);
  });

  it("weights the book mean by the caller's own exposure measure when given one", async () => {
    const { portfolioRanking } = await import("./eii-portfolio.js");
    const plain = await portfolioRanking({ cells: book(), year: 2023 });
    const weighted = await portfolioRanking({ cells: book(true), year: 2023 });

    // Weights 100, 200, 300 on values 1.6, 0.9, 2.0.
    expect(weighted.weighted_index).toBeCloseTo(
      (1.6 * 100 + 0.9 * 200 + 2.0 * 300) / 600,
      6,
    );
    expect(plain.weighted_index).toBeNull();
  });

  it("treats a cell listed twice as one cell with the exposure combined", async () => {
    const { portfolioRanking } = await import("./eii-portfolio.js");
    const answer = await portfolioRanking({
      cells: [
        { h3: A, weight: 10 },
        { h3: A, weight: 15 },
      ],
      year: 2023,
    });

    expect(answer.requested).toBe(1);
    expect(answer.cells[0]?.weight).toBe(25);
  });

  it("refuses anything that is not a cell id", async () => {
    const { portfolioRanking } = await import("./eii-portfolio.js");

    await expect(
      portfolioRanking({ cells: [{ h3: "1 Example Road, Kelowna" }] }),
    ).rejects.toThrow(/cell identifiers only/);
  });

  it("refuses an empty book rather than returning an empty answer", async () => {
    const { portfolioRanking } = await import("./eii-portfolio.js");
    await expect(portfolioRanking({ cells: [] })).rejects.toThrow(/no cells/);
  });

  it("writes an audit row for the call", async () => {
    const { portfolioRanking } = await import("./eii-portfolio.js");
    const answer = await portfolioRanking({ cells: book(), year: 2023 });

    expect(answer.audit.tool).toBe("portfolio_ranking");
    expect(answer.audit.entry_id).toMatch(/^[0-9a-f]{32}$/);
  });
});

describe("portfolio_change", () => {
  it("reports the direction each cell moved", async () => {
    const { portfolioChange } = await import("./eii-portfolio.js");
    const answer = await portfolioChange({ cells: book(), before: 2022, after: 2023 });
    const byCell = new Map(answer.cells.map((cell) => [cell.h3, cell]));

    expect(byCell.get(A)?.change).toBeCloseTo(1.2, 6);
    expect(byCell.get(A)?.direction).toBe("worse");
    expect(byCell.get(B)?.direction).toBe("better");
    expect(byCell.get(C)?.direction).toBe("unchanged");
  });

  it("counts what worsened and what improved", async () => {
    const { portfolioChange } = await import("./eii-portfolio.js");
    const answer = await portfolioChange({ cells: book(), before: 2022, after: 2023 });

    expect(answer.worsened).toBe(1);
    expect(answer.improved).toBe(1);
    expect(answer.comparable).toBe(3);
  });

  it("does not invent a change for a cell measured in only one of the two years", async () => {
    const { portfolioChange } = await import("./eii-portfolio.js");
    const answer = await portfolioChange({ cells: book(), before: 2022, after: 2023 });
    const cell = answer.cells.find((entry) => entry.h3 === UNMEASURED);

    expect(cell?.change).toBeNull();
    expect(cell?.direction).toBe("unmeasurable");
    expect(answer.not_comparable).toEqual([UNMEASURED]);
  });

  it("excludes the incomparable cells from the mean rather than counting them as no change", async () => {
    const { portfolioChange } = await import("./eii-portfolio.js");
    const answer = await portfolioChange({ cells: book(), before: 2022, after: 2023 });

    expect(answer.mean_change).toBeCloseTo((1.2 + -0.2 + 0.0) / 3, 6);
  });

  it("reports both run ids so a change across a method change can be seen", async () => {
    const { portfolioChange } = await import("./eii-portfolio.js");
    const answer = await portfolioChange({ cells: book(), before: 2022, after: 2023 });
    const cell = answer.cells.find((entry) => entry.h3 === A);

    expect(cell?.before_run_id).toBe("run_2022");
    expect(cell?.after_run_id).toBe("run_2023");
  });

  it("refuses to compare a period with itself", async () => {
    const { portfolioChange } = await import("./eii-portfolio.js");
    await expect(
      portfolioChange({ cells: book(), before: 2023, after: 2023 }),
    ).rejects.toThrow(/nothing to compare/);
  });
});

describe("the uniformity caveat", () => {
  it("says so when nearly every cell moved the same way", async () => {
    const { portfolioChange } = await import("./eii-portfolio.js");
    // A, B and C all rose between the fixture's two years except B, so force the case with a
    // book of the two that did move the same way.
    const answer = await portfolioChange({
      cells: [{ h3: A }],
      before: 2022,
      after: 2023,
    });

    expect(answer.method_justification).toContain("0.25 degree reanalysis");
    expect(answer.method_justification).toContain("distinguishes poorly");
  });

  it("stays quiet when the book moved both ways", async () => {
    const { portfolioChange } = await import("./eii-portfolio.js");
    const answer = await portfolioChange({ cells: book(), before: 2022, after: 2023 });

    expect(answer.method_justification).not.toContain("0.25 degree reanalysis");
  });
});
