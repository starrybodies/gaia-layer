"""The label: did this cell, having burned, burn severely.

This is the only module whose errors cannot be caught downstream. A mistake in a feature
weakens the model; a mistake here changes what the model is being asked, and every metric
after it measures the wrong question convincingly.

Three refusals define it.

A cell that is only partly inside a perimeter is dropped rather than labelled. A hex half
in and half out has a severity that is an average of burned and unburned ground, which is a
number describing nothing. `MINIMUM_BURNED_FRACTION` is where the line sits.

A cell without enough clear observations either side of the fire is dropped rather than
labelled. Missing severity is not low severity, and treating it as a negative example would
teach the model that cloudy ground does not burn.

A cell that burned twice in the study period is kept once per fire, and the reburn is
flagged. It is a legitimately hard case rather than a corrupt one, but a reader comparing
counts should be able to see how many there are.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa
from shapely.geometry import shape as shapely_shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .archive import SourceRecord
from .sources import nbac, severity
from .sources.severity import HIGH_SEVERITY_DNBR, SeverityWindow
from .spine import Spine

log = logging.getLogger(__name__)

#: How much of a cell must lie inside a perimeter before it counts as burned ground.
MINIMUM_BURNED_FRACTION = 0.5

#: How much of a cell must carry a usable severity measurement before it is labelled.
MINIMUM_MEASURED_FRACTION = 0.5

#: A season needs at least this many clear scenes for its composite to be trusted. One
#: scene is an observation; three is a composite that can survive an undetected cloud.
MINIMUM_SCENES = 2


@dataclass(frozen=True)
class LabelSet:
    """Labels, and the full account of what was thrown away to get them."""

    table: pa.Table
    sources: list[SourceRecord] = field(default_factory=list)
    excluded: dict[str, int] = field(default_factory=dict)

    @property
    def prevalence(self) -> float:
        if self.table.num_rows == 0:
            return float("nan")
        return float(np.asarray(self.table.column("high_severity")).mean())


def _cell_bounds(spine: Spine, mask: np.ndarray) -> tuple[float, float, float, float] | None:
    """Bounding box in WGS84 of the cells a fire touches, with a margin for the composite."""
    if not mask.any():
        return None

    lat = np.asarray(spine.cells.column("lat"))[mask]
    lon = np.asarray(spine.cells.column("lon"))[mask]

    margin = 0.02  # roughly two kilometres, enough to cover the hexes' own extent
    return (
        float(lon.min() - margin),
        float(lat.min() - margin),
        float(lon.max() + margin),
        float(lat.max() + margin),
    )


def _resample_to_spine(window: SeverityWindow, spine: Spine) -> np.ndarray:
    """Put a fire's severity patch onto the spine's own grid.

    The patch is computed on a grid snapped to the same resolution and CRS as the spine, so
    this is an offset copy rather than a resample — no interpolation touches the values.
    """
    surface = np.full(spine.grid.shape, np.nan, dtype="float32")

    column = round((window.transform.c - spine.grid.left) / spine.grid.resolution_m)
    row = round((spine.grid.top - window.transform.f) / spine.grid.resolution_m)

    height, width = window.dnbr.shape
    row_start, column_start = max(row, 0), max(column, 0)
    row_end = min(row + height, spine.grid.height)
    column_end = min(column + width, spine.grid.width)

    if row_end <= row_start or column_end <= column_start:
        return surface

    surface[row_start:row_end, column_start:column_end] = window.dnbr[
        row_start - row : row_end - row, column_start - column : column_end - column
    ]
    return surface


def severity_labels(
    spine: Spine,
    years: tuple[int, ...],
    *,
    minimum_burned: float = MINIMUM_BURNED_FRACTION,
    minimum_measured: float = MINIMUM_MEASURED_FRACTION,
) -> LabelSet:
    """Per-cell high-severity labels for every fire year, with the exclusions counted."""
    rows: dict[str, list[Any]] = {
        "h3": [],
        "fire_year": [],
        "fire_id": [],
        "dnbr": [],
        "burned_fraction": [],
        "measured_fraction": [],
        "high_severity": [],
        "reburn": [],
    }
    sources: list[SourceRecord] = []
    excluded = {
        "partly_burned_cells": 0,
        "unmeasured_cells": 0,
        "fires_without_imagery": 0,
        "fires_outside_area": 0,
    }
    seen: dict[str, int] = {}
    cell_ids = spine.cells.column("h3").to_pylist()

    for year in years:
        perimeters, source = nbac.perimeters(year, within=_study_geometry(spine))
        sources.append(source)
        if not perimeters:
            excluded["fires_outside_area"] += 1
            continue

        for perimeter in perimeters:
            burned = nbac.burned_fraction([perimeter], spine)
            inside = burned >= minimum_burned
            partial = (burned > 0.0) & ~inside
            excluded["partly_burned_cells"] += int(partial.sum())

            if not inside.any():
                continue

            bounds = _cell_bounds(spine, inside)
            if bounds is None:
                continue

            window, scene_sources = severity.severity_for_bounds(
                bounds,
                year,
                crs=spine.grid.crs,
                resolution_m=spine.grid.resolution_m,
            )
            if min(window.observations_pre, window.observations_post) < MINIMUM_SCENES:
                log.warning(
                    "fire %s has %d pre and %d post scenes; no label",
                    perimeter.fire_id,
                    window.observations_pre,
                    window.observations_post,
                )
                excluded["fires_without_imagery"] += 1
                continue

            sources.extend(scene_sources)

            surface = _resample_to_spine(window, spine)
            mean_dnbr, measured = spine.mean(surface)

            usable = inside & (measured >= minimum_measured) & np.isfinite(mean_dnbr)
            excluded["unmeasured_cells"] += int((inside & ~usable).sum())

            for index in np.flatnonzero(usable):
                cell = cell_ids[index]
                rows["h3"].append(cell)
                rows["fire_year"].append(year)
                rows["fire_id"].append(perimeter.fire_id)
                rows["dnbr"].append(float(mean_dnbr[index]))
                rows["burned_fraction"].append(float(burned[index]))
                rows["measured_fraction"].append(float(measured[index]))
                rows["high_severity"].append(bool(mean_dnbr[index] >= HIGH_SEVERITY_DNBR))
                rows["reburn"].append(cell in seen)
                seen[cell] = seen.get(cell, 0) + 1

    table = pa.table(
        {
            "h3": pa.array(rows["h3"], pa.string()),
            "fire_year": pa.array(rows["fire_year"], pa.int16()),
            "fire_id": pa.array(rows["fire_id"], pa.string()),
            "dnbr": pa.array(rows["dnbr"], pa.float32()),
            "burned_fraction": pa.array(rows["burned_fraction"], pa.float32()),
            "measured_fraction": pa.array(rows["measured_fraction"], pa.float32()),
            "high_severity": pa.array(rows["high_severity"], pa.bool_()),
            "reburn": pa.array(rows["reburn"], pa.bool_()),
        }
    )

    log.info(
        "%d labelled cells, %.1f%% high severity, %d excluded",
        table.num_rows,
        100.0 * float(np.asarray(table.column("high_severity")).mean()) if table.num_rows else 0.0,
        sum(excluded.values()),
    )
    return LabelSet(table=table, sources=sources, excluded=excluded)


def _study_geometry(spine: Spine) -> BaseGeometry:
    """The study polygon, which is what perimeters are filtered against."""
    from .area import STUDY_AREA

    geometry: BaseGeometry = shapely_shape(STUDY_AREA.geometry.model_dump())
    return geometry


def merge_reburns(table: pa.Table) -> pa.Table:
    """One row per cell per fire is the natural grain; some models want one row per cell.

    Where a cell burned more than once, the most severe fire is kept. Taking the worst
    rather than the latest matches the question being asked — whether this ground is capable
    of burning severely — and taking a mean would invent a fire that never happened.
    """
    if table.num_rows == 0:
        return table

    order = np.lexsort(
        (-np.asarray(table.column("dnbr"), dtype="float64"), np.asarray(table.column("h3")))
    )
    sorted_table = table.take(pa.array(order))

    cells = np.asarray(sorted_table.column("h3"))
    keep = np.ones(cells.size, dtype=bool)
    keep[1:] = cells[1:] != cells[:-1]

    return sorted_table.filter(pa.array(keep))


def union_of(perimeters: list[nbac.Perimeter]) -> BaseGeometry:
    """Merged footprint of a year's fires, for reporting rather than labelling."""
    merged: BaseGeometry = unary_union([perimeter.geometry for perimeter in perimeters])
    return merged
