// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const EcologicalStateRequestSchema = z
  .object({
    date_range: z
      .object({ end: z.string().date(), start: z.string().date() })
      .strict()
      .describe("Inclusive date range. The layer works in whole days; sub-daily is out of scope."),
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
    indicators: z
      .union([
        z.array(
          z
            .enum([
              "ndvi",
              "ndmi",
              "nbr",
              "vpd_kpa",
              "precip_30d_mm",
              "temp_max_c",
              "days_since_rain",
              "soil_moisture_0_7cm",
              "soil_moisture_7_28cm",
              "elevation_m",
              "slope_deg",
              "aspect_deg",
              "twi",
            ])
            .describe(
              "Every quantity the layer can serve.\n\nGrouped by family; the family is derivable via :func:`indicator_family`.",
            ),
        ),
        z.null(),
      ])
      .describe("Restrict the response. Omit for everything available.")
      .default(null),
  })
  .strict();
export type EcologicalStateRequest = z.infer<typeof EcologicalStateRequestSchema>;
