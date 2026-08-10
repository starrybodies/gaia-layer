// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const SubstrateComponentSchema = z
  .object({
    contribution: z
      .number()
      .gte(0)
      .lte(100)
      .describe("normalized x weight x 100, the points this adds."),
    indicator: z
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
        "heat_load",
        "land_cover",
      ])
      .describe(
        "Every quantity the layer can serve.\n\nGrouped by family; the family is derivable via :func:`indicator_family`.",
      ),
    normalization: z
      .string()
      .min(1)
      .describe("The rescaling applied, stated so it can be reversed."),
    normalized: z
      .number()
      .gte(0)
      .lte(1)
      .describe("Value rescaled so 1.0 is the most fire-prone substrate condition."),
    rationale: z
      .string()
      .min(1)
      .describe("Why this indicator belongs in a wildfire substrate score."),
    raw: z
      .object({
        claim_id: z
          .string()
          .regex(new RegExp("^clm_[0-9A-HJKMNP-TV-Z]{26}$"))
          .describe("Identifier for this exact claim. Pass to get_provenance to trace it."),
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
        generated_at: z.string().datetime({ offset: true }),
        geometry_hash: z
          .string()
          .min(8)
          .describe("Stable hash of the geometry this value describes."),
        indicator: z
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
            "heat_load",
            "land_cover",
          ])
          .describe(
            "Every quantity the layer can serve.\n\nGrouped by family; the family is derivable via :func:`indicator_family`.",
          ),
        kind: z.literal("numeric").default("numeric"),
        method: z
          .object({
            citation: z.string().min(1).describe("Full bibliographic citation for the method."),
            doi: z.union([z.string(), z.null()]).default(null),
            formula: z.union([z.string(), z.null()]).default(null),
            name: z.string().min(1),
            notes: z.union([z.string(), z.null()]).default(null),
            url: z.union([z.string(), z.null()]).default(null),
          })
          .strict()
          .describe(
            "The published method a value was computed by, so a consumer can check the maths.",
          ),
        period: z
          .object({ end: z.string().date(), start: z.string().date() })
          .strict()
          .describe("Period the value describes."),
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
        spatial_stats: z
          .union([
            z
              .object({
                maximum: z.number(),
                mean: z.number(),
                median: z.number(),
                minimum: z.number(),
                p10: z.number(),
                p90: z.number(),
                std: z.number().gte(0),
                total_pixels: z.number().int().gt(0),
                valid_pixels: z.number().int().gte(0),
              })
              .strict()
              .describe(
                "Distribution of the indicator across the geometry.\n\nThe envelope's scalar ``value`` is an aggregate over an area. Serving the aggregate\nwithout its spread would hide the case where half a parcel is saturated and half is\ntinder-dry, which is exactly the case an underwriter needs to see.",
              ),
            z.null(),
          ])
          .default(null),
        unit: z.string().min(1).describe("Unit of the value. 'index' for unitless ratios."),
        validation_status: z
          .enum(["validated", "flagged"])
          .describe(
            "The subset of :class:`ValidationStatus` a served value is allowed to carry.\n\n``rejected`` is deliberately absent. A rejected value is never served as an answer,\nand this enum is what makes that a type error rather than a convention.",
          ),
        value: z.number(),
      })
      .strict()
      .describe("The underlying measured value, envelope intact."),
    weight: z.number().gt(0).lte(1),
  })
  .strict()
  .describe(
    "One indicator's contribution to the wildfire substrate score.\n\nEvery field is present so the score can be reconstructed by hand from its parts. A\nscore a land manager cannot decompose is a score they cannot act on.",
  );
export type SubstrateComponent = z.infer<typeof SubstrateComponentSchema>;
