// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const RejectedValueSchema = z
  .object({
    claim_id: z.string().regex(new RegExp("^clm_[0-9A-HJKMNP-TV-Z]{26}$")),
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
      .min(1),
    generated_at: z.string().datetime({ offset: true }),
    geometry_hash: z.string().min(8),
    indicator: z
      .union([
        z
          .enum([
            "ndvi",
            "ndmi",
            "nbr",
            "vpd_kpa",
            "precip_30d_mm",
            "temp_max_c",
            "days_since_rain",
            "soil_moisture_0_7cm",
            "soil_moisture_7_28cm",
            "elevation_m",
            "slope_deg",
            "aspect_deg",
            "twi",
          ])
          .describe(
            "Every quantity the layer can serve.\n\nGrouped by family; the family is derivable via :func:`indicator_family`.",
          ),
        z.null(),
      ])
      .default(null),
    period: z
      .object({ end: z.string().date(), start: z.string().date() })
      .strict()
      .describe("Inclusive date range. The layer works in whole days; sub-daily is out of scope."),
    provenance: z
      .array(
        z
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
            description: z
              .string()
              .min(1)
              .describe("Plain-language account of what this step did."),
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
          ),
      )
      .min(1)
      .describe(
        "Ordered chain from source observation to validated output. Never empty — a value without provenance cannot be constructed.",
      ),
    reason: z.string().min(1),
    validation_status: z.literal("rejected").default("rejected"),
  })
  .strict()
  .describe(
    "A value the constraint engine refused.\n\nDeliberately has no ``value`` field. A rejected measurement is reported as an absence\nwith a reason, never as a number the caller might use by accident.",
  );
export type RejectedValue = z.infer<typeof RejectedValueSchema>;
