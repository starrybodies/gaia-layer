/**
 * The EII surface, against a real archive built in a temporary directory.
 *
 * A fixture archive rather than a mocked query layer, because most of what can go wrong
 * here is in the SQL: provenance resolved through the wrong join returns an empty chain,
 * and an empty chain is precisely what the guard exists to catch. Mocking the database
 * would test the code around the bug.
 *
 * The archive is written by DuckDB in the same shape the pipeline writes — dimension tables
 * in the catalog, facts in `component=X/year=Y/part.parquet` — so the read path under test
 * is the production one.
 */

import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { DuckDBInstance } from "@duckdb/node-api";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { findProvenanceViolations } from "@gaia/core";

let directory: string;

const CELL = "88264d6c01fffff";
const EMPTY_CELL = "88264d6c03fffff";

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

  const components = ["eii", "a_structure", "b_water", "c_riparian", "d_moisture", "e_drought"];
  for (const component of components) {
    await conn.run(`
      INSERT INTO method VALUES ('m_${component}', 'method for ${component}', 'formula',
        'A citation (2024).', NULL, 'a note about what this cannot establish', '1.0');
      INSERT INTO source VALUES ('s_${component}', 'dataset ${component}', 'v1',
        'https-open', 'https://example.invalid/${component}', 9000.0, 'annual',
        'Source citation (2024).', 'CC-BY-4.0', TIMESTAMPTZ '2026-08-11 00:00:00+00');
      INSERT INTO source_set VALUES ('set_${component}', TIMESTAMPTZ '2026-08-11 00:00:00+00');
      INSERT INTO source_set_member VALUES ('set_${component}', 's_${component}');
      INSERT INTO run VALUES ('run_${component}', 'build', '${component}', 'm_${component}',
        'set_${component}', '{}', TIMESTAMPTZ '2026-08-11 00:00:00+00',
        TIMESTAMPTZ '2026-08-11 00:01:00+00', 'succeeded', NULL, '0.2.0', '1');
      INSERT INTO partition_index VALUES ('${component}', 2023, 'path', 2, 'run_${component}',
        TIMESTAMPTZ '2026-08-11 00:01:00+00');
    `);

    // One measured cell and one the pipeline reached but could not measure. The second is
    // the important one: null must survive to the surface as null.
    const value = component === "eii" ? "1.25" : "0.9";
    const flags = component === "eii" ? "'water_balance_clamp'" : "''";
    const directory = join(root, `component=${component}`, "year=2023");
    mkdirSync(directory, { recursive: true });
    await conn.run(`
      COPY (
        SELECT * FROM (VALUES
          ('${CELL}', DATE '2023-01-01', DATE '2023-08-14', '${component}', ${value},
           'standard_error', 0.4, 1.0, 'm_${component}', 'run_${component}',
           'set_${component}', ${flags}),
          ('${EMPTY_CELL}', DATE '2023-01-01', DATE '2023-08-14', '${component}', NULL,
           'standard_error', NULL, 0.0, 'm_${component}', 'run_${component}',
           'set_${component}', '')
        ) AS t(h3, period_start, period_end, component, value, uncertainty_type,
               uncertainty_value, valid_fraction, method_id, run_id, source_set_id,
               constraint_flags)
      ) TO '${join(directory, "part.parquet").replace(/'/g, "''")}' (FORMAT PARQUET);
    `);
  }

  await conn.run(`
    INSERT INTO h3_cell VALUES ('${CELL}', 8, '87264d6cfffffff', 49.9, -119.5, 0.74);
    INSERT INTO h3_cell VALUES ('${EMPTY_CELL}', 8, '87264d6cfffffff', 49.91, -119.51, 0.74);
  `);

  conn.closeSync();
  instance.closeSync();
}

beforeAll(async () => {
  directory = mkdtempSync(join(tmpdir(), "gaia-eii-"));
  process.env["GAIA_EII_DIR"] = directory;
  process.env["GAIA_EII_AUDIT_PATH"] = join(directory, "audit.duckdb");
  await buildFixtureArchive(directory);

  writeFileSync(
    resolve(directory, "validation.json"),
    JSON.stringify({
      verdict: "PASS",
      gate_statement: "Component A, added to baseline_3, produces a positive delta AUC-PR...",
      gate_delta: { point: 0.141, low: 0.109, high: 0.177, excludes_zero: true },
      attribution_delta: { point: 0.158, low: 0.129, high: 0.189, excludes_zero: true },
      calibration_delta: { point: 0.009, low: 0.006, high: 0.013, excludes_zero: true },
      models: { candidate_with_component_a: { auc_pr_overall: 0.3428 } },
    }),
  );
}, 60_000);

afterAll(async () => {
  const { closeArchive } = await import("./eii-db.js");
  const { closeAudit } = await import("./eii-audit.js");
  await closeArchive();
  await closeAudit();
  rmSync(directory, { recursive: true, force: true });
});

describe("get_eii", () => {
  it("returns the composite with its chain and its doubt", async () => {
    const { getEii } = await import("./eii.js");
    const answer = await getEii({ h3: CELL });

    expect(answer.index.value).toBeCloseTo(1.25, 6);
    expect(answer.index.uncertainty_value).toBeCloseTo(0.4, 6);
    expect(answer.index.uncertainty_type).toBe("standard_error");
    expect(answer.index.provenance.length).toBeGreaterThan(0);
    expect(answer.index.provenance[0]?.citation).toContain("2024");
  });

  it("says which way the scale runs, on every response", async () => {
    const { getEii, ORIENTATION } = await import("./eii.js");
    const answer = await getEii({ h3: CELL });

    expect(answer.orientation).toBe(ORIENTATION);
    expect(answer.orientation.toLowerCase()).toContain("higher is worse");
  });

  it("states what the number cannot support", async () => {
    const { getEii } = await import("./eii.js");
    const answer = await getEii({ h3: CELL });

    expect(answer.index.method_justification).toContain("cannot support");
    expect(answer.index.method_justification).toContain("0.74");
  });

  it("passes the provenance guard", async () => {
    const { getEii } = await import("./eii.js");
    const answer = await getEii({ h3: CELL });

    expect(findProvenanceViolations(answer)).toEqual([]);
  });

  it("serves an unmeasured cell as null rather than zero", async () => {
    const { getEii } = await import("./eii.js");
    const answer = await getEii({ h3: EMPTY_CELL });

    expect(answer.index.value).toBeNull();
    expect(answer.index.validation_status).toBe("unmeasured");
    expect(answer.index.confidence).toBe(0);
  });

  it("surfaces a constraint flag rather than hiding the clamp", async () => {
    const { getEii } = await import("./eii.js");
    const answer = await getEii({ h3: CELL });

    expect(answer.index.constraint_flags).toContain("water_balance_clamp");
    expect(answer.index.validation_status).toBe("flagged");
  });

  it("refuses a cell the archive does not hold", async () => {
    const { getEii } = await import("./eii.js");
    await expect(getEii({ h3: "88264d6c05fffff" })).rejects.toThrow(/no eii for cell/i);
  });
});

describe("get_component", () => {
  it("returns one component with its own method", async () => {
    const { getComponent } = await import("./eii.js");
    const answer = await getComponent({ h3: CELL, component: "b_water" });

    expect(answer.component).toBe("b_water");
    expect(answer.measurement.method.method_id).toBe("m_b_water");
  });

  it("refuses a component that does not exist", async () => {
    const { getComponent } = await import("./eii.js");
    await expect(getComponent({ h3: CELL, component: "f_vibes" })).rejects.toThrow(/one of/);
  });

  it("warns that the reanalysis components are interpolated", async () => {
    const { getComponent } = await import("./eii.js");
    const water = await getComponent({ h3: CELL, component: "b_water" });
    const structure = await getComponent({ h3: CELL, component: "a_structure" });

    expect(water.measurement.method_justification).toContain("interpolation");
    expect(structure.measurement.method_justification).not.toContain("interpolation");
  });
});

describe("explain_score", () => {
  it("decomposes the index over the components present", async () => {
    const { explainScore } = await import("./eii.js");
    const answer = await explainScore({ h3: CELL });

    expect(answer.contributions).toHaveLength(5);
    const total = answer.contributions.reduce((sum, c) => sum + (c.share_of_index ?? 0), 0);
    expect(total).toBeCloseTo(1, 6);
  });

  it("separates a component never built from one that could not measure this cell", async () => {
    const { explainScore } = await import("./eii.js");
    const answer = await explainScore({ h3: CELL });

    expect(answer.components_not_built).toEqual([]);
  });

  it("names components it could not measure rather than scoring them zero", async () => {
    const { explainScore } = await import("./eii.js");
    const answer = await explainScore({ h3: EMPTY_CELL });

    expect(answer.missing_components).toHaveLength(5);
    expect(answer.contributions).toHaveLength(0);
    expect(answer.reading).toContain("No component");
  });
});

describe("compare_baseline", () => {
  it("reports the gate in its own words", async () => {
    const { compareBaseline } = await import("./eii.js");
    const answer = await compareBaseline();

    expect(answer.verdict).toBe("PASS");
    expect(answer.gate_statement).toContain("baseline_3");
  });

  it("says that only Component A has been through the gate", async () => {
    const { compareBaseline } = await import("./eii.js");
    const answer = await compareBaseline();

    expect(answer.caveat).toContain("Only Component A");
  });

  it("describes the archive it actually has, not the one the spec imagined", async () => {
    const { compareBaseline } = await import("./eii.js");
    const answer = await compareBaseline();

    expect(answer.caveat).toContain("built and served");
    expect(answer.caveat).not.toContain("not built in this archive");
  });
});

describe("portfolio_scan", () => {
  it("names the cells it could not measure", async () => {
    const { portfolioScan } = await import("./eii.js");
    const answer = await portfolioScan({ cells: [CELL, EMPTY_CELL] });

    expect(answer.requested).toBe(2);
    expect(answer.scored).toBe(1);
    expect(answer.unmeasured).toEqual([EMPTY_CELL]);
  });

  it("means over the scored cells and says so", async () => {
    const { portfolioScan } = await import("./eii.js");
    const answer = await portfolioScan({ cells: [CELL, EMPTY_CELL] });

    expect(answer.mean_index).toBeCloseTo(1.25, 6);
    expect(answer.method_justification).toContain("listed rather than averaged away");
  });

  it("counts cells above a stated threshold", async () => {
    const { portfolioScan } = await import("./eii.js");
    const answer = await portfolioScan({ cells: [CELL, EMPTY_CELL], threshold: 2.0 });

    expect(answer.above_threshold.threshold).toBe(2.0);
    expect(answer.above_threshold.count).toBe(0);
  });

  it("refuses an empty portfolio", async () => {
    const { portfolioScan } = await import("./eii.js");
    await expect(portfolioScan({ cells: [] })).rejects.toThrow(/no cells/);
  });
});

describe("resources", () => {
  it("serves the schema with the orientation in it", async () => {
    const { readResource } = await import("./eii.js");
    const resource = await readResource("eii://schema");
    const payload = JSON.parse(resource.text) as Record<string, unknown>;

    expect(String(payload["orientation"]).toLowerCase()).toContain("higher is worse");
    expect(String(payload["missing_values"])).toContain("never means zero");
  });

  it("serves the catalog from the partition index", async () => {
    const { readResource } = await import("./eii.js");
    const payload = JSON.parse((await readResource("eii://catalog")).text) as {
      partitions: unknown[];
      cells: number;
    };

    expect(payload.partitions.length).toBe(6);
    expect(payload.cells).toBe(2);
  });

  it("serves every method record in the methodology", async () => {
    const { readResource } = await import("./eii.js");
    const payload = JSON.parse((await readResource("eii://methodology")).text) as {
      methods: unknown[];
      weighting: string;
    };

    expect(payload.methods.length).toBe(6);
    expect(payload.weighting).toContain("Equal weights are an admission");
  });

  it("serves the validation verdict", async () => {
    const { readResource } = await import("./eii.js");
    const payload = JSON.parse((await readResource("eii://validation")).text) as {
      available: boolean;
      verdict: string;
    };

    expect(payload.available).toBe(true);
    expect(payload.verdict).toBe("PASS");
  });

  it("refuses a resource that does not exist", async () => {
    const { readResource } = await import("./eii.js");
    await expect(readResource("eii://vibes")).rejects.toThrow(/no resource/);
  });
});

describe("the audit log", () => {
  it("records every call, append only", async () => {
    const { getEii } = await import("./eii.js");
    const { readAudit } = await import("./eii-audit.js");

    const before = await readAudit(1000);
    await getEii({ h3: CELL });
    const after = await readAudit(1000);

    expect(after.length).toBe(before.length + 1);
    expect(after[0]?.tool).toBe("get_eii");
  });

  it("returns a digest the caller can recompute", async () => {
    const { getEii } = await import("./eii.js");
    const { responseDigest } = await import("./eii-audit.js");

    const answer = await getEii({ h3: CELL });
    const { audit, ...payload } = answer;

    expect(audit.response_digest).toBe(responseDigest(payload));
  });

  it("counts the rows a scan returned", async () => {
    const { portfolioScan } = await import("./eii.js");
    const answer = await portfolioScan({ cells: [CELL, EMPTY_CELL] });

    expect(answer.audit.rows_returned).toBe(1);
  });
});
