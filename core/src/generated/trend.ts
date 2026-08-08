// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const TrendSchema = z
  .object({
    direction: z.enum(["increasing", "decreasing", "stable"]),
    first: z.number().describe("Fitted value at the start of the period."),
    last: z.number().describe("Fitted value at the end of the period."),
    n_observations: z.number().int().gte(0),
    p_value: z.number().gte(0).lte(1),
    r_squared: z.number().gte(0).lte(1),
    significant: z
      .boolean()
      .describe("True when p < 0.05 and at least 4 observations contributed."),
    slope_per_month: z.number().describe("Ordinary least squares slope, units per month."),
  })
  .strict()
  .describe(
    "Direction and strength of change in an indicator over a period.\n\n``significant`` is the field that matters. A slope without a significance test invites\nthe reader to see a trend in noise, which is the quantitative failure mode lesson 2\nexists to prevent.",
  );
export type Trend = z.infer<typeof TrendSchema>;
