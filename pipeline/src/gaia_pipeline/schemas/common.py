"""Enums, identifiers and geometry types shared across every schema in the layer."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    """Base for every schema here: unknown fields are an error, not a shrug.

    A response that silently accepts extra keys is a response whose shape cannot be
    relied on downstream. Rejecting them keeps the generated Zod schemas honest.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)


class ValidationStatus(StrEnum):
    """Outcome of the constraint engine for a single value."""

    VALIDATED = "validated"
    FLAGGED = "flagged"
    REJECTED = "rejected"


class ServedStatus(StrEnum):
    """The subset of :class:`ValidationStatus` a served value is allowed to carry.

    ``rejected`` is deliberately absent. A rejected value is never served as an answer,
    and this enum is what makes that a type error rather than a convention.
    """

    VALIDATED = "validated"
    FLAGGED = "flagged"


class Severity(StrEnum):
    WARN = "warn"
    ERROR = "error"


class IndicatorId(StrEnum):
    """Every quantity the layer can serve.

    Grouped by family; the family is derivable via :func:`indicator_family`.
    """

    # --- spectral (Sentinel-2) -------------------------------------------------
    NDVI = "ndvi"
    NDMI = "ndmi"
    NBR = "nbr"

    # --- climate (ERA5 / ERA5-Land) --------------------------------------------
    VPD_KPA = "vpd_kpa"
    PRECIP_30D_MM = "precip_30d_mm"
    TEMP_MAX_C = "temp_max_c"
    DAYS_SINCE_RAIN = "days_since_rain"

    # --- soil (ERA5-Land volumetric) -------------------------------------------
    SOIL_MOISTURE_0_7CM = "soil_moisture_0_7cm"
    SOIL_MOISTURE_7_28CM = "soil_moisture_7_28cm"

    # --- terrain (Copernicus DEM 30 m) ------------------------------------------
    ELEVATION_M = "elevation_m"
    SLOPE_DEG = "slope_deg"
    ASPECT_DEG = "aspect_deg"
    TWI = "twi"


class IndicatorFamily(StrEnum):
    SPECTRAL = "spectral"
    CLIMATE = "climate"
    SOIL = "soil"
    TERRAIN = "terrain"


_FAMILY_BY_INDICATOR: dict[IndicatorId, IndicatorFamily] = {
    IndicatorId.NDVI: IndicatorFamily.SPECTRAL,
    IndicatorId.NDMI: IndicatorFamily.SPECTRAL,
    IndicatorId.NBR: IndicatorFamily.SPECTRAL,
    IndicatorId.VPD_KPA: IndicatorFamily.CLIMATE,
    IndicatorId.PRECIP_30D_MM: IndicatorFamily.CLIMATE,
    IndicatorId.TEMP_MAX_C: IndicatorFamily.CLIMATE,
    IndicatorId.DAYS_SINCE_RAIN: IndicatorFamily.CLIMATE,
    IndicatorId.SOIL_MOISTURE_0_7CM: IndicatorFamily.SOIL,
    IndicatorId.SOIL_MOISTURE_7_28CM: IndicatorFamily.SOIL,
    IndicatorId.ELEVATION_M: IndicatorFamily.TERRAIN,
    IndicatorId.SLOPE_DEG: IndicatorFamily.TERRAIN,
    IndicatorId.ASPECT_DEG: IndicatorFamily.TERRAIN,
    IndicatorId.TWI: IndicatorFamily.TERRAIN,
}


def indicator_family(indicator: IndicatorId) -> IndicatorFamily:
    return _FAMILY_BY_INDICATOR[indicator]


UNITS: dict[IndicatorId, str] = {
    IndicatorId.NDVI: "index",
    IndicatorId.NDMI: "index",
    IndicatorId.NBR: "index",
    IndicatorId.VPD_KPA: "kPa",
    IndicatorId.PRECIP_30D_MM: "mm",
    IndicatorId.TEMP_MAX_C: "degC",
    IndicatorId.DAYS_SINCE_RAIN: "days",
    IndicatorId.SOIL_MOISTURE_0_7CM: "m3/m3",
    IndicatorId.SOIL_MOISTURE_7_28CM: "m3/m3",
    IndicatorId.ELEVATION_M: "m",
    IndicatorId.SLOPE_DEG: "degrees",
    IndicatorId.ASPECT_DEG: "degrees",
    IndicatorId.TWI: "index",
}

Longitude = Annotated[float, Field(ge=-180.0, le=180.0)]
Latitude = Annotated[float, Field(ge=-90.0, le=90.0)]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]

Position = Annotated[list[float], Field(min_length=2, max_length=3)]
LinearRing = Annotated[list[Position], Field(min_length=4)]


class Polygon(Strict):
    """GeoJSON Polygon, WGS84 lon/lat, per RFC 7946."""

    type: Literal["Polygon"] = "Polygon"
    coordinates: Annotated[list[LinearRing], Field(min_length=1)]


class MultiPolygon(Strict):
    type: Literal["MultiPolygon"] = "MultiPolygon"
    coordinates: Annotated[list[list[LinearRing]], Field(min_length=1)]


class BBox(Strict):
    """Axis-aligned bounding box in WGS84 degrees."""

    west: Longitude
    south: Latitude
    east: Longitude
    north: Latitude

    @model_validator(mode="after")
    def _ordered(self) -> BBox:
        if self.east <= self.west:
            raise ValueError("bbox east must exceed west")
        if self.north <= self.south:
            raise ValueError("bbox north must exceed south")
        return self

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.west, self.south, self.east, self.north)

    def as_polygon(self) -> Polygon:
        ring: list[list[float]] = [
            [self.west, self.south],
            [self.east, self.south],
            [self.east, self.north],
            [self.west, self.north],
            [self.west, self.south],
        ]
        return Polygon(coordinates=[ring])


Geometry = Annotated[Polygon | MultiPolygon | BBox, Field(union_mode="left_to_right")]


class DateRange(Strict):
    """Inclusive date range. The layer works in whole days; sub-daily is out of scope."""

    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> DateRange:
        if self.end < self.start:
            raise ValueError("date range end must not precede start")
        return self

    def __str__(self) -> str:
        return f"{self.start.isoformat()}/{self.end.isoformat()}"
