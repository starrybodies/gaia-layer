"""The B, D and E backfill D-016 left outstanding, and the persist that closes it.

    uv run --project pipeline python pipeline/scripts/build_components.py [YYYY-MM-DD]

One as-of date: 14 August 2023, the day before McDougall Creek. Components A and C were
already built; B, D and E were code-complete and tested with no archive behind them, because
the metered route could not deliver forty years at eighty-eight nodes. The store can.

Resumable in the sense the cache is: every lattice series lands in `data/eii/cache` and is
skipped on a second run. Deleting one of those files is how to refetch it.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import date

from gaia_pipeline.eii.run import build_components, build_spine, persist_components

#: The day before McDougall Creek. A second as-of date in a different year is what the
#: portfolio's change view compares against, and the archive partitions by year, so the two
#: dates have to sit in different years to coexist.
DEFAULT_AS_OF = date(2023, 8, 14)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # One line per range request would be tens of thousands of lines of nothing.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log = logging.getLogger("backfill")

    as_of = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_AS_OF
    log.info("as of %s", as_of)

    started = time.monotonic()
    spine = build_spine()
    log.info("spine: %d cells", spine.n_cells)

    tables = build_components(spine, as_of=as_of)
    for name, table in tables.items():
        log.info("%s: %d rows, columns %s", name, table.num_rows, table.column_names)

    directory = persist_components(spine, tables, as_of=as_of)
    log.info("persisted to %s in %.1f minutes", directory, (time.monotonic() - started) / 60.0)


if __name__ == "__main__":
    main()
