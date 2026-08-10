"""Fire perimeters from the National Burned Area Composite.

NBAC is the ground truth the severity model trains inside. The question v0.2 asks is not
where fire happens but how severely it burns once it does, so every target pixel has to sit
within a perimeter someone mapped from imagery rather than within a modelled footprint. NBAC
is that mapping for Canada: the Canadian Forest Service reconciles agency perimeters,
satellite hotspots and Landsat or Sentinel-2 delineation into one composite per year, and
republishes the whole series each spring.

Per-year archives rather than the combined 1972-2025 file. A single year is tens of
megabytes against a gigabyte for the composite, and the ten study years are read repeatedly
during validation, so each year's zip is cached on disk and read from there afterwards.

The release date rides in the file name — `NBAC_2023_20260513.zip` — and changes whenever
the composite is rebuilt, taking the geometry with it. It is discovered from the directory
listing rather than pinned in code, and recorded as the source version, so a result can
always be traced back to the composite it was computed against.

Attributes, as found in the 2026-05-13 release. They have moved between releases, so the
lookups below accept alternatives:

    YEAR, NFIREID, GID          fire identity; GID reads "2023_834"
    POLY_HA, ADJ_HA             polygon area, and area adjusted for unburned islands
    AG_SDATE, AG_EDATE          start and end as reported by the responsible agency
    HS_SDATE, HS_EDATE          start and end inferred from satellite hotspots
    CAPDATE                     acquisition date of the image the perimeter was drawn on
    BASRC, FIREMAPS, FIREMAPM   provenance of the perimeter itself
    FIRECAUS, PRESCRIBED        cause, and whether the burn was deliberate
    ADMIN_NAME, ADMIN_DIV       reporting agency and its subdivision
    ADJ_FLAG, VERSION           adjustment note, and the release date again

Agency dates are preferred and hotspot dates fill in behind them: the agency knows when it
was called, and the hotspots know when the fire was still moving. Both are frequently blank,
which is why `start_date` and `end_date` are optional.

Why the shapefile is read by hand. NBAC ships zipped ESRI shapefiles and this project has no
OGR binding — `fiona` and `pyogrio` are not dependencies, and the GDAL that rasterio bundles
exposes rasters only. Adding a wheel carrying a second copy of GDAL to read one frozen 1998
file format was the worse trade, so the reader at the bottom of this module parses it: a
fixed header, an index sidecar, and an array of doubles. It is checked against the data
rather than against trust. `POLY_HA` is NBAC's own polygon area, and the tests require the
geometry this reader reconstructs to reproduce that number in an equal-area projection.

NBAC is distributed in EPSG:3978, Canada Atlas Lambert, written into the `.prj` in ESRI's
WKT dialect with no EPSG code attached. The reprojection to EPSG:4326 is therefore stated
here rather than read from the file.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import numpy as np
import pyogrio
import pyogrio.raw
from pyproj import Transformer
from rasterio.features import rasterize
from shapely import from_wkb as shapely_from_wkb
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform
from tenacity import retry, stop_after_attempt, wait_exponential

from ...config import settings
from ..archive import SourceRecord
from ..spine import Spine

log = logging.getLogger(__name__)

BASE_URL = "https://cwfis.cfs.nrcan.gc.ca/downloads/nbac/"
DATASET = "NBAC"
ACCESS_ROUTE = "cwfis-datamart"
CITATION = (
    "Canadian Forest Service. National Burned Area Composite (NBAC). Natural Resources Canada."
)

#: Verbatim from https://cwfis.cfs.nrcan.gc.ca/downloads/licence.txt, including the typo in
#: the attribution sentence. The licence itself is open; the citation clause is a condition
#: of it, so it travels with the data rather than living in a README.
LICENCE = (
    "Open Government Licence - Canada "
    "(http://open.canada.ca/en/open-government-licence-canada). Attribution required: "
    '"When using these data for mapping activities and analysis, research, evaluation or '
    "display, please acknowledged the source using the following citation: Canadian Forest "
    "Service. Canadian Wildland Fire Information System (CWFIS), Natural Resources Canada, "
    "Canadian Forest Service, Northern Forestry Centre, Edmonton, Alberta. "
    'http://cwfis.cfs.nrcan.gc.ca."'
)

#: Canada Atlas Lambert, the projection every NBAC year arrives in.
NATIVE_CRS = "EPSG:3978"
WGS84 = "EPSG:4326"

#: The combined 1972-2025 archive shares the prefix, so the year is matched as four digits
#: and the release as eight, which the composite's `1972to2025` name cannot satisfy.
_ARCHIVE = re.compile(r"NBAC_(\d{4})_(\d{8})\.zip")

#: First candidate wins. Releases before 2022 named the agency dates AFSDATE and AFEDATE.
_FIRE_ID = ("GID",)
_AREA_HA = ("POLY_HA", "ADJ_HA")
_START_DATE = ("AG_SDATE", "AFSDATE", "HS_SDATE")
_END_DATE = ("AG_EDATE", "AFEDATE", "HS_EDATE")


@dataclass(frozen=True)
class Perimeter:
    """One mapped fire, in EPSG:4326."""

    fire_id: str
    year: int
    geometry: BaseGeometry
    area_ha: float
    start_date: date | None
    end_date: date | None


def available_years() -> dict[int, str]:
    """Year to download URL, discovered from the CWFIS directory listing."""
    response = _listing()
    return _parse_listing(response)


def perimeters(
    year: int,
    *,
    within: BaseGeometry | None = None,
    cache_dir: Path | None = None,
) -> tuple[list[Perimeter], SourceRecord]:
    """Fire perimeters for a year, optionally clipped to an area of interest.

    `within` selects the perimeters that intersect it and leaves their geometry whole. A
    perimeter cut at the study-area boundary would report a fire smaller than the one that
    burned, and nothing downstream needs the truncation: rasterisation onto the spine's grid
    already bounds the result to the area being measured.
    """
    directory = cache_dir or settings().data_dir / "cache" / "nbac"
    archive = _archive_for(year, directory)

    source = SourceRecord(
        dataset=DATASET,
        version=_release_of(archive),
        access_route=ACCESS_ROUTE,
        uri=BASE_URL + archive.name,
        citation=CITATION,
        native_resolution_m=None,
        native_timestep="annual",
        licence=LICENCE,
        # When the bytes were pulled, not when they were parsed. A cached year read a month
        # later is still an observation of the day it was fetched.
        retrieved=datetime.fromtimestamp(archive.stat().st_mtime, UTC),
    )
    return _read_perimeters(archive, year, within), source


def burned_mask(perimeters: list[Perimeter], spine: Spine) -> np.ndarray:
    """Boolean array on the spine's grid: True where a perimeter covers the pixel."""
    grid = spine.grid
    if not perimeters:
        return np.zeros(grid.shape, dtype=bool)

    to_grid = Transformer.from_crs(WGS84, grid.crs, always_xy=True).transform
    shapes = [shapely_transform(to_grid, perimeter.geometry) for perimeter in perimeters]

    # Pixel-centre rule, the same one the spine uses to assign pixels to cells. With
    # `all_touched` the two would disagree along every perimeter edge and burned area would
    # be inflated by a pixel's width all the way around each fire.
    burned: np.ndarray = rasterize(
        shapes,
        out_shape=grid.shape,
        transform=grid.transform,
        fill=0,
        default_value=1,
        dtype="uint8",
        all_touched=False,
    )
    return burned.astype(bool)


def burned_fraction(perimeters: list[Perimeter], spine: Spine) -> np.ndarray:
    """Per-cell fraction of the cell inside a perimeter, via spine.fraction()."""
    return spine.fraction(burned_mask(perimeters, spine))


# ---------------------------------------------------------------------------- acquisition


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=3, min=3, max=60), reraise=True)
def _listing() -> str:
    response = httpx.get(BASE_URL, timeout=settings().http_timeout_s, follow_redirects=True)
    response.raise_for_status()
    return response.text


def _parse_listing(html: str) -> dict[int, str]:
    return {
        int(year): f"{BASE_URL}NBAC_{year}_{release}.zip"
        for year, release in _ARCHIVE.findall(html)
    }


def _archive_for(year: int, directory: Path) -> Path:
    """The year's zip on disk, downloading it once if it is not already there."""
    directory.mkdir(parents=True, exist_ok=True)

    # If several releases are cached, the newest name wins; release dates sort as they read.
    cached = sorted(directory.glob(f"NBAC_{year}_*.zip"))
    if cached:
        return cached[-1]

    urls = available_years()
    if year not in urls:
        raise KeyError(f"CWFIS publishes no NBAC archive for {year}")

    url = urls[year]
    target = directory / url.rsplit("/", 1)[-1]
    log.info("downloading %s", url)
    _download(url, target)
    return target


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=3, min=3, max=60), reraise=True)
def _download(url: str, target: Path) -> None:
    # Streamed to a partial name and renamed on completion, so an interrupted download can
    # never be mistaken for a cache hit on the next run.
    partial = target.with_name(target.name + ".part")
    with httpx.stream(
        "GET", url, timeout=settings().http_timeout_s, follow_redirects=True
    ) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                handle.write(chunk)
    partial.replace(target)


def _release_of(archive: Path) -> str:
    match = _ARCHIVE.search(archive.name)
    if match is None:
        raise ValueError(f"{archive.name} is not an NBAC year archive")
    return match.group(2)


# ---------------------------------------------------------------------------- attributes


def _read_perimeters(archive: Path, year: int, within: BaseGeometry | None) -> list[Perimeter]:
    """Read a year's perimeters, keeping only those that meet the area of interest.

    GDAL reads the shapefile straight out of the zip through its virtual filesystem, and
    takes the spatial filter down with it, so a national file costs its index rather than
    its geometry. The filter is expressed in the file's own projection because that is what
    the record bounding boxes are in.
    """
    to_native = Transformer.from_crs(WGS84, NATIVE_CRS, always_xy=True).transform
    to_wgs84 = Transformer.from_crs(NATIVE_CRS, WGS84, always_xy=True).transform

    area_of_interest = shapely_transform(to_native, within) if within is not None else None
    bbox = area_of_interest.bounds if area_of_interest is not None else None

    meta, _, geometry_wkb, field_data = pyogrio.raw.read(
        f"/vsizip/{archive}", bbox=bbox, force_2d=True
    )
    fields = list(meta["fields"])

    found: list[Perimeter] = []
    for number, blob in enumerate(geometry_wkb):
        if blob is None:
            continue
        geometry = shapely_from_wkb(blob)
        if geometry is None or geometry.is_empty:
            continue

        # A bounding-box filter keeps rectangles, not shapes. The intersection test is what
        # actually decides whether a fire touched the study area.
        if area_of_interest is not None and not geometry.intersects(area_of_interest):
            continue

        row = {
            field: "" if values[number] is None else str(values[number])
            for field, values in zip(fields, field_data, strict=True)
        }
        found.append(
            Perimeter(
                fire_id=_fire_id(row, year, number),
                year=int(_number(row.get("YEAR")) or year),
                geometry=shapely_transform(to_wgs84, geometry),
                area_ha=_number(_first(row, _AREA_HA)) or 0.0,
                start_date=_date(_first(row, _START_DATE)),
                end_date=_date(_first(row, _END_DATE)),
            )
        )
    return found


def _fire_id(row: dict[str, str], year: int, number: int) -> str:
    """NBAC's own composite id where it exists, and one built the same way where it does not."""
    identifier = _first(row, _FIRE_ID)
    if identifier:
        return identifier
    sequence = _number(row.get("NFIREID"))
    return f"{year}_{int(sequence)}" if sequence is not None else f"{year}_{number + 1}"


def _first(row: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = row.get(name)
        if value:
            return value
    return None


def _number(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _date(value: str | None) -> date | None:
    """A fire date, from either form the field arrives in.

    The dBASE column holds `20230701`, but GDAL types it as a date and hands back
    `2023-07-01`, and a NaT becomes the string `NaT`. Both spellings mean the same day and
    neither is worth propagating a parsing quirk over.
    """
    if not value or value == "NaT":
        return None

    text = value.strip()[:10]
    for pattern in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------- shapefile reader
