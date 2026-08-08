// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const ValidationReportSchema = z
  .object({
    confidence: z.number().gte(0).lte(1),
    confidence_basis: z
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
      ),
    constraints_checked: z.array(z.string()).min(1),
    flags: z
      .array(
        z
          .object({
            code: z.string().min(1).describe("Stable machine-readable flag code."),
            confidence_penalty: z
              .number()
              .gte(0)
              .lte(1)
              .describe("Multiplicative reduction this flag applied to the confidence score.")
              .default(0),
            constraint: z
              .string()
              .min(1)
              .describe("Identifier of the constraint that produced this flag."),
            expected: z
              .union([z.string(), z.null()])
              .describe("What the constraint required, in plain language.")
              .default(null),
            message: z.string().min(1).describe("Plain-language explanation."),
            observed: z
              .union([z.number(), z.null()])
              .describe("The value that tripped the check.")
              .default(null),
            severity: z.enum(["warn", "error"]),
          })
          .strict()
          .describe(
            "A constraint the value did not satisfy.\n\nFlags travel with the value rather than replacing it. A consumer that ignores flags\ngets a number; a consumer that reads them gets a number and the reason to discount it.",
          ),
      )
      .optional(),
    status: z.enum(["validated", "flagged", "rejected"]),
  })
  .strict()
  .describe("The constraint engine's verdict on one candidate value, before it becomes a claim.");
export type ValidationReport = z.infer<typeof ValidationReportSchema>;
