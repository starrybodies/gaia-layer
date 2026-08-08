// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const ProvenanceRequestSchema = z
  .object({ claim_id: z.string().regex(new RegExp("^clm_[0-9A-HJKMNP-TV-Z]{26}$")) })
  .strict();
export type ProvenanceRequest = z.infer<typeof ProvenanceRequestSchema>;
