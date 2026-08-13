/**
 * MCP definitions for the EII surface.
 *
 * The descriptions are the only documentation an agent gets before it calls something, so
 * they carry the two things this layer most needs a caller to know before it reads a number:
 * which way the scale runs, and what a 0.74 km2 landscape measurement cannot be used for.
 * Both are repeated in every response as well, because an agent that loaded the tool list an
 * hour ago is not necessarily an agent that still has it in context.
 */

import {
  COMPONENTS,
  ServiceError,
  callEiiTool,
  type PortfolioRequest,
} from "@gaia/service";

const H3_SCHEMA = {
  type: "string",
  description:
    "H3 resolution-8 cell id, about 0.74 km2. Use h3.latLngToCell(lat, lng, 8) to get one " +
    "for a coordinate.",
} as const;

const YEAR_SCHEMA = {
  type: "integer",
  description:
    "Fire year to read. Omit for whichever years the archive holds; the response always " +
    "states the as-of date it answered for.",
} as const;

const SCALE_WARNING =
  "Higher is worse: this is a departure scale where positive means the direction " +
  "associated with more severe fire, not a health score. It describes landscape condition " +
  "over a 0.74 km2 cell and cannot support a parcel-level or building-level claim.";

export const EII_TOOL_DEFINITIONS = [
  {
    name: "get_eii",
    description:
      "The composite Ecosystem Integrity Index for one H3 cell, with its uncertainty, its " +
      "full provenance chain, and a statement of what it cannot establish. " +
      SCALE_WARNING,
    inputSchema: {
      type: "object",
      properties: { h3: H3_SCHEMA, year: YEAR_SCHEMA },
      required: ["h3"],
    },
  },
  {
    name: "get_component",
    description:
      "One component of the index for one cell: vegetation structure (a_structure), water " +
      "balance (b_water), riparian condition (c_riparian), fuel moisture (d_moisture), or " +
      "drought (e_drought). Only a_structure has been through a validation gate; the " +
      "others are built and served but unvalidated. " +
      SCALE_WARNING,
    inputSchema: {
      type: "object",
      properties: {
        h3: H3_SCHEMA,
        component: { type: "string", enum: [...COMPONENTS] },
        year: YEAR_SCHEMA,
      },
      required: ["h3", "component"],
    },
  },
  {
    name: "explain_score",
    description:
      "Why a cell scored what it scored: every component's value, its share of the index, " +
      "and which components were missing. Read from stored contributions rather than a " +
      "live model, so the explanation matches the one served last week.",
    inputSchema: {
      type: "object",
      properties: { h3: H3_SCHEMA, year: YEAR_SCHEMA },
      required: ["h3"],
    },
  },
  {
    name: "compare_baseline",
    description:
      "What the index adds over fire weather and a static fuel map, in the words of a gate " +
      "written before the first model was fitted. Returns the verdict, the bootstrap " +
      "intervals and every model's metrics, and would report a failure as plainly as a pass.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "portfolio_scan",
    description:
      "The index across a set of cells, for a book of exposures. Names the cells it could " +
      "not measure rather than averaging over the ones it could, because a mean that " +
      "improves as coverage falls is not a portfolio statistic. " +
      SCALE_WARNING,
    inputSchema: {
      type: "object",
      properties: {
        cells: { type: "array", items: H3_SCHEMA, description: "H3 resolution-8 cell ids." },
        year: YEAR_SCHEMA,
        threshold: {
          type: "number",
          description:
            "Index value at or above which a cell is counted as elevated. Defaults to 1.0, " +
            "which is one standard deviation above the reference on the departure scale.",
        },
      },
      required: ["cells"],
    },
  },
  {
    name: "portfolio_ranking",
    description:
      "Rank the cells of a book against each other and roll the ranking up to resolution-7 " +
      "parents. Rank 1 is the cell furthest in the direction associated with more severe " +
      "fire. Cells the archive cannot score carry a null rank and are listed by id rather " +
      "than ranked last, because an unmeasured cell is not a good cell. " +
      SCALE_WARNING,
    inputSchema: {
      type: "object",
      properties: {
        cells: {
          type: "array",
          description:
            "The book. Each line is a cell id and, optionally, the caller's own exposure " +
            "measure. Cell identifiers only: this surface has no field for an address, a " +
            "coordinate or a policy number and will refuse anything that is not an H3 id.",
          items: {
            type: "object",
            properties: {
              h3: H3_SCHEMA,
              weight: {
                type: "number",
                description:
                  "The caller's own exposure measure — insured value, replacement cost, " +
                  "count of risks. Used only to weight a mean, and not stored.",
              },
            },
            required: ["h3"],
          },
        },
        year: YEAR_SCHEMA,
        component: {
          type: "string",
          enum: [...COMPONENTS],
          description: "Rank on one component instead of the composite.",
        },
      },
      required: ["cells"],
    },
  },
  {
    name: "portfolio_change",
    description:
      "What moved in a book between two as-of periods. A cell scored in one period and not " +
      "the other is reported as not comparable and contributes nothing to the mean, " +
      "because a change computed against a missing value is a change invented by the " +
      "arithmetic. Both run ids travel with every cell so a change spanning a method " +
      "change can be seen rather than assumed away. " +
      SCALE_WARNING,
    inputSchema: {
      type: "object",
      properties: {
        cells: {
          type: "array",
          description: "The book, in the same shape portfolio_ranking takes.",
          items: {
            type: "object",
            properties: { h3: H3_SCHEMA, weight: { type: "number" } },
            required: ["h3"],
          },
        },
        before: { type: "integer", description: "The earlier fire year." },
        after: { type: "integer", description: "The later fire year." },
        component: { type: "string", enum: [...COMPONENTS] },
      },
      required: ["cells", "before", "after"],
    },
  },
  {
    name: "read_dossier",
    description:
      "The validation dossier: the gate verdict and its bootstrap intervals, what carries " +
      "the lift, where the index fails, how much of the claim is evidenced, and the " +
      "findings that weaken it. Computed in the pipeline and persisted with the run that " +
      "produced it, so what an agent reads here is what a human reads on the diligence " +
      "page. The sections that weaken the claim come first and are flagged: an agent " +
      "summarising this must not drop them.",
    inputSchema: { type: "object", properties: {} },
  },
] as const;

function requireCell(args: Record<string, unknown>): string {
  const h3 = args["h3"];
  if (typeof h3 !== "string" || h3.length === 0) {
    throw new ServiceError("invalid_request", "h3 is required: a resolution-8 cell id.");
  }
  return h3;
}

function optionalYear(args: Record<string, unknown>): number | undefined {
  const year = args["year"];
  return typeof year === "number" ? year : undefined;
}

export async function callEii(name: string, args: Record<string, unknown>): Promise<unknown> {
  switch (name) {
    case "get_eii":
      return callEiiTool(name, { h3: requireCell(args), year: optionalYear(args) });

    case "get_component": {
      const component = args["component"];
      if (typeof component !== "string") {
        throw new ServiceError(
          "invalid_request",
          `component is required, one of ${COMPONENTS.join(", ")}.`,
        );
      }
      return callEiiTool(name, { h3: requireCell(args), component, year: optionalYear(args) });
    }

    case "explain_score":
      return callEiiTool(name, { h3: requireCell(args), year: optionalYear(args) });

    case "compare_baseline":
      return callEiiTool(name, {});

    case "portfolio_ranking":
    case "portfolio_change": {
      const cells = args["cells"];
      if (!Array.isArray(cells)) {
        throw new ServiceError(
          "invalid_request",
          "cells is required: an array of { h3, weight? }. Cell identifiers only — never " +
            "an address or a coordinate.",
        );
      }
      return callEiiTool(name, args);
    }

    case "read_dossier":
      return callEiiTool(name, {});

    case "portfolio_scan": {
      const cells = args["cells"];
      if (!Array.isArray(cells) || cells.some((cell) => typeof cell !== "string")) {
        throw new ServiceError("invalid_request", "cells is required: an array of H3 cell ids.");
      }
      const request: PortfolioRequest = { cells: cells as string[], year: optionalYear(args) };
      const threshold = args["threshold"];
      if (typeof threshold === "number") request.threshold = threshold;
      return callEiiTool(name, request as unknown as Record<string, unknown>);
    }

    default:
      throw new ServiceError("invalid_request", `Unknown EII tool: ${name}`);
  }
}
