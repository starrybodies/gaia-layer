// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const ErrorResponseSchema = z
  .object({
    detail: z.union([z.string(), z.null()]).default(null),
    error: z.string().min(1).describe("Stable machine-readable code."),
    generated_at: z.string().datetime({ offset: true }),
    message: z.string().min(1),
    retryable: z.boolean().default(false),
  })
  .strict()
  .describe("The only other shape a tool may return. Errors are structured, never prose blobs.");
export type ErrorResponse = z.infer<typeof ErrorResponseSchema>;
