"""Building the v0.2 archive: spine, labels, features, and the gate.

Each stage writes what it produced and can be re-run without the others. A ten-year
severity archive is hours of imagery reads, and a pipeline that has to start from the top
after a network wobble is a pipeline nobody finishes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import shape as shapely_shape
from shapely.geometry.base import BaseGeometry

from ..config import settings
from ..validate.experiment import run_experiment
from ..validate.report import GATE_STATEMENT, write_report
from ..validate.splits import DEFAULT_BUFFER_KM, leakage_report, spatial_folds
from .area import STUDY_AREA, STUDY_YEARS
from .spine import Spine
from .target import severity_labels

log = logging.getLogger(__name__)

#: Fires below this size contribute few whole cells and cost a full imagery read each. The
#: floor is stated rather than hidden: it is a compute decision, and the report says how
#: much burned area it excluded.
MINIMUM_FIRE_HA = 200.0


def archive_dir() -> Path:
    return settings().data_dir / "eii"


def cache_dir() -> Path:
    return archive_dir() / "cache"


def build_spine() -> Spine:
    """The H3 spine over the study area, cached after the first build."""
    spine = Spine.build(STUDY_AREA, cache_dir())
    log.info(
        "spine: %d cells on a %dx%d grid at %.0f m",
        spine.n_cells,
        spine.grid.width,
        spine.grid.height,
        spine.grid.resolution_m,
    )
    return spine


def build_labels(
    spine: Spine, years: tuple[int, ...] = STUDY_YEARS, *, minimum_fire_ha: float = MINIMUM_FIRE_HA
) -> pa.Table:
    """Severity labels for every fire year, written one year at a time.

    Per-year writes are the recovery story: a failure in 2021 costs 2021, not the nine years
    already measured.
    """
    directory = archive_dir() / "labels"
    directory.mkdir(parents=True, exist_ok=True)

    written: list[pa.Table] = []
    for year in years:
        path = directory / f"labels-{year}.parquet"
        if path.exists():
            log.info("%d already measured", year)
            written.append(pq.read_table(path))
            continue

        labels = severity_labels(spine, (year,))
        pq.write_table(labels.table, path, compression="zstd")
        # Beside the labels, not only in the log. A cached run never reaches this branch
        # again, and an exclusion nobody can still count is an exclusion nobody reports.
        _exclusions_path(year).write_text(json.dumps(labels.excluded, sort_keys=True, indent=2))

        log.info(
            "%d: %d cells, %.1f%% high severity, excluded %s",
            year,
            labels.table.num_rows,
            100.0 * labels.prevalence if labels.table.num_rows else 0.0,
            labels.excluded,
        )
        written.append(labels.table)

    if not written:
        return pa.table({})
    return pa.concat_tables([table for table in written if table.num_rows > 0])


def _exclusions_path(year: int) -> Path:
    return archive_dir() / "labels" / f"exclusions-{year}.json"


def label_exclusions(years: tuple[int, ...] = STUDY_YEARS) -> tuple[dict[str, int], list[int]]:
    """What the labelling threw away, summed over the years, and which years cannot say.

    Returned as two things rather than one, because a year whose count was never written
    is not a year that excluded nothing. Adding it in as a zero would understate the total
    and there would be no way to tell from the result.
    """
    totals: dict[str, int] = {}
    unrecorded: list[int] = []
    for year in years:
        path = _exclusions_path(year)
        if not (archive_dir() / "labels" / f"labels-{year}.parquet").exists():
            continue
        if not path.exists():
            unrecorded.append(year)
            continue
        for reason, count in json.loads(path.read_text()).items():
            totals[reason] = totals.get(reason, 0) + int(count)
    return totals, unrecorded


def cell_coordinates(spine: Spine, cells: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """Projected coordinates for a list of cell ids, for the spatial splitter."""
    from pyproj import Transformer

    index = {cell: position for position, cell in enumerate(spine.cells.column("h3").to_pylist())}
    lat = np.asarray(spine.cells.column("lat"))
    lon = np.asarray(spine.cells.column("lon"))

    rows = np.array([index[cell] for cell in cells])
    transformer = Transformer.from_crs("EPSG:4326", spine.grid.crs, always_xy=True)
    x, y = transformer.transform(lon[rows], lat[rows])
    return np.asarray(x), np.asarray(y)


def build_features(spine: Spine, labels: pa.Table) -> pa.Table:
    """Every feature group, joined onto the labelled cells and cached.

    The inventory fetches are the slow part and they do not change between runs, so the
    assembled table is written once and reused. Deleting the file is the way to rebuild.
    """
    from . import features
    from .sources import nbac

    path = archive_dir() / "features.parquet"
    if path.exists():
        log.info("features already assembled")
        return pq.read_table(path)

    terrain = _cached_columns("terrain", spine, features.terrain_features)
    fuel = _cached_columns("fuel", spine, features.fuel_features)
    structure = _cached_table("structure", spine, features.structure_features)

    log.info("fire weather")
    weather: dict[str, dict[str, float]] = {}
    for year in sorted(set(labels.column("fire_year").to_pylist())):
        perimeters, _ = nbac.perimeters(int(year), within=_study_geometry())
        codes, _ = features.weather_for_fires(perimeters, int(year))
        weather.update(codes)

    table = features.assemble(
        spine, labels, structure=structure, terrain=terrain, fuel=fuel, weather=weather
    )
    pq.write_table(table, path, compression="zstd")
    log.info("features: %d rows, %d columns", table.num_rows, len(table.column_names))
    return table


# ------------------------------------------------------------------ components B through E


#: The date each year's components are taken as of, unless a caller names another. The first
#: of August is the middle of the Okanagan fire season and the same assumption
#: `features.ASSUMED_IGNITION` makes for a fire with no recorded start date, so a component
#: and a label for the same year describe the same moment.
DEFAULT_AS_OF = (8, 1)


#: How many lattice nodes are fetched and cached together. Large enough to hold the whole
#: study lattice in one chunk, which is the opposite of what the metered archive wanted and
#: is what the published store rewards: its files are chunked six longitudes wide, so
#: splitting the lattice into eights re-reads the same bytes for every split. The per-chunk
#: Parquet cache stays, because a whole-lattice forty-year read is still minutes rather than
#: seconds and deleting one file is still how to refetch it.
NODES_PER_CHUNK = 128


def _cached_series(name: str, build: Callable[[], tuple[pa.Table, Any]]) -> pa.Table:
    """A lattice series, fetched once. Deleting the file is how to refetch it."""
    path = cache_dir() / f"{name}.parquet"
    if path.exists():
        log.info("%s already fetched", name)
        return pq.read_table(path)
    table, _ = build()
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="zstd")
    return table


def _cached_lattice(
    name: str,
    points: list[tuple[float, float]],
    fetch: Callable[[list[tuple[float, float]]], tuple[pa.Table, Any]],
) -> pa.Table:
    """The same series, but written a chunk of nodes at a time and resumable.

    Each chunk's `point` column is local to its own request, so it is offset back onto the
    lattice's own numbering before the chunks are concatenated. Getting that wrong would
    silently attribute one node's weather to another, which is the kind of error that
    produces a plausible map and a wrong one.
    """
    parts: list[pa.Table] = []
    for start in range(0, len(points), NODES_PER_CHUNK):
        chunk = points[start : start + NODES_PER_CHUNK]
        path = cache_dir() / f"{name}-{start:04d}.parquet"
        if path.exists():
            parts.append(pq.read_table(path))
            continue

        log.info("%s: nodes %d-%d of %d", name, start, start + len(chunk) - 1, len(points))
        table, _ = fetch(chunk)
        offset = pa.array(np.asarray(table.column("point"), dtype="int32") + start, pa.int32())
        table = table.set_column(table.column_names.index("point"), "point", offset)

        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path, compression="zstd")
        parts.append(table)

    return pa.concat_tables(parts)


def climate_series(as_of: date) -> tuple[list[tuple[float, float]], pa.Table, pa.Table]:
    """The lattice, its long water balance, and its noon weather over the study years.

    The balance reaches back to `climate.REFERENCE_START` because Components B and E are
    both departures and a departure needs a distribution behind it. The noon weather only
    covers the study years: the fire weather codes are re-run from a spring startup each
    season, so their reference is the study period rather than the century.
    """
    from .sources import climate

    points = climate.lattice(STUDY_AREA)
    log.info("climate lattice: %d nodes at %.2f degrees", len(points), climate.LATTICE_SPACING_DEG)

    balance = _cached_lattice(
        f"climate-balance-{as_of.isoformat()}",
        points,
        lambda chunk: climate.water_balance(chunk, climate.REFERENCE_START, as_of),
    )
    noon_start = date(min(STUDY_YEARS), 3, 1)
    weather = _cached_lattice(
        f"climate-noon-{as_of.isoformat()}",
        points,
        lambda chunk: climate.noon_weather_lattice(chunk, noon_start, max(as_of, noon_start)),
    )
    return points, balance, weather


def _soil_reference(
    points: list[tuple[float, float]], as_of: date
) -> tuple[np.ndarray, np.ndarray, int]:
    """Mean shallow and deep soil moisture over the window ending `as_of`, and in past years.

    Fetched one season at a time rather than as one long series. ERA5-Land soil moisture is
    hourly and only available hourly, so forty years of it is tens of millions of values to
    extract two thirty-day means; the seasons around the same date in the study years are
    what the departure is actually taken against.
    """
    from .components.water import MOISTURE_WINDOW_DAYS
    from .sources import climate

    span = timedelta(days=MOISTURE_WINDOW_DAYS - 1)
    years = [year for year in STUDY_YEARS if year <= as_of.year]

    shallow = np.full((len(points), len(years)), np.nan)
    deep = np.full((len(points), len(years)), np.nan)

    for column, year in enumerate(years):
        try:
            end = as_of.replace(year=year)
        except ValueError:  # 29 February in a common year
            end = as_of.replace(year=year, day=28)

        def fetch(chunk: list[tuple[float, float]], window_end: date = end) -> tuple[pa.Table, Any]:
            return climate.soil_moisture(chunk, window_end - span, window_end)

        table = _cached_lattice(f"climate-soil-{end.isoformat()}", points, fetch)
        node = np.asarray(table.column("point"), dtype="int64")
        for name, into in (("soil_shallow", shallow), ("soil_deep", deep)):
            values = np.asarray(table.column(name), dtype="float64")
            present = np.isfinite(values)
            totals = np.bincount(node[present], weights=values[present], minlength=len(points))
            counts = np.bincount(node[present], minlength=len(points))
            with np.errstate(invalid="ignore"):
                into[:, column] = np.where(counts > 0, totals / np.maximum(counts, 1), np.nan)

    current = years.index(as_of.year)
    return shallow, deep, current


def build_components(spine: Spine, *, as_of: date | None = None) -> dict[str, pa.Table]:
    """Components B through E and the composite index, for one as-of date.

    Every component is a departure from the same node's own history, so the whole thing
    hangs off one date rather than a year: the retrodiction needs to ask what the index said
    on 14 August 2023, not what it said about 2023.
    """
    from .components import composite, drought, moisture, riparian, water
    from .components.reference import season_flags
    from .sources import bcgw, canopy, climate

    as_of = as_of or date(max(STUDY_YEARS), *DEFAULT_AS_OF)
    points, balance, weather = climate_series(as_of)
    n_points = len(points)

    # ---- B: water balance and antecedent soil moisture
    deficit_z = water.deficit_anomaly(balance, n_points=n_points, as_of=as_of)
    shallow, deep, current = _soil_reference(points, as_of)
    others = [column for column in range(shallow.shape[1]) if column != current]
    shallow_z = water.moisture_anomaly(shallow[:, current], shallow[:, others])
    deep_z = water.moisture_anomaly(deep[:, current], deep[:, others])

    b_flags = season_flags(deficit_z, np.column_stack([shallow[:, others], deep[:, others]]))
    component_b = water.component_b(
        spine,
        deficit_z=climate.to_cells(spine, points, deficit_z),
        soil_shallow_z=climate.to_cells(spine, points, shallow_z),
        soil_deep_z=climate.to_cells(spine, points, deep_z),
        flags=np.rint(climate.to_cells(spine, points, b_flags.astype("float64"))).astype("int64"),
    )

    # ---- D: fire weather codes and vapour pressure deficit
    reference_years = [year for year in STUDY_YEARS if year < as_of.year]
    codes = moisture.seasonal_codes(weather, n_points=n_points, as_of=as_of)
    history = {name: np.full((n_points, len(reference_years)), np.nan) for name in codes}
    for column, year in enumerate(reference_years):
        try:
            earlier = as_of.replace(year=year)
        except ValueError:
            earlier = as_of.replace(year=year, day=28)
        past = moisture.seasonal_codes(weather, n_points=n_points, as_of=earlier)
        for name in history:
            history[name][:, column] = past[name]

    component_d = moisture.component_d(
        spine,
        dc_z=climate.to_cells(spine, points, moisture.code_anomaly(codes["dc"], history["dc"])),
        bui_z=climate.to_cells(spine, points, moisture.code_anomaly(codes["bui"], history["bui"])),
        vpd_z=climate.to_cells(
            spine, points, moisture.code_anomaly(codes["vpd_kpa"], history["vpd_kpa"])
        ),
    )

    # ---- E: drought
    spei_by_scale = drought.spei_at(balance, n_points=n_points, as_of=as_of)
    component_e = drought.component_e(
        spine,
        spei_by_scale={
            scale: climate.to_cells(spine, points, values)
            for scale, values in spei_by_scale.items()
        },
    )

    # ---- C: riparian
    component_c = _build_riparian(spine, bcgw=bcgw, canopy=canopy, riparian=riparian)

    # ---- A, already built, and the composite over all five
    structure = _cached_table("structure", spine, _structure_features)
    index = composite.compose(
        spine,
        scores={
            "a_structure": np.asarray(structure.column("a_score"), dtype="float64"),
            "b_water": np.asarray(component_b.column("b_score"), dtype="float64"),
            "c_riparian": np.asarray(component_c.column("c_score"), dtype="float64"),
            "d_moisture": np.asarray(component_d.column("d_score"), dtype="float64"),
            "e_drought": np.asarray(component_e.column("e_score"), dtype="float64"),
        },
        uncertainties={
            "a_structure": np.asarray(structure.column("uncertainty"), dtype="float64"),
            "b_water": np.asarray(component_b.column("uncertainty"), dtype="float64"),
            "c_riparian": np.asarray(component_c.column("uncertainty"), dtype="float64"),
            "d_moisture": np.asarray(component_d.column("uncertainty"), dtype="float64"),
            "e_drought": np.asarray(component_e.column("uncertainty"), dtype="float64"),
        },
    )

    return {
        "a_structure": structure,
        "b_water": component_b,
        "c_riparian": component_c,
        "d_moisture": component_d,
        "e_drought": component_e,
        "eii": index,
    }


def _structure_features(spine: Spine) -> tuple[pa.Table, list[Any]]:
    from . import features

    return features.structure_features(spine)


def _build_riparian(spine: Spine, *, bcgw: Any, canopy: Any, riparian: Any) -> pa.Table:
    """Component C, over the Freshwater Atlas and the canopy mosaic.

    The three water layers are unioned into one mask rather than kept apart. A cell beside
    both a stream and a wetland has one riparian band, not two, and adding the fractions
    would report more riparian ground than the cell contains.
    """
    from ..schemas.common import BBox
    from .components.reference import reference_strata

    west, south, east, north = _spine_bbox(spine)
    bbox = BBox(west=west, south=south, east=east, north=north)

    mask = np.zeros(spine.grid.shape, dtype=bool)
    covered = np.zeros(spine.n_cells, dtype=bool)
    for layer in (bcgw.FWA_STREAMS_LAYER, bcgw.FWA_LAKES_LAYER, bcgw.FWA_WETLANDS_LAYER):
        try:
            features, _ = bcgw.fetch_features(layer, bbox)
        except Exception as error:
            log.warning("freshwater atlas layer %s unavailable: %s", layer, error)
            continue
        mask |= bcgw.rasterise_presence(features, spine, buffer_m=riparian.RIPARIAN_BUFFER_M)
        covered[:] = True

    if not covered.any():
        log.warning("no Freshwater Atlas coverage; Component C will be entirely missing")

    grid_canopy, _ = canopy.fetch(spine)

    structure = _cached_table("structure", spine, _structure_features)
    strata = reference_strata(
        np.asarray(structure.column("bec_stratum"), dtype="float64"),
        np.zeros(spine.n_cells, dtype="float64"),
    )

    return riparian.component_c(
        spine, riparian_mask=mask, canopy=grid_canopy, strata=strata, covered=covered
    )


def _spine_bbox(spine: Spine) -> tuple[float, float, float, float]:
    from .features import _spine_bbox as bounds

    return bounds(spine)


def _study_geometry() -> BaseGeometry:
    geometry: BaseGeometry = shapely_shape(STUDY_AREA.geometry.model_dump())
    return geometry


def run_gate(spine: Spine, features_table: pa.Table, *, n_folds: int = 5, seed: int = 0) -> Path:
    """Fit the models, evaluate the gate, and write the report."""
    cells = features_table.column("h3").to_pylist()
    x, y = cell_coordinates(spine, cells)

    folds = spatial_folds(x, y, n_folds=n_folds, buffer_km=DEFAULT_BUFFER_KM, seed=seed)
    leakage = leakage_report(x, y, folds, buffer_km=DEFAULT_BUFFER_KM)
    log.info("leakage check: %s", leakage)

    labels = np.asarray(features_table.column("high_severity")).astype(int)
    table = {
        name: np.asarray(features_table.column(name), dtype="float64")
        for name in features_table.column_names
        if name not in {"h3", "fire_id", "high_severity", "dnbr", "fire_year"}
    }

    result = run_experiment(table, labels, folds, seed=seed)

    years = sorted(set(features_table.column("fire_year").to_pylist()))
    excluded, unrecorded = label_exclusions()
    excluded["cells_never_predicted_in_any_fold"] = int(
        (~result.evaluated[np.isfinite(result.labels)]).sum()
    )
    path = write_report(
        result,
        Path("docs/plans/10-component-a-validation.md"),
        context={
            "years": f"{min(years)}-{max(years)}",
            "folds": n_folds,
            "buffer_km": DEFAULT_BUFFER_KM,
            "block_size_km": 20,
            "minimum_train_test_distance_m": round(leakage["minimum_train_test_distance_m"]),
            "excluded": excluded,
            "exclusions_unrecorded_for": unrecorded,
            "minimum_fire_ha": MINIMUM_FIRE_HA,
        },
    )
    _write_validation_summary(result, as_of=path, years=years, leakage=leakage, folds=n_folds)
    log.info("gate %s; report at %s", "PASSED" if result.gate_passes else "FAILED", path)
    return path


def _cached_columns(
    name: str, spine: Spine, build: Callable[[Spine], tuple[dict[str, np.ndarray], list[Any]]]
) -> dict[str, np.ndarray]:
    """A per-cell column group, cached so a long assembly can be resumed.

    Each group costs minutes of inventory fetching and none of them depend on the labels, so
    a failure in one should not throw away the others.
    """
    path = archive_dir() / f"{name}.parquet"
    if path.exists():
        log.info("%s already built", name)
        table = pq.read_table(path)
        return {column: np.asarray(table.column(column)) for column in table.column_names}

    log.info("building %s", name)
    columns, _ = build(spine)
    pq.write_table(
        pa.table(
            {key: pa.array(np.asarray(value), pa.float32()) for key, value in columns.items()}
        ),
        path,
        compression="zstd",
    )
    return columns


def _cached_table(
    name: str, spine: Spine, build: Callable[[Spine], tuple[pa.Table, list[Any]]]
) -> pa.Table:
    path = archive_dir() / f"{name}.parquet"
    if path.exists():
        log.info("%s already built", name)
        return pq.read_table(path)

    log.info("building %s", name)
    table, _ = build(spine)
    pq.write_table(table, path, compression="zstd")
    return table


# --------------------------------------------------------------------- persisting the index


#: Which method record describes each component, so a fact can be traced to the equations
#: that produced it without the service having to know anything about the components.
def _component_methods() -> dict[str, Any]:
    from .components.composite import COMPOSITE_METHOD
    from .components.drought import DROUGHT_METHOD
    from .components.moisture import MOISTURE_METHOD
    from .components.riparian import RIPARIAN_METHOD
    from .components.structure import STRUCTURE_METHOD
    from .components.water import WATER_METHOD

    return {
        "a_structure": STRUCTURE_METHOD,
        "b_water": WATER_METHOD,
        "c_riparian": RIPARIAN_METHOD,
        "d_moisture": MOISTURE_METHOD,
        "e_drought": DROUGHT_METHOD,
        "eii": COMPOSITE_METHOD,
    }


#: Which column in each component's table is the component's own value, and which is its
#: doubt. Named here rather than guessed from position, because a rename that silently
#: served the wrong column would be undetectable downstream.
_VALUE_COLUMNS: dict[str, tuple[str, str]] = {
    "a_structure": ("a_score", "uncertainty"),
    "b_water": ("b_score", "uncertainty"),
    "c_riparian": ("c_score", "uncertainty"),
    "d_moisture": ("d_score", "uncertainty"),
    "e_drought": ("e_score", "uncertainty"),
    "eii": ("eii", "uncertainty"),
}


def persist_components(
    spine: Spine,
    tables: dict[str, pa.Table],
    *,
    as_of: date,
    sources: dict[str, list[Any]] | None = None,
) -> Path:
    """Write every component to the Parquet archive and register it in the catalog.

    One partition per component per year, which is what makes a year rebuildable without
    touching the rest. The provenance is stored by reference — the fact rows carry a method,
    a run and a source set, and the chain is assembled on read — so the archive is the size
    of the measurements rather than the size of the paperwork.
    """
    from .archive import (
        SourceRecord,
        finish_run,
        open_catalog,
        register_method,
        register_sources,
        start_run,
        write_cells,
        write_component,
    )

    directory = archive_dir()
    conn = open_catalog(directory / "catalog.duckdb")
    write_cells(conn, spine.cells)

    methods = _component_methods()
    period_start = date(as_of.year, 1, 1)
    written: dict[str, int] = {}

    for component, table in tables.items():
        value_column, doubt_column = _VALUE_COLUMNS[component]
        method = methods[component]
        register_method(conn, method)

        # A component with no source of its own still has to point at something, or its
        # facts cannot have their provenance resolved. The composite points at the archive
        # itself, which is honest: its inputs are the components, not an external dataset.
        records = (sources or {}).get(component) or [
            SourceRecord(
                dataset="gaia-layer EII components",
                version=method.version,
                access_route="internal",
                uri=f"eii://component/{component}",
                citation=method.citation,
                native_resolution_m=None,
                native_timestep="per as-of date",
                licence="see component sources",
            )
        ]
        source_set = register_sources(conn, records)
        run_id = start_run(
            conn,
            command=f"build_components(as_of={as_of.isoformat()})",
            component=component,
            method_id=method.method_id,
            source_set_id=source_set,
            parameters={"as_of": as_of.isoformat(), "cells": spine.n_cells},
        )

        n = table.num_rows
        facts = pa.table(
            {
                "h3": table.column("h3"),
                "period_start": pa.array([period_start] * n, pa.date32()),
                "period_end": pa.array([as_of] * n, pa.date32()),
                "component": pa.array([component] * n, pa.string()),
                "value": table.column(value_column).cast(pa.float64()),
                "uncertainty_type": pa.array(["standard_error"] * n, pa.string()),
                "uncertainty_value": table.column(doubt_column).cast(pa.float64()),
                "valid_fraction": pa.array(
                    np.isfinite(np.asarray(table.column(value_column), dtype="float64")).astype(
                        "float64"
                    ),
                    pa.float64(),
                ),
                "method_id": pa.array([method.method_id] * n, pa.string()),
                "run_id": pa.array([run_id] * n, pa.string()),
                "source_set_id": pa.array([source_set] * n, pa.string()),
                "constraint_flags": table.column("flags")
                if "flags" in table.column_names
                else pa.array([""] * n, pa.string()),
            }
        )
        written[component] = write_component(
            conn, directory, facts=facts, component=component, year=as_of.year, run_id=run_id
        )
        finish_run(conn, run_id, status="succeeded")

    conn.close()
    log.info("persisted %s to %s", written, directory)
    return directory


def _write_validation_summary(
    result: Any, *, as_of: Path, years: list[Any], leakage: dict[str, Any], folds: int
) -> Path:
    """The gate verdict as machine-readable JSON, beside the archive.

    The markdown report is written for an actuary to read. An agent asking `eii://validation`
    needs the same facts without parsing prose, and needs them to be the same facts — so both
    come out of the same `ExperimentResult` in the same call rather than from two code paths
    that could drift.
    """

    def delta(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            "point": value.point,
            "low": value.low,
            "high": value.high,
            "excludes_zero": value.excludes_zero,
        }

    excluded, unrecorded = label_exclusions()
    payload = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "verdict": "PASS" if result.gate_passes else "FAIL",
        "gate_statement": GATE_STATEMENT,
        "report_path": str(as_of),
        "fire_years": [int(year) for year in years],
        "folds": folds,
        "leakage": {key: float(value) for key, value in leakage.items()},
        "gate_delta": delta(result.gate_delta),
        "attribution_delta": delta(result.attribution_delta),
        "calibration_delta": delta(result.calibration_delta),
        "models": {
            name: {
                "features": list(model.groups),
                **{key: float(value) for key, value in model.summary.items()},
            }
            for name, model in result.models.items()
        },
        "excluded": excluded,
        "exclusions_unrecorded_for": unrecorded,
        "notes": list(result.notes),
    }

    path = archive_dir() / "validation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return path
