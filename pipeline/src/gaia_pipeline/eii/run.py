"""Building the v0.2 archive: spine, labels, features, and the gate.

Each stage writes what it produced and can be re-run without the others. A ten-year
severity archive is hours of imagery reads, and a pipeline that has to start from the top
after a network wobble is a pipeline nobody finishes.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import shape as shapely_shape
from shapely.geometry.base import BaseGeometry

from ..config import settings
from ..validate.experiment import run_experiment
from ..validate.report import write_report
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

    log.info("terrain")
    terrain, _ = features.terrain_features(spine)
    log.info("fuel type")
    fuel, _ = features.fuel_features(spine)
    log.info("structure")
    structure, _ = features.structure_features(spine)

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
    path = write_report(
        result,
        Path("docs/plans/10-component-a-validation.md"),
        context={
            "years": f"{min(years)}-{max(years)}",
            "folds": n_folds,
            "buffer_km": DEFAULT_BUFFER_KM,
            "block_size_km": 20,
            "minimum_train_test_distance_m": round(leakage["minimum_train_test_distance_m"]),
            "excluded": {},
        },
    )
    log.info("gate %s; report at %s", "PASSED" if result.gate_passes else "FAILED", path)
    return path
