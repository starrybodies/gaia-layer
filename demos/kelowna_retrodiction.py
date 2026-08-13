#!/usr/bin/env python
"""Demo 1 — Kelowna 2023, asked in the order it actually happened.

Fits the validated candidate model on every fire before 2023, predicts the McDougall Creek
perimeter as of 14 August 2023 — the day before ignition — and then, only then, looks at what
burned. Reports Traders Cove and Wilson Landing by name, including if it missed them.

    uv run --project pipeline python demos/kelowna_retrodiction.py

Needs the feature table, which `build_features` writes to `data/eii/features.parquet`. It
needs the network only to resolve the two place names against the provincial gazetteer;
everything else comes off disk.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import h3

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline" / "src"))

from gaia_pipeline.eii.area import H3_RES  # noqa: E402
from gaia_pipeline.eii.run import archive_dir  # noqa: E402
from gaia_pipeline.eii.sources import gazetteer  # noqa: E402
from gaia_pipeline.validate.retrodiction import (  # noqa: E402
    AS_OF,
    COMMUNITIES,
    STRUCTURE_LOSS_CONTEXT,
    retrodict,
)

#: How the fire is identified. NBAC's ids are opaque — McDougall Creek is `2023_834` — and
#: the largest 2023 fire in this study area is not it: that is Crater Creek, 36,000 ha and a
#: hundred kilometres south. Picking by size would have produced a confident report about the
#: wrong fire, which is what nearly happened. So the fire is the one whose labelled cells
#: cover the ground the case study is about.
ANCHOR = ("Traders Cove", "Community")


def rule(text: str = "") -> None:
    print(f"\n{'-' * 78}\n{text}" if text else "-" * 78)


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    import pyarrow.parquet as pq

    path = archive_dir() / "features.parquet"
    if not path.exists():
        print(f"No feature table at {path}. Run build_features first.", file=sys.stderr)
        return 1

    features = pq.read_table(path)

    rule("Resolving the communities against the BC gazetteer")
    places: dict[str, tuple[str, float, float]] = {}
    for name, kind in COMMUNITIES:
        found = gazetteer.find(name, feature_type=kind)
        cell = h3.latlng_to_cell(found.lat, found.lon, H3_RES)
        places[cell] = (found.name, found.lat, found.lon)
        print(
            f"  {found.name:<18} {found.feature_type:<12} "
            f"{found.lat:.5f}, {found.lon:.5f}  {cell}"
        )

    rule("Finding the fire by the ground it covers, not by its size")
    anchor = gazetteer.find(ANCHOR[0], feature_type=ANCHOR[1])
    anchor_cell = h3.latlng_to_cell(anchor.lat, anchor.lon, H3_RES)

    import numpy as np

    cells = np.asarray(features.column("h3"))
    years = np.asarray(features.column("fire_year"), dtype="int64")
    ids = np.asarray(features.column("fire_id"))

    match = (cells == anchor_cell) & (years == 2023)
    if not match.any():
        print(
            f"No labelled 2023 cell at {anchor.name} ({anchor_cell}), so the fire cannot be "
            "identified from the ground up. Stopping rather than guessing.",
            file=sys.stderr,
        )
        return 1

    fire_id = str(ids[match][0])
    n_cells = int(((ids == fire_id)).sum())
    print(f"  {anchor.name} falls in {fire_id}, which holds {n_cells:,} labelled cells")
    print(f"  as of {AS_OF}, the day before McDougall Creek's 15 August run")
    print(
        "  caveat: the fire weather in the features is computed at NBAC's recorded start "
        "date for this perimeter, which precedes 14 August. The as-of date is when the "
        "question is asked, not the vintage of every input."
    )

    rule("Fitting on everything before 2023, then predicting")
    result = retrodict(features, fire_id=fire_id, places=places)

    print(f"  trained on {result.trained_on_cells:,} cells from {result.trained_on_years}")
    print(f"  predicted {result.n_cells:,} cells inside the perimeter")
    print(f"  flagged the top {100 * (1 - 0.80):.0f}% by predicted probability, at p >= {result.threshold:.4f}")

    rule("What it said about the two communities")
    for place in result.places:
        predicted = "—" if place.predicted is None else f"{place.predicted:.4f}"
        print(f"  {place.name:<18} p = {predicted:<8} {place.verdict}")
    for note in result.notes:
        print(f"  note: {note}")

    rule("Across the whole fire")
    print(f"  cells that burned at high severity : {result.observed_severe_cells:,}")
    print(f"  cells flagged in advance           : {result.flagged_cells:,}")
    print(f"  hits                               : {result.hits:,}")
    print(f"  misses (burned severely, unflagged): {result.misses:,}")
    print(f"  false alarms                       : {result.false_alarms:,}")
    print(f"  recall                             : {result.recall:.3f}")
    print(f"  precision                          : {result.precision:.3f}")

    rule("Case-study context, which is not a prediction")
    for key in (
        "structures_lost_reported_august_2023",
        "structures_lost_revised_2025",
        "area_burned_ha",
        "insured_loss_cad",
    ):
        print(f"  {key.replace('_', ' '):<38} {STRUCTURE_LOSS_CONTEXT[key]:,}")
    print(f"\n  {STRUCTURE_LOSS_CONTEXT['sources']}")
    print(f"  {STRUCTURE_LOSS_CONTEXT['caveat']}")

    rule()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
