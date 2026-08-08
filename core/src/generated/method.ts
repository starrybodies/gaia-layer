// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const MethodSchema = z
  .object({
    citation: z.string().min(1).describe("Full bibliographic citation for the method."),
    doi: z.union([z.string(), z.null()]).default(null),
    formula: z.union([z.string(), z.null()]).default(null),
    name: z.string().min(1),
    notes: z.union([z.string(), z.null()]).default(null),
    url: z.union([z.string(), z.null()]).default(null),
  })
  .strict()
  .describe("The published method a value was computed by, so a consumer can check the maths.");
export type Method = z.infer<typeof MethodSchema>;
