"""Build the synthetic demo book from Overture's open building footprints.

    uv run --project pipeline python pipeline/scripts/build_demo_book.py

Reads `s3://overturemaps-us-west-2` anonymously through DuckDB — no account, no token, and
the bucket is not requester-pays, which is what disqualified the Landsat route in D-011.
Writes `data/eii/demo-book.json`.

The read is the only step that holds a coordinate. Everything it writes is cell identifiers,
counts and a value that says in its own field name that it was invented.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import duckdb
import numpy as np

from gaia_pipeline.eii import demo_book
from gaia_pipeline.eii.area import STUDY_AREA
from gaia_pipeline.eii.run import archive_dir

#: Enough cells that the portfolio view has something to rank, few enough that the whole
#: book fits on one screen when a reviewer wants to check it by eye.
BOOK_SIZE = 400
SEED = 20260813


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log = logging.getLogger("demo-book")
    started = time.monotonic()

    conn = duckdb.connect()
    conn.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    conn.execute("SET s3_region = 'us-west-2';")
    # Anonymous. Empty credentials rather than absent ones, so a stray key in the environment
    # cannot quietly turn this into an authenticated read that nobody else can reproduce.
    conn.execute("SET s3_access_key_id = ''; SET s3_secret_access_key = '';")

    log.info("reading Overture %s over the study area", demo_book.OVERTURE_RELEASE)
    rows = conn.execute(demo_book.overture_query(STUDY_AREA)).fetchnumpy()
    log.info("%d footprints in %.0f s", rows["lat"].size, time.monotonic() - started)

    tally = demo_book.cells_from_footprints(
        np.asarray(rows["lat"], dtype="float64"),
        np.asarray(rows["lon"], dtype="float64"),
        np.asarray(rows["footprint_m2"], dtype="float64"),
    )
    log.info("%d res-8 cells carry at least one building", len(tally))

    book = demo_book.book_from_cells(tally, size=BOOK_SIZE, seed=SEED)
    path: Path = archive_dir() / "demo-book.json"
    demo_book.write_book(book, path)
    log.info(
        "%d cells, %d exposures, %.1f minutes",
        book["totals"]["cells"],
        book["totals"]["exposures"],
        (time.monotonic() - started) / 60.0,
    )


if __name__ == "__main__":
    main()
