/**
 * Values mirrored from the Python side that are not part of a serialized schema.
 *
 * Kept small on purpose. Anything that describes a data shape belongs in the Pydantic
 * schemas and arrives here generated; only genuinely cross-cutting constants live here.
 */

export const DEFAULT_API_PORT = 8811;
export const DEFAULT_CONSOLE_PORT = 3311;

/** Header the REST API reads its key from. */
export const API_KEY_HEADER = "x-gaia-key";

export const INDICATOR_LABELS: Record<string, string> = {
  ndvi: "Vegetation greenness (NDVI)",
  ndmi: "Vegetation moisture (NDMI)",
  nbr: "Burn ratio (NBR)",
  vpd_kpa: "Vapour pressure deficit",
  precip_30d_mm: "Precipitation, trailing 30 days",
  temp_max_c: "Maximum temperature",
  days_since_rain: "Days since measurable rain",
  soil_moisture_0_7cm: "Soil moisture, 0–7 cm",
  soil_moisture_7_28cm: "Soil moisture, 7–28 cm",
  elevation_m: "Elevation",
  slope_deg: "Slope",
  aspect_deg: "Aspect",
  twi: "Topographic wetness index",
};

/**
 * Which direction of an indicator means a drier, more fire-prone substrate. Used by the
 * console to colour trend arrows; the substrate score computes its own normalisation
 * server-side and does not read this.
 */
export const DRYING_DIRECTION: Record<string, "up" | "down"> = {
  ndvi: "down",
  ndmi: "down",
  nbr: "down",
  vpd_kpa: "up",
  precip_30d_mm: "down",
  temp_max_c: "up",
  days_since_rain: "up",
  soil_moisture_0_7cm: "down",
  soil_moisture_7_28cm: "down",
  twi: "down",
};
