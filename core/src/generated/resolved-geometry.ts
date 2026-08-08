// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with `make schema`.

import { z } from "zod";

export const ResolvedGeometrySchema = z
  .object({
    analysis_crs: z.string().describe("CRS the indicators were computed in."),
    aoi_id: z
      .union([z.string(), z.null()])
      .describe("Set when the geometry matched a configured AOI.")
      .default(null),
    area_km2: z.number().gt(0),
    bbox: z
      .object({
        east: z.number().gte(-180).lte(180),
        north: z.number().gte(-90).lte(90),
        south: z.number().gte(-90).lte(90),
        west: z.number().gte(-180).lte(180),
      })
      .strict()
      .describe("Axis-aligned bounding box in WGS84 degrees."),
    geometry_hash: z.string().min(8),
    grid_resolution_m: z.number().gt(0),
  })
  .strict()
  .describe("The geometry a response describes, after snapping to the analysis grid.");
export type ResolvedGeometry = z.infer<typeof ResolvedGeometrySchema>;
