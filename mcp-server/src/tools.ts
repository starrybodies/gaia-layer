/**
 * MCP tool definitions and dispatch.
 *
 * The descriptions here are the only documentation an agent gets, so they state what each
 * tool measures, what it deliberately does not, and that every number comes back with a
 * citable provenance chain.
 */

import { ServiceError, listCoverage } from "@gaia/service";

const GEOMETRY_SCHEMA = {
  description:
    "Area to describe. A GeoJSON Polygon or MultiPolygon in WGS84, or a bounding box " +
    "object with west/south/east/north. Must match an ingested area of interest.",
  oneOf: [
    {
      type: "object",
      properties: {
        type: { const: "Polygon" },
        coordinates: { type: "array" },
      },
      required: ["type", "coordinates"],
    },
    {
      type: "object",
      properties: {
        type: { const: "MultiPolygon" },
        coordinates: { type: "array" },
      },
      required: ["type", "coordinates"],
    },
    {
      type: "object",
      properties: {
        west: { type: "number" },
        south: { type: "number" },
        east: { type: "number" },
        north: { type: "number" },
      },
      required: ["west", "south", "east", "north"],
    },
  ],
} as const;

const DATE_RANGE_SCHEMA = {
  type: "object",
  properties: {
    start: { type: "string", format: "date" },
    end: { type: "string", format: "date" },
  },
  required: ["start", "end"],
  additionalProperties: false,
} as const;

export const TOOL_DEFINITIONS = [
  {
    name: "get_ecological_state",
    description:
      "Validated ecological state for an area over a date range: vegetation greenness and " +
      "moisture, burn ratio, climate and soil moisture, terrain, and the trend in each. " +
      "Every value carries a confidence score, a validation status, a provenance chain " +
      "back to source observations, and the citation for the method used to compute it. " +
      "Values that failed validation are reported as rejected rather than omitted.",
    inputSchema: {
      type: "object",
      properties: {
        geometry: GEOMETRY_SCHEMA,
        date_range: DATE_RANGE_SCHEMA,
        indicators: {
          type: "array",
          items: { type: "string" },
          description: "Optional subset of indicator ids. Omit for everything available.",
        },
      },
      required: ["geometry", "date_range"],
    },
  },
  {
    name: "get_wildfire_substrate_score",
    description:
      "Composite wildfire substrate condition for an area on a date, scored 0-100, with " +
      "the full decomposition into contributing indicators: each one's measured value, " +
      "how it was normalised, its weight, and the points it contributed. " +
      "This scores the condition of the ground, not the probability of ignition and not " +
      "fire weather on the day. It says how predisposed the substrate is, given a fire.",
    inputSchema: {
      type: "object",
      properties: {
        geometry: GEOMETRY_SCHEMA,
        date: {
          type: "string",
          format: "date",
          description: "Resolves to the month containing this date.",
        },
      },
      required: ["geometry", "date"],
    },
  },
  {
    name: "get_provenance",
    description:
      "Trace any number this layer has previously returned back to the satellite scenes, " +
      "reanalysis cells or elevation tiles it came from, through every processing and " +
      "validation step. Takes the claim_id attached to any served value. Use this to cite " +
      "a figure, or to check one before relying on it.",
    inputSchema: {
      type: "object",
      properties: {
        claim_id: { type: "string", description: "The claim_id from a served value." },
      },
      required: ["claim_id"],
    },
  },
  {
    name: "compare_periods",
    description:
      "Change detection between two periods for the same area. Returns both periods' " +
      "values with their envelopes, the delta, and whether the change is statistically " +
      "significant given the spread and the effective sample size. A difference that is " +
      "not significant is reported as not significant rather than as change.",
    inputSchema: {
      type: "object",
      properties: {
        geometry: GEOMETRY_SCHEMA,
        period_a: DATE_RANGE_SCHEMA,
        period_b: DATE_RANGE_SCHEMA,
        indicators: { type: "array", items: { type: "string" } },
      },
      required: ["geometry", "period_a", "period_b"],
    },
  },
  {
    name: "list_coverage",
    description:
      "What this layer can currently answer for: which areas are ingested, which " +
      "indicators exist for each, over what dates, and the data quality behind them — " +
      "mean confidence and the count of validated, flagged and rejected values. " +
      "Call this first if you are unsure whether an area is covered.",
    inputSchema: {
      type: "object",
      properties: {
        aoi_id: { type: "string", description: "Restrict to one area of interest." },
      },
    },
  },
] as const;

export async function callTool(name: string, args: Record<string, unknown>): Promise<unknown> {
  switch (name) {
    case "list_coverage":
      return listCoverage();
    case "get_ecological_state":
    case "get_wildfire_substrate_score":
    case "get_provenance":
    case "compare_periods":
      void args;
      throw new ServiceError("internal", `${name} is not implemented yet.`);
    default:
      throw new ServiceError("invalid_request", `Unknown tool: ${name}`);
  }
}
