/**
 * Demo 2 — the walkthrough an agent would do, in the order it would do it.
 *
 *     pnpm demo:agent
 *
 * An agent meeting this layer for the first time does not start by asking for a number. It
 * starts by asking what the numbers mean, because the one thing it cannot recover from is
 * reading the scale backwards: "Ecosystem Integrity Index" sounds like a score where higher
 * is healthier, and it is the opposite. So the walkthrough reads `eii://schema` and
 * `eii://methodology` first, then asks what the index says, then asks why, then asks what
 * the whole thing is worth against the baseline it has to beat.
 *
 * Every step prints the part of the response that an agent should be acting on rather than
 * the whole payload, and the last step shows the audit entry, because a number an
 * underwriter cites has to be a number somebody can later prove was served.
 */

import {
  archiveExists,
  compareBaseline,
  explainScore,
  getComponent,
  getEii,
  portfolioScan,
  readResource,
} from "@gaia/service";

const RULE = "-".repeat(78);

function rule(title?: string): void {
  console.log(title === undefined ? `\n${RULE}` : `\n${RULE}\n${title}`);
}

/** A cell to demonstrate on, overridable so this runs against any built archive. */
const CELL = process.env["GAIA_DEMO_CELL"] ?? "8812d17987fffff";

async function anyCell(): Promise<string> {
  const { queryArchive } = await import("@gaia/service");
  const rows = await queryArchive(
    "SELECT h3 FROM eii.h3_cell ORDER BY h3 LIMIT 1",
  );
  return rows.length > 0 ? String(rows[0]?.["h3"]) : CELL;
}

async function main(): Promise<number> {
  if (!(await archiveExists())) {
    console.error(
      "No EII archive found. Build one first:\n" +
        "  uv run --project pipeline python -c \"" +
        "from gaia_pipeline.eii.run import build_spine, build_components, persist_components; " +
        "from datetime import date; s=build_spine(); " +
        "persist_components(s, build_components(s, as_of=date(2023,8,14)), as_of=date(2023,8,14))\"",
    );
    return 1;
  }

  rule("1. What do the numbers mean? — eii://schema");
  const schema = JSON.parse((await readResource("eii://schema")).text) as Record<string, unknown>;
  console.log(`  orientation   : ${String(schema["orientation"]).slice(0, 180)}...`);
  console.log(`  missing values: ${String(schema["missing_values"])}`);

  rule("2. How was it built, and what will it not support? — eii://methodology");
  const methodology = JSON.parse((await readResource("eii://methodology")).text) as {
    weighting: string;
    methods: { method_id: string; name: string }[];
  };
  console.log(`  weighting: ${methodology.weighting}`);
  for (const method of methodology.methods) {
    console.log(`  - ${method.method_id.padEnd(36)} ${method.name}`);
  }

  rule("3. What is actually in the archive? — eii://catalog");
  const catalog = JSON.parse((await readResource("eii://catalog")).text) as {
    cells: number;
    partitions: { component: string; year: number; rows: number }[];
  };
  console.log(`  ${catalog.cells.toLocaleString()} cells`);
  for (const partition of catalog.partitions) {
    console.log(
      `  - ${partition.component.padEnd(14)} ${partition.year}  ${partition.rows.toLocaleString()} rows`,
    );
  }

  const cell = await anyCell();

  rule(`4. What does the index say about ${cell}? — get_eii`);
  const index = await getEii({ h3: cell });
  console.log(`  value       : ${index.index.value ?? "not measured"}`);
  console.log(`  uncertainty : ${index.index.uncertainty_value ?? "—"} (${index.index.uncertainty_type})`);
  console.log(`  confidence  : ${index.index.confidence.toFixed(3)}`);
  console.log(`  status      : ${index.index.validation_status}`);
  console.log(`  sources     : ${index.index.provenance.map((step) => step.dataset).join(", ")}`);
  console.log(`  cannot say  : ${index.index.method_justification}`);

  rule("5. Why? — explain_score");
  const explanation = await explainScore({ h3: cell });
  console.log(`  ${explanation.reading}`);
  for (const contribution of explanation.contributions) {
    const share = contribution.share_of_index;
    console.log(
      `  - ${contribution.component.padEnd(14)} ${String(contribution.measurement.value ?? "—").padStart(8)}` +
        `   ${share === null ? "—" : `${(share * 100).toFixed(1)}% of the index`}`,
    );
  }
  if (explanation.missing_components.length > 0) {
    console.log(`  not measured here: ${explanation.missing_components.join(", ")}`);
  }

  rule("6. One component on its own — get_component");
  const first = explanation.contributions[0]?.component ?? "a_structure";
  const component = await getComponent({ h3: cell, component: first });
  console.log(`  ${first}: ${component.measurement.value ?? "not measured"}`);
  console.log(`  method: ${component.measurement.method.name}`);
  console.log(`  formula: ${component.measurement.method.formula ?? "—"}`);

  rule("7. Is any of this worth having? — compare_baseline");
  const comparison = await compareBaseline();
  console.log(`  gate    : ${comparison.gate_statement}`);
  console.log(`  verdict : ${comparison.verdict}`);
  console.log(`  delta   : ${JSON.stringify(comparison.gate_delta)}`);
  console.log(`  caveat  : ${comparison.caveat}`);

  rule("8. Across a book of exposures — portfolio_scan");
  const { queryArchive } = await import("@gaia/service");
  const sample = (await queryArchive("SELECT h3 FROM eii.h3_cell ORDER BY h3 LIMIT 50")).map(
    (row) => String(row["h3"]),
  );
  const scan = await portfolioScan({ cells: sample, threshold: 1.0 });
  console.log(`  requested ${scan.requested}, scored ${scan.scored}`);
  console.log(`  mean index: ${scan.mean_index?.toFixed(4) ?? "—"}`);
  console.log(
    `  above ${scan.above_threshold.threshold}: ${scan.above_threshold.count} cells ` +
      `(${(scan.above_threshold.share * 100).toFixed(1)}%)`,
  );
  console.log(`  unmeasured: ${scan.unmeasured.length}`);
  console.log(`  ${scan.method_justification}`);

  rule("9. Prove it was served — the audit entry");
  console.log(`  entry   : ${scan.audit.entry_id}`);
  console.log(`  tool    : ${scan.audit.tool}`);
  console.log(`  digest  : ${scan.audit.response_digest}`);
  console.log(`  called  : ${scan.audit.called}`);
  console.log("  The digest is over the response body without the audit block, so a caller");
  console.log("  holding the response can recompute it and prove the log matches.");

  rule();
  return 0;
}

const { closeArchive } = await import("@gaia/service");
const { closeAudit } = await import("@gaia/service");
const code = await main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : String(error));
  return 1;
});
await closeArchive();
await closeAudit();
process.exit(code);
