/**
 * The five agent-facing capabilities.
 *
 * Implemented against the data lake in milestone 4. Until then these exist so the
 * transports have something to bind to and `make dev` boots.
 */

import { ServiceError } from "./errors.js";

const NOT_YET = (name: string): never => {
  throw new ServiceError("internal", `${name} is not implemented yet.`);
};

export const TOOL_NAMES = [
  "get_ecological_state",
  "get_wildfire_substrate_score",
  "get_provenance",
  "compare_periods",
  "list_coverage",
] as const;

export type ToolName = (typeof TOOL_NAMES)[number];

export async function listCoverage(): Promise<never> {
  return NOT_YET("list_coverage");
}
