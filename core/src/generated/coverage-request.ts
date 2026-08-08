// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const CoverageRequestSchema = z
  .object({ aoi_id: z.union([z.string(), z.null()]).default(null) })
  .strict();
export type CoverageRequest = z.infer<typeof CoverageRequestSchema>;
