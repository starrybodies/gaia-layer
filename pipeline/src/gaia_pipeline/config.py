"""Runtime configuration: where the data lake lives, and what area of interest to work on.

The pilot AOI is a default, not a constant. Any GeoJSON polygon can be registered and the
whole pipeline follows it — that is what makes this a layer rather than one bespoke study.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .schemas.common import BBox, MultiPolygon, Polygon

# The Southern Gulf Islands and the adjacent coastal Douglas-fir zone on southeastern
# Vancouver Island. Chosen because it is the driest biogeoclimatic zone in coastal British
# Columbia, carries a Sentinel-2 archive with workable cloud statistics in summer, and is
# small enough that a full 12-month ingest runs on a Mac mini.
PILOT_BBOX = BBox(west=-123.90, south=48.40, east=-123.10, north=49.00)

# UTM zone 10N. Metric, minimal distortion at this longitude, and the CRS Sentinel-2 tiles
# over this area are already delivered in, so reprojection is a no-op for the spectral
# indicators.
ANALYSIS_CRS = "EPSG:32610"

# 20 m is the native resolution of the SWIR bands (B11, B12) that NDMI and NBR depend on.
# Resampling those up to the 10 m of B04/B08 would fabricate detail the sensor never
# recorded, so the common grid is set by the coarsest band the indicators need.
GRID_RESOLUTION_M = 20.0


class AreaOfInterest(BaseModel):
    """A named area the layer maintains coverage for."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    aoi_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    name: str = Field(min_length=1)
    description: str = ""
    geometry: Polygon | MultiPolygon
    analysis_crs: str = ANALYSIS_CRS
    grid_resolution_m: float = Field(default=GRID_RESOLUTION_M, gt=0.0)

    @classmethod
    def from_geojson(
        cls, path: Path, aoi_id: str, name: str, description: str = ""
    ) -> AreaOfInterest:
        """Load an AOI from a GeoJSON file.

        Accepts a bare geometry, a Feature, or a FeatureCollection whose first feature
        carries the geometry — all three are what people actually have on disk.
        """
        raw: Any = json.loads(path.read_text())
        geom = _extract_geometry(raw)
        parsed: Polygon | MultiPolygon = (
            Polygon.model_validate(geom)
            if geom["type"] == "Polygon"
            else MultiPolygon.model_validate(geom)
        )
        return cls(aoi_id=aoi_id, name=name, description=description, geometry=parsed)

    def bbox(self) -> BBox:
        rings: list[list[list[float]]]
        if isinstance(self.geometry, Polygon):
            rings = self.geometry.coordinates
        else:
            rings = [ring for poly in self.geometry.coordinates for ring in poly]
        xs = [pt[0] for ring in rings for pt in ring]
        ys = [pt[1] for ring in rings for pt in ring]
        return BBox(west=min(xs), south=min(ys), east=max(xs), north=max(ys))


def _extract_geometry(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("GeoJSON root must be an object")
    kind = raw.get("type")
    if kind in {"Polygon", "MultiPolygon"}:
        return raw
    if kind == "Feature":
        return _extract_geometry(raw["geometry"])
    if kind == "FeatureCollection":
        features = raw.get("features") or []
        if not features:
            raise ValueError("FeatureCollection contains no features")
        return _extract_geometry(features[0])
    raise ValueError(f"unsupported GeoJSON type for an AOI: {kind!r}")


PILOT_AOI = AreaOfInterest(
    aoi_id="sgi-cdf",
    name="Southern Gulf Islands and Coastal Douglas-fir",
    description=(
        "Wildfire-relevant pilot zone in southern British Columbia: the Southern Gulf "
        "Islands and the adjacent Coastal Douglas-fir biogeoclimatic zone on southeastern "
        "Vancouver Island."
    ),
    geometry=PILOT_BBOX.as_polygon(),
)


def canonical_geometry(geometry: Polygon | MultiPolygon | BBox) -> str:
    """Render a geometry to the canonical string that gets hashed.

    Deliberately not JSON. The service layer has to compute this same hash in TypeScript,
    and JSON round-trips of floats do not agree across the two languages — Python writes
    ``48.0`` where JavaScript writes ``48``. Formatting every ordinate to a fixed six
    decimal places sidesteps that entirely, and six places is about 11 cm at this latitude,
    which is far below the 20 m analysis grid.

    Form: ``TYPE:x,y;x,y|ring|ring#polygon``
    """
    if isinstance(geometry, BBox):
        geometry = geometry.as_polygon()

    def ring(points: list[list[float]]) -> str:
        return ";".join(f"{pt[0]:.6f},{pt[1]:.6f}" for pt in points)

    if isinstance(geometry, Polygon):
        body = "|".join(ring(r) for r in geometry.coordinates)
        return f"Polygon:{body}"
    body = "#".join("|".join(ring(r) for r in poly) for poly in geometry.coordinates)
    return f"MultiPolygon:{body}"


def geometry_hash(geometry: Polygon | MultiPolygon | BBox) -> str:
    """Stable 16-hex-character hash of a geometry, used to key values and claims."""
    return hashlib.sha256(canonical_geometry(geometry).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Settings:
    """Paths and knobs. Everything overridable by environment variable."""

    data_dir: Path
    duckdb_path: Path
    raster_dir: Path
    parquet_dir: Path
    api_port: int
    http_timeout_s: float
    max_concurrent_reads: int
    history_months: int

    @property
    def manifest_dir(self) -> Path:
        return self.data_dir / "manifests"


@lru_cache(maxsize=1)
def settings() -> Settings:
    root = Path(os.environ.get("GAIA_DATA_DIR", _repo_root() / "data")).resolve()
    return Settings(
        data_dir=root,
        duckdb_path=Path(os.environ.get("GAIA_DUCKDB_PATH", root / "gaia.duckdb")).resolve(),
        raster_dir=root / "lake" / "rasters",
        parquet_dir=root / "lake" / "tables",
        api_port=int(os.environ.get("API_PORT", "8811")),
        http_timeout_s=float(os.environ.get("GAIA_HTTP_TIMEOUT", "120")),
        max_concurrent_reads=int(os.environ.get("GAIA_MAX_CONCURRENT_READS", "8")),
        history_months=int(os.environ.get("GAIA_HISTORY_MONTHS", "12")),
    )


def _repo_root() -> Path:
    # pipeline/src/gaia_pipeline/config.py -> repo root is four levels up.
    return Path(__file__).resolve().parents[3]


def ensure_dirs() -> None:
    s = settings()
    for path in (s.data_dir, s.raster_dir, s.parquet_dir, s.manifest_dir):
        path.mkdir(parents=True, exist_ok=True)


def default_history_range(today: date | None = None) -> tuple[date, date]:
    """The 12 whole months ending with the last complete month before ``today``."""
    anchor = today or date.today()
    end_year, end_month = (
        (anchor.year, anchor.month - 1) if anchor.month > 1 else (anchor.year - 1, 12)
    )
    end = _last_day_of_month(end_year, end_month)
    months = settings().history_months
    start_month_index = end_year * 12 + (end_month - 1) - (months - 1)
    start = date(start_month_index // 12, start_month_index % 12 + 1, 1)
    return start, end


def _last_day_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])
