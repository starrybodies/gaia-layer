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

from ..config import settings
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
