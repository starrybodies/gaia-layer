/**
 * The two artifacts the console renders but never computes: the diligence dossier and the
 * demo book.
 *
 * Both are written by the pipeline, in Python, with a run behind them. This module reads
 * them and does nothing else — no aggregation, no formatting, no derived figures. That is
 * the whole point of it existing: if the console fetched these files itself, the temptation
 * to work out one more number on the way to the screen would be one import away.
 *
 * Absent is not an error worth dressing up. A checkout that has not run the pipeline has no
 * dossier, and the surface should say the pipeline has not run rather than fail in a way
 * that reads as a broken deployment.
 */

import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { archiveDir } from "./eii-db.js";
import { ServiceError } from "./errors.js";

export interface DossierFigure {
  label: string;
  value: number | null;
  display: string;
  note: string;
  interval: { low: number; high: number; excludes_zero: boolean; display: string } | null;
  source: string;
}

export interface DossierSection {
  id: string;
  title: string;
  statement: string;
  kind: "figures" | "table" | "list";
  disclosure: boolean;
  caveat: string;
  columns: string[];
  rows: (string | number)[][];
  figures: DossierFigure[];
}

export interface Dossier {
  generated: string;
  run_id: string;
  method_id: string;
  method_version: string;
  source_set_id: string;
  verdict: string | null;
  gate_statement: string | null;
  disclosure_count: number;
  sections: DossierSection[];
}

export interface DemoBookCell {
  h3: string;
  h3_parent: string;
  exposures: number;
  footprint_m2: number;
  synthetic_insured_value: number;
}

export interface DemoBook {
  synthetic: true;
  label: string;
  warning: string;
  privacy: string;
  generated: string;
  resolution: number;
  parent_resolution: number;
  seed: number;
  footprint_source: Record<string, string>;
  cells: DemoBookCell[];
  totals: { cells: number; exposures: number; synthetic_insured_value: number };
}

function readArtifact<T>(name: string, what: string): T {
  const path = resolve(archiveDir(), name);
  if (!existsSync(path)) {
    throw new ServiceError(
      "artifact_unavailable",
      `${what} has not been built in this deployment`,
      `expected ${name} in the archive directory; run the pipeline that writes it`,
    );
  }
  return JSON.parse(readFileSync(path, "utf8")) as T;
}

/** The precomputed diligence dossier, exactly as Python wrote it. */
export function readDossier(): Dossier {
  return readArtifact<Dossier>("dossier.json", "the diligence dossier");
}

/**
 * The synthetic demo book.
 *
 * Re-checked on the way out rather than trusted: this file is the one thing in the system
 * that looks like a client portfolio, and a deployment that served it without the synthetic
 * label would be serving something that could be screenshotted as a real one.
 */
export function readDemoBook(): DemoBook {
  const book = readArtifact<DemoBook>("demo-book.json", "the demo book");
  if (book.synthetic !== true || !book.label.includes("SYNTHETIC")) {
    throw new ServiceError(
      "internal",
      "the demo book on disk is not labelled synthetic and will not be served",
    );
  }
  return book;
}
