// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const ValidationFlagSchema = z
  .object({
    code: z.string().min(1).describe("Stable machine-readable flag code."),
    confidence_penalty: z
      .number()
      .gte(0)
      .lte(1)
      .describe("Multiplicative reduction this flag applied to the confidence score.")
      .default(0),
    constraint: z.string().min(1).describe("Identifier of the constraint that produced this flag."),
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
  );
export type ValidationFlag = z.infer<typeof ValidationFlagSchema>;
