// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const CoverageResponseSchema = z
  .object({
    algorithm_version: z.string(),
    aois: z.array(
      z
        .object({
          analysis_crs: z.string(),
          aoi_id: z.string(),
          area_km2: z.number().gt(0),
          bbox: z
            .object({
              east: z.number().gte(-180).lte(180),
              north: z.number().gte(-90).lte(90),
              south: z.number().gte(-90).lte(90),
              west: z.number().gte(-180).lte(180),
            })
            .strict()
            .describe("Axis-aligned bounding box in WGS84 degrees."),
          grid_resolution_m: z.number().gt(0),
          indicators: z.array(
            z
              .object({
                family: z.enum(["spectral", "climate", "soil", "terrain", "land_cover"]),
                first_period_start: z.string().date(),
                flagged_count: z.number().int().gte(0),
                indicator: z
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
                    "heat_load",
                    "land_cover",
                  ])
                  .describe(
                    "Every quantity the layer can serve.\n\nGrouped by family; the family is derivable via :func:`indicator_family`.",
                  ),
                last_period_end: z.string().date(),
                mean_confidence: z.number().gte(0).lte(1),
                period_count: z.number().int().gte(0),
                rejected_count: z.number().int().gte(0),
                source: z.string(),
                unit: z.string(),
                validated_count: z.number().int().gte(0),
              })
              .strict(),
          ),
          last_ingested_at: z
            .union([z.string().datetime({ offset: true }), z.null()])
            .default(null),
          name: z.string(),
        })
        .strict(),
    ),
    generated_at: z.string().datetime({ offset: true }),
    pipeline_version: z.string(),
  })
  .strict();
export type CoverageResponse = z.infer<typeof CoverageResponseSchema>;
