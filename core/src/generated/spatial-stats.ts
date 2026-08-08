// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const SpatialStatsSchema = z
  .object({
    maximum: z.number(),
    mean: z.number(),
    median: z.number(),
    minimum: z.number(),
    p10: z.number(),
    p90: z.number(),
    std: z.number().gte(0),
    total_pixels: z.number().int().gt(0),
    valid_pixels: z.number().int().gte(0),
  })
  .strict()
  .describe(
    "Distribution of the indicator across the geometry.\n\nThe envelope's scalar ``value`` is an aggregate over an area. Serving the aggregate\nwithout its spread would hide the case where half a parcel is saturated and half is\ntinder-dry, which is exactly the case an underwriter needs to see.",
  );
export type SpatialStats = z.infer<typeof SpatialStatsSchema>;
