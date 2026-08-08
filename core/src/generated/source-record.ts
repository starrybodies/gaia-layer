// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const SourceRecordSchema = z
  .object({
    access_route: z.union([z.string(), z.null()]).default(null),
    acquired_at: z.union([z.string().datetime({ offset: true }), z.null()]).default(null),
    asset_id: z.string(),
    dataset_id: z.string(),
    source: z.string(),
    spatial_ref: z.string(),
    url: z.union([z.string(), z.null()]).default(null),
  })
  .strict()
  .describe("A distinct source observation underlying a claim.");
export type SourceRecord = z.infer<typeof SourceRecordSchema>;
