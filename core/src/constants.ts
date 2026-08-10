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
  heat_load: "Heat load index",
  land_cover: "Land cover class",
  dnbr: "Burn severity (dNBR)",
  ndvi_annual_min: "Greenness, annual minimum",
  ndmi_annual_min: "Canopy moisture, annual minimum",
  nbr_annual_min: "Burn ratio, annual minimum",
  ndvi_amplitude: "Greenness, seasonal amplitude",
  ndmi_amplitude: "Canopy moisture, seasonal amplitude",
  nbr_amplitude: "Burn ratio, seasonal amplitude",
};

/**
 * Which direction of an indicator means a drier, more fire-prone substrate. Read by the
 * console for trend arrows and for which end of the map ramp gets the warm colours, and by
 * the service when a landscape reading has to say which tail is the dry one.
 *
 * The substrate score is listed for those two presentational uses only. Its own
 * normalisation is computed server-side from the component weights and does not read this.
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
  heat_load: "up",
  dnbr: "up",
  slope_deg: "up",
  substrate_score: "up",
};

/**
 * The same question for a derived layer, answered from the layer it derives from.
 *
 * A departure and an annual minimum run the same way as their parent — a negative moisture
 * departure and a low annual moisture are both the dry end. A seasonal swing does not: a
 * wide annual range is fuel that cures, whichever indicator measured it.
 *
 * One function rather than a longer table, because every new spectral index would otherwise
 * need three more rows that could each be wrong on their own.
 */
export function dryingDirection(indicator: string): "up" | "down" {
  if (indicator.endsWith("_amplitude")) return "up";
  for (const suffix of ["_departure", "_annual_min"]) {
    if (indicator.endsWith(suffix)) {
      return DRYING_DIRECTION[indicator.slice(0, -suffix.length)] ?? "down";
    }
  }
  return DRYING_DIRECTION[indicator] ?? "down";
}

/**
 * Layers whose numbers are labels rather than quantities. Nothing may average them, ramp
 * them or call one of them higher than another; land cover has no direction of drying and
 * is absent from DRYING_DIRECTION for that reason rather than by oversight.
 */
export const CATEGORICAL_INDICATORS: ReadonlySet<string> = new Set(["land_cover"]);

/**
 * ESA WorldCover v200 legend, with the colour the map draws each class in. Colours are the
 * product's own, so a reader who knows WorldCover reads this map without a key.
 */
export const LAND_COVER_CLASSES: Record<number, { label: string; colour: string }> = {
  10: { label: "Tree cover", colour: "#006400" },
  20: { label: "Shrubland", colour: "#ffbb22" },
  30: { label: "Grassland", colour: "#ffff4c" },
  40: { label: "Cropland", colour: "#f096ff" },
  50: { label: "Built-up", colour: "#fa0000" },
  60: { label: "Bare or sparse vegetation", colour: "#b4b4b4" },
  70: { label: "Snow and ice", colour: "#f0f0f0" },
  80: { label: "Permanent water", colour: "#0064c8" },
  90: { label: "Herbaceous wetland", colour: "#0096a0" },
  95: { label: "Mangroves", colour: "#00cf75" },
  100: { label: "Moss and lichen", colour: "#fae6a0" },
};
