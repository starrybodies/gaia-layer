"""A demo portfolio, built from open building footprints and labelled synthetic throughout.

The portfolio surface needs a book to scan and there is no client book to scan. What there
is: Overture's open building footprints, which are anonymously readable from
`s3://overturemaps-us-west-2` and cover the study area at 174,218 buildings. Those become a
plausible spatial distribution of exposure. They do not become a portfolio — there is no
insured value in an open footprint dataset, and inventing one is exactly the kind of number
that ends up quoted back as if it were measured.

So every value in the emitted book is **synthetic**, the payload says so at the top level, at
the row level and in the field names, and the derivation is written down: a fixed notional
rate per square metre of footprint, seeded, deterministic. Nothing here is a price, an
estimate of a price, or evidence about prices.

**The privacy contract, which is a design constraint rather than a policy note.** A real
client sends H3 cell identifiers and nothing else. Not addresses, not coordinates, not
policy numbers — a res-8 cell is about 0.74 km2 and roughly a neighbourhood, and that is the
finest thing this layer ever needs to know about an exposure. The book format has no field
for anything finer, `book_from_cells` accepts only cell ids, and a test sweeps the emitted
payload for anything that parses as a coordinate. Making the private form the *only* form it
can express is the difference between a promise and a constraint.

The book deliberately includes cells the archive cannot score. A demo assembled only from
cells with measurements would show a portfolio scan that never says "unmeasured", which is
the one thing a portfolio scan most needs to be able to say.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import h3
import numpy as np

from ..config import AreaOfInterest
from .area import H3_PARENT_RES, H3_RES

log = logging.getLogger(__name__)

#: The Overture release read. Pinned rather than "latest": a book that changes underneath a
#: screenshot is not a demo, it is a moving target.
OVERTURE_RELEASE = "2026-07-22.0"

#: Anonymous, no account, no token. Requester pays is what killed the Landsat route in D-011;
#: this bucket does not.
OVERTURE_BUCKET = "s3://overturemaps-us-west-2"

#: Notional cost per square metre of footprint. A round number, chosen to be obviously round.
#: It is not a construction cost, a replacement cost or a market rate, and the field it feeds
#: is named so that it cannot be quoted without the word "synthetic" attached.
SYNTHETIC_RATE_PER_M2 = 4000.0

#: Storeys assumed, so the notional value varies with something other than footprint alone.
#: Drawn per cell from a seeded generator, which is why the book is reproducible.
SYNTHETIC_STOREYS = (1.0, 3.0)

#: Metres per degree of latitude, used to turn a planar area in square degrees into square
#: metres with a cosine correction for longitude. Not `ST_Area_Spheroid`, which returns NaN
#: for Overture's `GEOMETRY('OGC:CRS84')` column and — because the first version of this
#: module treated a non-finite area as zero — produced a book in which every building had no
#: footprint and every synthetic value was 0. At a building's size and this latitude the
#: equirectangular approximation is well under a percent, which is far inside the tolerance
#: of a quantity that is invented anyway.
DEGREE_M = 111_320.0


class LeakedDetailError(ValueError):
    """The book was asked to carry something finer than a cell.

    Its own error because the mistake it guards against is not a crash — it is a book that
    works, scans correctly, and quietly contains the addresses the client was told it would
    never have to send.
    """


def overture_query(area: AreaOfInterest, *, release: str = OVERTURE_RELEASE) -> str:
    """SQL for the footprints inside an area, at the coarsest detail the book needs.

    Reads the bounding box columns rather than the geometry where it can, because the bbox
    struct is a top-level column in Overture's GeoParquet and filters before any geometry is
    decoded. The centroid is taken from the bbox rather than from the polygon: the book only
    needs to know which 0.74 km2 hex a building falls in, and a bbox centre and a true
    centroid disagree by metres.
    """
    bbox = area.bbox()
    path = f"{OVERTURE_BUCKET}/release/{release}/theme=buildings/type=building/*"
    return f"""
        SELECT
            (bbox.xmin + bbox.xmax) / 2 AS lon,
            (bbox.ymin + bbox.ymax) / 2 AS lat,
            ST_Area(geometry)
                * {DEGREE_M} * {DEGREE_M}
                * cos(radians((bbox.ymin + bbox.ymax) / 2)) AS footprint_m2
        FROM read_parquet('{path}', hive_partitioning = 1)
        WHERE bbox.xmin >= {bbox.west} AND bbox.xmax <= {bbox.east}
          AND bbox.ymin >= {bbox.south} AND bbox.ymax <= {bbox.north}
    """


def cells_from_footprints(
    lat: np.ndarray, lon: np.ndarray, footprint_m2: np.ndarray
) -> dict[str, dict[str, float]]:
    """Aggregate footprints to res-8 cells, discarding the coordinates as it goes.

    This is the step where the detail is dropped, and it is the only step that ever holds
    both a coordinate and a cell id. Everything downstream takes cells.
    """
    if not (lat.size == lon.size == footprint_m2.size):
        raise ValueError("latitude, longitude and footprint must be the same length")

    tally: dict[str, dict[str, float]] = {}
    for one_lat, one_lon, area in zip(lat, lon, footprint_m2, strict=True):
        if not (np.isfinite(one_lat) and np.isfinite(one_lon)):
            continue
        cell = h3.latlng_to_cell(float(one_lat), float(one_lon), H3_RES)
        entry = tally.setdefault(cell, {"buildings": 0.0, "measured": 0.0, "footprint_m2": 0.0})
        entry["buildings"] += 1.0
        # An unmeasurable area is counted as unmeasured, not as zero. The first version of
        # this added `0.0` for a NaN and produced a book of four hundred cells in which every
        # building had no footprint — which is the same silent-zero failure D-012 records for
        # elevation, arrived at from the other direction.
        if np.isfinite(area):
            entry["measured"] += 1.0
            entry["footprint_m2"] += float(area)
    return tally


def book_from_cells(
    tally: dict[str, dict[str, float]],
    *,
    size: int,
    seed: int = 0,
    minimum_buildings: int = 5,
) -> dict[str, Any]:
    """The book itself: cell ids, counts, and a notional value that says it is notional.

    Sampled with probability proportional to building count rather than taking the densest
    cells, so the book spans the valley floor and the interface rather than being ten
    downtown blocks. Seeded, so the same call gives the same book.
    """
    eligible = sorted(
        cell
        for cell, entry in tally.items()
        if entry["buildings"] >= minimum_buildings and entry["footprint_m2"] > 0.0
    )
    if not eligible:
        raise ValueError(
            "no cell carries enough buildings with a measurable footprint to build a book "
            "from. A cell whose footprints did not measure cannot carry even an invented "
            "value, and putting it in the book at zero would be the silent-zero failure "
            "this module exists to avoid."
        )
    for cell in eligible:
        if h3.get_resolution(cell) != H3_RES:
            raise LeakedDetailError(
                f"cell {cell} is resolution {h3.get_resolution(cell)}; the book is defined at "
                f"resolution {H3_RES} and a finer cell is a finer disclosure"
            )

    rng = np.random.default_rng(seed)
    weights = np.array([tally[cell]["buildings"] for cell in eligible], dtype="float64")
    chosen = rng.choice(
        len(eligible), size=min(size, len(eligible)), replace=False, p=weights / weights.sum()
    )

    entries: list[dict[str, Any]] = []
    for index in sorted(chosen):
        cell = eligible[index]
        entry = tally[cell]
        storeys = float(rng.uniform(*SYNTHETIC_STOREYS))
        entries.append(
            {
                "h3": cell,
                "h3_parent": h3.cell_to_parent(cell, H3_PARENT_RES),
                "exposures": int(entry["buildings"]),
                "footprint_m2": round(entry["footprint_m2"], 1),
                "synthetic_insured_value": round(
                    entry["footprint_m2"] * storeys * SYNTHETIC_RATE_PER_M2
                ),
            }
        )

    return {
        "synthetic": True,
        "label": "SYNTHETIC DEMO BOOK — NOT A PORTFOLIO",
        "warning": (
            "Every value in this file is invented. The cells and the building counts are "
            "real, from Overture's open footprints; the insured values are footprint area "
            f"times a notional {SYNTHETIC_RATE_PER_M2:,.0f} per square metre times a seeded "
            "storey count, and they are not prices, estimates of prices, or evidence about "
            "prices."
        ),
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "resolution": H3_RES,
        "parent_resolution": H3_PARENT_RES,
        "seed": seed,
        "footprint_source": {
            "dataset": "Overture Maps buildings",
            "release": OVERTURE_RELEASE,
            "uri": f"{OVERTURE_BUCKET}/release/{OVERTURE_RELEASE}/theme=buildings",
            "access_route": "anonymous S3",
            "licence": "ODbL / CDLA-Permissive-2.0 depending on contributing source",
        },
        "privacy": (
            "The book carries H3 cell identifiers and nothing else that locates anything. A "
            "real client sends the same shape: cell ids, counts and their own values. No "
            "address, coordinate or policy identifier is required by this format or accepted "
            "by it."
        ),
        "cells": entries,
        "totals": {
            "cells": len(entries),
            "exposures": sum(entry["exposures"] for entry in entries),
            "synthetic_insured_value": sum(entry["synthetic_insured_value"] for entry in entries),
        },
    }


def assert_cells_only(payload: dict[str, Any]) -> None:
    """Refuse to write a book that carries anything finer than a cell.

    Checked on the serialised form rather than on the structure, because the failure this
    guards against is a field added later that nobody thought of as a coordinate.
    """
    allowed = {
        "h3",
        "h3_parent",
        "exposures",
        "footprint_m2",
        "synthetic_insured_value",
    }
    for entry in payload["cells"]:
        extra = set(entry) - allowed
        if extra:
            raise LeakedDetailError(
                f"the book carries {sorted(extra)} per cell; the format is cell identifiers, "
                "counts and values, and anything else is a disclosure nobody agreed to"
            )


def write_book(payload: dict[str, Any], path: Path) -> Path:
    assert_cells_only(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    log.info(
        "wrote %s: %d cells, %d exposures",
        path,
        payload["totals"]["cells"],
        payload["totals"]["exposures"],
    )
    return path
