"""Regenerate `data/eii/diagnostics.json` from the cached feature table.

    uv run --project pipeline python pipeline/scripts/build_diagnostics.py

The diagnostics on disk were written before the per-dimension coverage fix in
`validate/diagnostics.py`, and still carried the pooled note that read "covers 9,836 of
3,835 cells" — a figure that counts every cell once per stratum dimension and is therefore
larger than the table it claims to cover. Nothing derived from that file should reach a
diligence surface until it has been rebuilt by the current code, which is what this does.

Deterministic: the folds are the same spatially-blocked folds the gate uses, with the same
seed, over the same cached `features.parquet`. Nothing here fetches.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from gaia_pipeline.eii.run import archive_dir, build_spine, cell_coordinates
from gaia_pipeline.validate.diagnostics import run_diagnostics
from gaia_pipeline.validate.splits import DEFAULT_BUFFER_KM, spatial_folds

SEED = 0
FOLDS = 5


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log = logging.getLogger("diagnostics")
    started = time.monotonic()

    features = pq.read_table(archive_dir() / "features.parquet")
    spine = build_spine()
    cells = features.column("h3").to_pylist()
    x, y = cell_coordinates(spine, cells)
    folds = spatial_folds(x, y, n_folds=FOLDS, buffer_km=DEFAULT_BUFFER_KM, seed=SEED)

    labels = np.asarray(features.column("high_severity")).astype(int)
    log.info("%d cells, %d positives, %d folds", len(labels), int(labels.sum()), len(folds))

    result = run_diagnostics(features, labels, folds, seed=SEED)

    path: Path = archive_dir() / "diagnostics.json"
    path.write_text(json.dumps(asdict(result), indent=2, sort_keys=True))
    log.info("wrote %s in %.1f minutes", path, (time.monotonic() - started) / 60.0)
    for note in result.notes:
        log.info("note: %s", note)


if __name__ == "__main__":
    main()
