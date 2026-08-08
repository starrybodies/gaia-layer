// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const SubstrateScoreRequestSchema = z
  .object({
    date: z.string().date().describe("Date to score. Resolves to the month containing it."),
    geometry: z.union([
      z
        .object({
          coordinates: z.array(z.array(z.array(z.number()).min(2).max(3)).min(4)).min(1),
          type: z.literal("Polygon").default("Polygon"),
        })
        .strict()
        .describe("GeoJSON Polygon, WGS84 lon/lat, per RFC 7946."),
      z
        .object({
          coordinates: z.array(z.array(z.array(z.array(z.number()).min(2).max(3)).min(4))).min(1),
          type: z.literal("MultiPolygon").default("MultiPolygon"),
        })
        .strict(),
      z
        .object({
          east: z.number().gte(-180).lte(180),
          north: z.number().gte(-90).lte(90),
          south: z.number().gte(-90).lte(90),
          west: z.number().gte(-180).lte(180),
        })
        .strict()
        .describe("Axis-aligned bounding box in WGS84 degrees."),
    ]),
  })
  .strict();
export type SubstrateScoreRequest = z.infer<typeof SubstrateScoreRequestSchema>;
