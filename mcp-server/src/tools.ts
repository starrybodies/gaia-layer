/**
 * MCP tool definitions and dispatch.
 *
 * The descriptions here are the only documentation an agent gets, so they state what each
 * tool measures, what it deliberately does not, and that every number comes back with a
 * citable provenance chain.
 */

import {
  ServiceError,
  comparePeriods,
  getEcologicalState,
  getProvenance,
  getWildfireSubstrateScore,
  listCoverage,
  type DateRange,
  type GeometryInput,
} from "@gaia/service";

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

function requireGeometry(args: Record<string, unknown>): GeometryInput {
  const geometry = args["geometry"];
  if (geometry === undefined || geometry === null || typeof geometry !== "object") {
    throw new ServiceError("invalid_request", "geometry is required.");
  }
  return geometry as GeometryInput;
}

function requireRange(args: Record<string, unknown>, key: string): DateRange {
  const range = args[key];
  if (range === null || typeof range !== "object") {
    throw new ServiceError("invalid_request", `${key} is required.`);
  }
  const { start, end } = range as { start?: unknown; end?: unknown };
  if (typeof start !== "string" || typeof end !== "string") {
    throw new ServiceError("invalid_request", `${key} needs string start and end dates.`);
  }
  return { start, end };
}

function optionalIndicators(args: Record<string, unknown>): string[] | undefined {
  const value = args["indicators"];
  if (!Array.isArray(value)) return undefined;
  return value.filter((v): v is string => typeof v === "string");
}

export async function callTool(name: string, args: Record<string, unknown>): Promise<unknown> {
  switch (name) {
    case "get_ecological_state":
      return getEcologicalState(
        requireGeometry(args),
        requireRange(args, "date_range"),
        optionalIndicators(args),
      );

    case "get_wildfire_substrate_score": {
      const date = args["date"];
      if (typeof date !== "string") {
        throw new ServiceError("invalid_request", "date is required, as YYYY-MM-DD.");
      }
      return getWildfireSubstrateScore(requireGeometry(args), date);
    }

    case "get_provenance": {
      const claimId = args["claim_id"];
      if (typeof claimId !== "string") {
        throw new ServiceError("invalid_request", "claim_id is required.");
      }
      return getProvenance(claimId);
    }

    case "compare_periods":
      return comparePeriods(
        requireGeometry(args),
        requireRange(args, "period_a"),
        requireRange(args, "period_b"),
        optionalIndicators(args),
      );

    case "list_coverage": {
      const aoiId = args["aoi_id"];
      return listCoverage(typeof aoiId === "string" ? aoiId : undefined);
    }

    default:
      throw new ServiceError("invalid_request", `Unknown tool: ${name}`);
  }
}
