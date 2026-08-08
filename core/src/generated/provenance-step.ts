// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const ProvenanceStepSchema = z
  .object({
    access_route: z
      .union([z.string(), z.null()])
      .describe(
        "How the data was reached, e.g. 'earth-search-v1' or 'open-meteo-archive'. Distinguishes the dataset from the intermediary that served it.",
      )
      .default(null),
    acquired_at: z
      .union([z.string().datetime({ offset: true }), z.null()])
      .describe("When the underlying observation was made.")
      .default(null),
    algorithm_version: z.string(),
    asset_ids: z
      .array(z.string())
      .describe("Scene, granule or asset identifiers consumed by this step.")
      .optional(),
    dataset_id: z
      .union([z.string(), z.null()])
      .describe("Dataset identifier, e.g. 'sentinel-2-l2a'.")
      .default(null),
    description: z.string().min(1).describe("Plain-language account of what this step did."),
    index: z.number().int().gte(0).describe("Position in the chain, 0 first."),
    kind: z
      .enum(["observation", "processing", "validation"])
      .describe(
        "What a provenance step represents.\n\nA well-formed chain starts with at least one ``observation`` and ends with a\n``validation``. Everything between is ``processing``.",
      ),
    parameters: z
      .record(z.string(), z.any())
      .describe("Every parameter that affects the numeric output of this step.")
      .optional(),
    pipeline_version: z.string(),
    processed_at: z.string().datetime({ offset: true }).describe("When this step ran."),
    resolution_m: z.union([z.number().gt(0), z.null()]).default(null),
    software: z
      .union([z.string(), z.null()])
      .describe("Library and version doing the work, e.g. 'rasterio 1.4.3'.")
      .default(null),
    source: z
      .union([z.string(), z.null()])
      .describe("Originating organisation, e.g. 'ESA/Copernicus'.")
      .default(null),
    spatial_ref: z
      .string()
      .describe("CRS of this step's output, as an authority code, e.g. 'EPSG:32610'."),
  })
  .strict()
  .describe(
    "One link in the chain.\n\nThe six fields the build prompt calls non-negotiable for an ingested record — source,\ndataset id, acquisition time, processing time, pipeline version, spatial reference —\nare all here. They are optional only where the step kind makes them meaningless: a\nvalidation step has no acquisition timestamp of its own, it inherits the one carried by\nthe observation steps beneath it.",
  );
export type ProvenanceStep = z.infer<typeof ProvenanceStepSchema>;
