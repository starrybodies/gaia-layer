// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const IndicatorCoverageSchema = z
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
  .strict();
export type IndicatorCoverage = z.infer<typeof IndicatorCoverageSchema>;
