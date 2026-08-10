"""Assembling the feature table the experiment is fitted on.

Features are built only for cells that carry a label, which is to say only for ground that
burned. That is not a shortcut. The question is conditional — given that this cell burned,
did it burn severely — so a cell that never burned is not a negative example, it is not an
example at all, and fetching inventory for all 43,303 cells to use four thousand of them
would cost hours to answer a question nobody asked.

Four groups arrive here, matching the four the experiment knows how to switch on and off:

- **structure**, Component A's z-scores against each cell's own biogeoclimatic context
- **terrain**, from the Copernicus DEM, including the heat load index v0.1 added
- **fuel**, the FBP type, treated as the lossy prior it is
- **weather**, the fire weather codes at the fire's own ignition date rather than an annual
  mean, because a season's average says nothing about the afternoon a fire ran

The weather join is the one worth stating plainly: every cell in a fire gets that fire's
codes. Within a 36,000 ha perimeter the real weather varied, and ERA5-Land at 9 km could not
resolve it anyway. This is a per-fire covariate wearing a per-cell column, and the report
says so.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pyarrow as pa

from ..indices.terrain import heat_load_index, slope_and_aspect
from ..raster import read_window
from .archive import SourceRecord
from .components.structure import component_a
from .sources import bcgw, canopy, fbp, nbac
from .sources.weather import FwiState, fwi_series, noon_weather
from .spine import Spine

log = logging.getLogger(__name__)

#: Copernicus DEM through the same anonymous STAC route v0.1 uses for terrain.
DEM_COLLECTION = "cop-dem-glo-30"

#: Fire weather is accumulated from the start of the calendar year so the drought code has
#: the memory it is supposed to have by the time a fire starts in July.
FWI_SPINUP_START = (3, 1)

#: Where a fire has no agency start date, high summer is assumed and the cell is flagged.
ASSUMED_IGNITION = (8, 1)


def terrain_features(spine: Spine) -> tuple[dict[str, np.ndarray], list[SourceRecord]]:
    """Elevation, slope, aspect and heat load on the spine's cells.

    Reuses v0.1's terrain indices rather than reimplementing them; the heat load index in
    particular was written and tested in this codebase a day before this module.
    """
    from pystac_client import Client

    bbox = _spine_bbox(spine)
    client = Client.open("https://earth-search.aws.element84.com/v1")
    items = list(client.search(collections=[DEM_COLLECTION], bbox=list(bbox)).items())
    if not items:
        raise RuntimeError("no Copernicus DEM tiles cover the study area")

    elevation = np.full(spine.grid.shape, np.nan, dtype="float32")
    for item in items:
        asset = item.assets.get("data") or next(iter(item.assets.values()))
        # The STAC item advertises s3://, which GDAL will only follow with credentials.
        # The bucket is public over HTTPS, which is the difference between a cold start
        # that works and one that asks the operator for AWS keys.
        href = asset.href.replace("s3://", "https://").replace(
            "copernicus-dem-30m", "copernicus-dem-30m.s3.amazonaws.com"
        )
        try:
            patch = read_window(href, spine.grid)
        except Exception as error:
            log.warning("DEM tile %s unavailable: %s", item.id, error)
            continue
        elevation = np.where(np.isfinite(elevation), elevation, patch)

    if not np.isfinite(elevation).any():
        raise RuntimeError("no DEM data landed on the grid")

    # Sea level is not a thing here, but the DEM's own nodata is, and a zero would read as
    # flat valley bottom rather than absence.
    elevation = np.where(elevation > -100.0, elevation, np.nan)

    slope, aspect = slope_and_aspect(elevation, spine.grid.resolution_m)
    centre_lat = float(np.mean(np.asarray(spine.cells.column("lat"))))
    heat = heat_load_index(slope, aspect, centre_lat)

    columns: dict[str, np.ndarray] = {}
    for name, surface in (
        ("elevation_m", elevation),
        ("slope_deg", slope),
        ("aspect_deg", aspect),
        ("heat_load", heat),
    ):
        columns[name], _ = spine.mean(surface)

    source = SourceRecord(
        dataset="Copernicus DEM GLO-30",
        version="2021",
        access_route="earth-search-stac",
        uri=f"{DEM_COLLECTION} over {bbox}",
        citation="European Space Agency (2021). Copernicus DEM GLO-30.",
        native_resolution_m=30.0,
        native_timestep="single epoch",
        licence="Copernicus free and open",
    )
    return columns, [source]


def structure_features(spine: Spine) -> tuple[pa.Table, list[SourceRecord]]:
    """Component A over the spine, with the inventory it needs."""
    from ..schemas.common import BBox

    west, south, east, north = _spine_bbox(spine)
    bbox = BBox(west=west, south=south, east=east, north=north)

    bec_features, bec_source = bcgw.fetch_features(
        bcgw.BEC_LAYER, bbox, properties=["MAP_LABEL", "ZONE", "SUBZONE", "VARIANT"]
    )
    bec_grid, _ = bcgw.rasterise(bec_features, spine, attribute="MAP_LABEL", categorical=True)
    bec_codes, _ = spine.majority(bec_grid)

    vri_features, vri_source = bcgw.fetch_features(
        bcgw.VRI_LAYER, bbox, properties=["CROWN_CLOSURE", "PROJ_AGE_1", "BCLCS_LEVEL_2"]
    )
    closure_grid, _ = bcgw.rasterise(
        vri_features, spine, attribute="CROWN_CLOSURE", categorical=False
    )
    age_grid, _ = bcgw.rasterise(vri_features, spine, attribute="PROJ_AGE_1", categorical=False)
    cover_grid, _cover_labels = bcgw.rasterise(
        vri_features, spine, attribute="BCLCS_LEVEL_2", categorical=True
    )

    crown_closure, _ = spine.mean(closure_grid)
    stand_age, _ = spine.mean(age_grid)
    cover_codes, _ = spine.majority(cover_grid)

    heights, _, canopy_source = canopy.cell_heights(spine)

    table = component_a(
        spine,
        canopy_height=heights,
        crown_closure=crown_closure,
        stand_age=stand_age,
        bec_codes=bec_codes,
        cover_codes=cover_codes,
    )
    return table, [bec_source, vri_source, canopy_source]


def fuel_features(spine: Spine) -> tuple[dict[str, np.ndarray], list[SourceRecord]]:
    """FBP fuel type per cell, by majority because a class code is a name."""
    codes, _, source = fbp.cell_fuel_type(spine)
    return {"fbp_fuel_type": codes}, [source]


def weather_for_fires(
    perimeters: list[nbac.Perimeter], year: int
) -> tuple[dict[str, dict[str, float]], list[SourceRecord]]:
    """Fire weather codes at each fire's ignition, keyed by fire id.

    One point per fire, at its centroid. ERA5-Land is 9 km, so a per-cell weather field
    inside a perimeter would be interpolation dressed as measurement.
    """
    codes: dict[str, dict[str, float]] = {}
    sources: list[SourceRecord] = []

    for perimeter in perimeters:
        centroid = perimeter.geometry.centroid
        ignition = perimeter.start_date or date(year, *ASSUMED_IGNITION)
        start = date(year, *FWI_SPINUP_START)
        if ignition <= start:
            ignition = start + timedelta(days=30)

        try:
            observations, source = noon_weather(centroid.y, centroid.x, start, ignition)
        except Exception as error:
            log.warning("weather unavailable for %s: %s", perimeter.fire_id, error)
            continue

        series = fwi_series(
            observations.column("date").to_pylist(),
            np.asarray(observations.column("temp_c"), dtype="float64"),
            np.asarray(observations.column("rh_pct"), dtype="float64"),
            np.asarray(observations.column("wind_kmh"), dtype="float64"),
            np.asarray(observations.column("rain_mm"), dtype="float64"),
            start=FwiState(),
        )
        if series.num_rows == 0:
            continue

        last = series.num_rows - 1
        codes[perimeter.fire_id] = {
            name: float(series.column(name)[last].as_py())
            for name in ("ffmc", "dmc", "dc", "isi", "bui", "fwi", "vpd_kpa")
        }
        sources.append(source)

    return codes, sources


def assemble(
    spine: Spine,
    labels: pa.Table,
    *,
    structure: pa.Table,
    terrain: dict[str, np.ndarray],
    fuel: dict[str, np.ndarray],
    weather: dict[str, dict[str, float]],
) -> pa.Table:
    """Join every feature group onto the labelled cells, in label order."""
    positions = {cell: index for index, cell in enumerate(spine.cells.column("h3").to_pylist())}
    rows = np.array([positions[cell] for cell in labels.column("h3").to_pylist()])

    columns: dict[str, pa.Array] = {
        "h3": labels.column("h3"),
        "fire_year": labels.column("fire_year"),
        "fire_id": labels.column("fire_id"),
        "high_severity": labels.column("high_severity"),
        "dnbr": labels.column("dnbr"),
    }

    for name, values in terrain.items():
        columns[name] = pa.array(np.asarray(values)[rows], pa.float32())
    for name, values in fuel.items():
        columns[name] = pa.array(np.asarray(values)[rows], pa.float32())

    structure_by_cell = {
        cell: index for index, cell in enumerate(structure.column("h3").to_pylist())
    }
    structure_rows = np.array(
        [structure_by_cell.get(cell, -1) for cell in labels.column("h3").to_pylist()]
    )
    for name in ("z_canopy_height", "z_crown_closure", "z_stand_age", "a_score"):
        if name not in structure.column_names:
            continue
        values = np.asarray(structure.column(name), dtype="float64")
        picked = np.where(structure_rows >= 0, values[np.maximum(structure_rows, 0)], np.nan)
        columns[name] = pa.array(picked, pa.float32())

    fire_ids = labels.column("fire_id").to_pylist()
    for name in ("ffmc", "dmc", "dc", "isi", "bui", "fwi", "vpd_kpa"):
        columns[name] = pa.array(
            [weather.get(fire, {}).get(name, float("nan")) for fire in fire_ids], pa.float32()
        )

    return pa.table(columns)


def _spine_bbox(spine: Spine) -> tuple[float, float, float, float]:
    lat = np.asarray(spine.cells.column("lat"))
    lon = np.asarray(spine.cells.column("lon"))
    margin = 0.02
    return (
        float(lon.min() - margin),
        float(lat.min() - margin),
        float(lon.max() + margin),
        float(lat.max() + margin),
    )
