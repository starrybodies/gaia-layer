/**
 * The MCP surface, checked against the rule the codebase is actually held to.
 *
 * "A behaviour that exists in one transport and not the other is in the wrong place." That
 * is easy to write down and easy to lose: someone adds a REST route, the console uses it,
 * and the agent surface silently falls a version behind. So the first test here does not
 * check a behaviour at all — it checks that the set of tools this server advertises is
 * exactly the set the service dispatches, in both directions. Adding a tool to one side and
 * not the other fails here rather than six months later in a support thread.
 *
 * The rest is the argument dispatch, which is the only logic this module has. It runs
 * against a real archive built in a temporary directory, in the shape the pipeline writes,
 * because the failure worth catching is a request shape that parses and then asks the
 * service for the wrong thing.
 *
 * There is also a test for the tool descriptions. That is not decoration: the description is
 * the entire documentation an agent gets before it calls something, and an index served
 * without the direction of its scale is an index that will be read backwards.
 */

import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DuckDBInstance } from "@duckdb/node-api";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

let directory: string;

const A = "8812d0232bfffff";
const B = "8812d02321fffff";
const UNMEASURED = "8812d0340bfffff";

const VALUES: Record<string, Record<number, number | null>> = {
  [A]: { 2022: 0.4, 2023: 1.6 },
  [B]: { 2022: 1.1, 2023: 0.9 },
  [UNMEASURED]: { 2022: null, 2023: null },
};

async function buildFixtureArchive(root: string): Promise<void> {
  const instance = await DuckDBInstance.create(join(root, "catalog.duckdb"));
  const conn = await instance.connect();
  await conn.run(`
    CREATE TABLE method (method_id VARCHAR PRIMARY KEY, name VARCHAR, formula VARCHAR,
                         citation VARCHAR, doi VARCHAR, notes VARCHAR, version VARCHAR);
    CREATE TABLE source (source_id VARCHAR PRIMARY KEY, dataset VARCHAR, version VARCHAR,
                         access_route VARCHAR, uri VARCHAR, native_resolution_m DOUBLE,
                         native_timestep VARCHAR, citation VARCHAR, licence VARCHAR,
                         retrieved TIMESTAMPTZ);
    CREATE TABLE source_set (source_set_id VARCHAR PRIMARY KEY, created TIMESTAMPTZ);
    CREATE TABLE source_set_member (source_set_id VARCHAR, source_id VARCHAR);
    CREATE TABLE run (run_id VARCHAR PRIMARY KEY, command VARCHAR, component VARCHAR,
                      method_id VARCHAR, source_set_id VARCHAR, parameters VARCHAR,
                      started TIMESTAMPTZ, finished TIMESTAMPTZ, status VARCHAR, error VARCHAR,
                      pipeline_version VARCHAR, algorithm_version VARCHAR);
    CREATE TABLE h3_cell (h3 VARCHAR PRIMARY KEY, res UTINYINT, parent_h3 VARCHAR,
                          lat DOUBLE, lon DOUBLE, area_km2 REAL);
    CREATE TABLE partition_index (component VARCHAR, year INTEGER, path VARCHAR, rows BIGINT,
                                  run_id VARCHAR, written TIMESTAMPTZ);
  `);

  for (const component of ["eii", "a_structure"]) {
    for (const year of [2022, 2023]) {
      const tag = `${component}_${year}`;
      await conn.run(`
        INSERT INTO method VALUES ('m_${tag}', '${component}', NULL, 'A citation (2024).',
          NULL, 'what this cannot establish', '1.0');
        INSERT INTO source VALUES ('s_${tag}', 'dataset', 'v1', 'https-open',
          'https://example.invalid/${tag}', 9000.0, 'annual', 'Source citation (2024).',
          'CC-BY-4.0', TIMESTAMPTZ '2026-08-11 00:00:00+00');
        INSERT INTO source_set VALUES ('set_${tag}', TIMESTAMPTZ '2026-08-11 00:00:00+00');
        INSERT INTO source_set_member VALUES ('set_${tag}', 's_${tag}');
        INSERT INTO run VALUES ('run_${tag}', 'build', '${component}', 'm_${tag}',
          'set_${tag}', '{}', TIMESTAMPTZ '2026-08-11 00:00:00+00',
          TIMESTAMPTZ '2026-08-11 00:01:00+00', 'succeeded', NULL, '0.2.0', '1');
        INSERT INTO partition_index VALUES ('${component}', ${year}, 'path', 3, 'run_${tag}',
          TIMESTAMPTZ '2026-08-11 00:01:00+00');
      `);

      const rows = Object.entries(VALUES)
        .map(([h3, byYear]) => {
          const value = byYear[year];
          return (
            `('${h3}', DATE '${year}-01-01', DATE '${year}-08-14', '${component}', ` +
            `${value === null ? "NULL" : value}, 'standard_error', ` +
            `${value === null ? "NULL" : 0.3}, ${value === null ? 0.0 : 1.0}, ` +
            `'m_${tag}', 'run_${tag}', 'set_${tag}', '')`
          );
        })
        .join(",\n");

      const partition = join(root, `component=${component}`, `year=${year}`);
      mkdirSync(partition, { recursive: true });
      await conn.run(`
        COPY (
          SELECT * FROM (VALUES ${rows}) AS t(h3, period_start, period_end, component, value,
            uncertainty_type, uncertainty_value, valid_fraction, method_id, run_id,
            source_set_id, constraint_flags)
        ) TO '${join(partition, "part.parquet").replace(/'/g, "''")}' (FORMAT PARQUET);
      `);
    }
  }

  await conn.run(`
    INSERT INTO h3_cell VALUES ('${A}', 8, '8712d0232ffffff', 49.9, -119.5, 0.74);
    INSERT INTO h3_cell VALUES ('${B}', 8, '8712d0232ffffff', 49.91, -119.51, 0.74);
    INSERT INTO h3_cell VALUES ('${UNMEASURED}', 8, '8712d0340ffffff', 49.5, -119.9, 0.74);
  `);

  conn.closeSync();
  instance.closeSync();
}

beforeAll(async () => {
  directory = mkdtempSync(join(tmpdir(), "gaia-mcp-"));
  process.env["GAIA_EII_DIR"] = directory;
  process.env["GAIA_EII_AUDIT_PATH"] = join(directory, "audit.duckdb");
  await buildFixtureArchive(directory);

  writeFileSync(
    join(directory, "dossier.json"),
    JSON.stringify({
      generated: "2026-08-13T00:00:00+00:00",
      run_id: "run_DOSSIER",
      method_id: "eii.diligence_dossier",
      method_version: "1.0.0",
      source_set_id: "set_DOSSIER",
      verdict: "PASS",
      gate_statement: "Component A, added to baseline_3, produces a positive delta.",
      disclosure_count: 1,
      sections: [
        {
          id: "cross_fire_skill",
          title: "Read this before the headline",
          statement: "The baseline has no demonstrated cross-fire skill.",
          kind: "figures",
          disclosure: true,
          caveat: "",
          columns: [],
          rows: [],
          figures: [],
        },
        {
          id: "verdict",
          title: "The gate",
          statement: "PASS.",
          kind: "figures",
          disclosure: false,
          caveat: "",
          columns: [],
          rows: [],
          figures: [],
        },
      ],
    }),
  );
}, 60_000);

afterAll(async () => {
  const { closeArchive, closeAudit } = await import("@gaia/service");
  await closeArchive();
  await closeAudit();
  rmSync(directory, { recursive: true, force: true });
});

describe("the two transports advertise the same surface", () => {
  it("defines exactly the tools the service dispatches", async () => {
    const { EII_TOOL_DEFINITIONS } = await import("./eii-tools.js");
    const { EII_TOOL_NAMES } = await import("@gaia/service");

    const advertised = EII_TOOL_DEFINITIONS.map((tool) => tool.name).sort();
    expect(advertised).toEqual([...EII_TOOL_NAMES].sort());
  });

  it("routes every tool it advertises rather than falling through to the error", async () => {
    const { EII_TOOL_DEFINITIONS, callEii } = await import("./eii-tools.js");

    for (const tool of EII_TOOL_DEFINITIONS) {
      // Called with deliberately empty arguments: what matters is that the dispatcher
      // recognises the name. An unrouted tool says "Unknown EII tool"; a routed one
      // complains about its arguments, or answers.
      await expect(
        (async () => {
          try {
            await callEii(tool.name, {});
          } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            if (message.includes("Unknown EII tool")) throw new Error(`unrouted: ${tool.name}`);
          }
        })(),
      ).resolves.toBeUndefined();
    }
  });

  it("refuses a name it does not advertise", async () => {
    const { callEii } = await import("./eii-tools.js");
    await expect(callEii("delete_everything", {})).rejects.toThrow(/Unknown EII tool/);
  });
});

describe("the descriptions an agent reads before it calls anything", () => {
  it("say which way the scale runs on every tool that returns an index", async () => {
    const { EII_TOOL_DEFINITIONS } = await import("./eii-tools.js");
    const indexTools = EII_TOOL_DEFINITIONS.filter((tool) =>
      ["get_eii", "get_component", "portfolio_scan", "portfolio_ranking", "portfolio_change"]
        .includes(tool.name),
    );

    expect(indexTools).toHaveLength(5);
    for (const tool of indexTools) {
      expect(tool.description).toContain("Higher is worse");
      expect(tool.description).toContain("0.74 km2");
    }
  });

  it("tell an agent that the portfolio tools take cell ids and nothing else", async () => {
    const { EII_TOOL_DEFINITIONS } = await import("./eii-tools.js");
    const ranking = EII_TOOL_DEFINITIONS.find((tool) => tool.name === "portfolio_ranking");
    const cells = ranking?.inputSchema.properties.cells as { description?: string };

    expect(cells.description).toContain("Cell identifiers only");
    expect(cells.description).toContain("no field for an address");
  });

  it("warn an agent not to drop the dossier's disclosures when summarising", async () => {
    const { EII_TOOL_DEFINITIONS } = await import("./eii-tools.js");
    const dossier = EII_TOOL_DEFINITIONS.find((tool) => tool.name === "read_dossier");

    expect(dossier?.description).toContain("must not drop them");
  });
});

describe("argument dispatch", () => {
  it("passes a cell and a year through to the composite", async () => {
    const { callEii } = await import("./eii-tools.js");
    const answer = (await callEii("get_eii", { h3: A, year: 2023 })) as {
      index: { value: number };
    };

    expect(answer.index.value).toBeCloseTo(1.6, 6);
  });

  it("requires a cell rather than defaulting to one", async () => {
    const { callEii } = await import("./eii-tools.js");
    await expect(callEii("get_eii", {})).rejects.toThrow(/h3 is required/);
  });

  it("requires a component name for get_component", async () => {
    const { callEii } = await import("./eii-tools.js");
    await expect(callEii("get_component", { h3: A })).rejects.toThrow(/component is required/);
  });

  it("ranks a book and names what it could not score", async () => {
    const { callEii } = await import("./eii-tools.js");
    const answer = (await callEii("portfolio_ranking", {
      cells: [{ h3: A, weight: 100 }, { h3: B }, { h3: UNMEASURED }],
      year: 2023,
    })) as { scored: number; unmeasured: string[]; cells: { h3: string; rank: number | null }[] };

    expect(answer.scored).toBe(2);
    expect(answer.unmeasured).toEqual([UNMEASURED]);
    expect(answer.cells[0]?.h3).toBe(A);
    expect(answer.cells.find((cell) => cell.h3 === UNMEASURED)?.rank).toBeNull();
  });

  it("refuses a book line that is not a cell id", async () => {
    const { callEii } = await import("./eii-tools.js");
    await expect(
      callEii("portfolio_ranking", { cells: [{ h3: "1 Example Road" }] }),
    ).rejects.toThrow(/cell identifiers only/);
  });

  it("refuses a book that is not an array before the service sees it", async () => {
    const { callEii } = await import("./eii-tools.js");
    await expect(callEii("portfolio_ranking", { cells: "everything" })).rejects.toThrow(
      /never an address or a coordinate/,
    );
  });

  it("compares two periods and reports what is not comparable", async () => {
    const { callEii } = await import("./eii-tools.js");
    const answer = (await callEii("portfolio_change", {
      cells: [{ h3: A }, { h3: B }, { h3: UNMEASURED }],
      before: 2022,
      after: 2023,
    })) as { comparable: number; worsened: number; improved: number; not_comparable: string[] };

    expect(answer.comparable).toBe(2);
    expect(answer.worsened).toBe(1);
    expect(answer.improved).toBe(1);
    expect(answer.not_comparable).toEqual([UNMEASURED]);
  });

  it("serves the dossier with its disclosures still in front", async () => {
    const { callEii } = await import("./eii-tools.js");
    const answer = (await callEii("read_dossier", {})) as {
      run_id: string;
      sections: { id: string; disclosure: boolean }[];
    };

    expect(answer.run_id).toBe("run_DOSSIER");
    expect(answer.sections[0]?.disclosure).toBe(true);
    expect(answer.sections[0]?.id).toBe("cross_fire_skill");
  });

  it("says the dossier is absent rather than serving half of one", async () => {
    const { readDossier } = await import("@gaia/service");
    const previous = process.env["GAIA_EII_DIR"];
    process.env["GAIA_EII_DIR"] = mkdtempSync(join(tmpdir(), "gaia-empty-"));
    try {
      expect(() => readDossier()).toThrow(/has not been built/);
    } finally {
      process.env["GAIA_EII_DIR"] = previous;
    }
  });
});
