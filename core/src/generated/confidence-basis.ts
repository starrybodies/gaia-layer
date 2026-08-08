// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const ConfidenceBasisSchema = z
  .object({
    aggregation: z
      .string()
      .describe("How components combine into the score.")
      .default("weighted_arithmetic_mean"),
    cloud_fraction: z
      .union([z.number().gte(0).lte(1), z.null()])
      .describe("Mean cloud fraction across contributing scenes.")
      .default(null),
    components: z
      .array(
        z
          .object({
            description: z.string().min(1),
            name: z.string().min(1),
            value: z.number().gte(0).lte(1).describe("Component score, 1.0 being ideal."),
            weight: z.number().gt(0).lte(1),
          })
          .strict()
          .describe(
            "One named input to the confidence score, kept separate so the score decomposes.",
          ),
      )
      .min(1),
    observation_count: z
      .number()
      .int()
      .gte(0)
      .describe("Number of source observations composited into this value."),
    revisit_gap_days: z
      .union([z.number().gte(0), z.null()])
      .describe("Longest gap between contributing observations.")
      .default(null),
    spatial_coverage: z
      .number()
      .gte(0)
      .lte(1)
      .describe("Fraction of the geometry with a valid observation."),
  })
  .strict()
  .describe(
    "How the confidence score was arrived at.\n\nv0.1 keeps this deliberately simple, as the build prompt directs: composite pixel\ncount, cloud fraction, and sensor revisit gap. The structure admits more components\nlater without changing the envelope shape.",
  );
export type ConfidenceBasis = z.infer<typeof ConfidenceBasisSchema>;
