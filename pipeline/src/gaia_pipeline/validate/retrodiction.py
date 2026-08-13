"""Kelowna 2023: what the index would have said on 14 August, and where it was wrong.

McDougall Creek started on 15 August 2023, crossed Okanagan Lake's west side, and destroyed
homes at Traders Cove and Wilson Landing. It is the case study the whole pitch rests on, and
a case study is worth nothing unless the model is asked the question in the order it actually
arrives: fit on what was known before, predict, then look.

So this is a strict temporal hold-out. The model trains on fires from every year *before*
2023 and never sees a 2023 cell, a 2023 label, or a 2023 weather code during fitting. The
prediction is then made for the cells inside the McDougall Creek perimeter as of the day
before ignition, and only afterwards is it compared with what burned.

**Misses are reported.** A retrodiction that only lists the places it got right is a
selection of the places it got right. The report below names every community it was asked
about, says whether it was flagged, says what actually happened there, and counts the
false negatives beside the true ones. If the model missed Wilson Landing, the report says
it missed Wilson Landing.

**What it cannot establish.** The label is remotely sensed burn severity, not structure loss.
The structure-loss counts around this fire — 189 reported by the Central Okanagan Emergency
Operations Centre in August 2023, revised to 303 in the province's 2025 investigation, over
13,970 ha, with CAD 480 million insured per IBC and CatIQ — are case-study context and are
labelled as such. Nothing here was trained on them and nothing here predicts them.

**And one thing the framing must not be allowed to imply.** The fire weather in the feature
table is computed at the perimeter's *recorded* start date, which is NBAC's date of first
record. For McDougall Creek that is 1 July 2023, not the 15 August blow-up that took the
homes. So "as of 14 August" describes when the question is being asked, not the vintage of
every input behind the answer: the weather codes are older than that date, and a codes-at-14-
August retrodiction would be a different and probably stronger exercise. Saying so is
cheaper than being caught assuming it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pyarrow as pa

from .experiment import CANDIDATE, FEATURE_GROUPS, HYPERPARAMETERS, MODELS, columns_for

log = logging.getLogger(__name__)

#: The fire, and the day before it started. NBAC records McDougall Creek's ignition as
#: 15 August 2023; the index is taken as of the 14th so that nothing in the prediction can
#: have been measured after the smoke.
FIRE_YEAR = 2023
AS_OF = date(2023, 8, 14)

#: The two communities the fire reached, as they are named in the provincial gazetteer.
#: `Wilson Landing` rather than `Wilson's Landing`: the gazetteer drops the apostrophe, and
#: the lookup has to match the gazetteer rather than the newspapers.
COMMUNITIES: tuple[tuple[str, str | None], ...] = (
    ("Traders Cove", "Community"),
    ("Wilson Landing", "Locality"),
)

#: A cell is "flagged" when its predicted probability of high-severity burn puts it in the
#: top fifth of the fire's own cells. Stated before the result is looked at, and stated as a
#: share rather than an absolute probability because the model's levels are known to be
#: over-confident in the tail while its ranking is what the gate tested.
FLAG_QUANTILE = 0.80


@dataclass(frozen=True)
class PlaceOutcome:
    """One named place: what was predicted for it, and what happened there."""

    name: str
    h3: str
    lat: float
    lon: float
    predicted: float | None
    flagged: bool
    observed_high_severity: bool | None
    dnbr: float | None

    @property
    def verdict(self) -> str:
        if self.predicted is None or self.observed_high_severity is None:
            return "not scored"
        if self.flagged and self.observed_high_severity:
            return "flagged, and burned severely"
        if self.flagged and not self.observed_high_severity:
            return "flagged, did not burn severely"
        if not self.flagged and self.observed_high_severity:
            return "MISSED: burned severely, not flagged"
        return "not flagged, did not burn severely"


@dataclass(frozen=True)
class Retrodiction:
    """The whole exercise, including the parts that did not work."""

    fire_id: str
    as_of: date
    n_cells: int
    trained_on_years: list[int]
    trained_on_cells: int
    threshold: float
    places: list[PlaceOutcome] = field(default_factory=list)
    flagged_cells: int = 0
    observed_severe_cells: int = 0
    hits: int = 0
    misses: int = 0
    false_alarms: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else float("nan")

    @property
    def precision(self) -> float:
        total = self.hits + self.false_alarms
        return self.hits / total if total else float("nan")


def _matrix(table: pa.Table, columns: list[str]) -> np.ndarray:
    return np.column_stack([np.asarray(table.column(name), dtype="float64") for name in columns])


def retrodict(
    features: pa.Table,
    *,
    fire_id: str,
    places: dict[str, tuple[str, float, float]],
    fire_year: int = FIRE_YEAR,
    as_of: date = AS_OF,
    flag_quantile: float = FLAG_QUANTILE,
    seed: int = 0,
) -> Retrodiction:
    """Fit on everything before `fire_year`, predict this fire, then compare.

    `places` maps an H3 cell id to a name and its coordinates, already resolved. The
    resolution happens outside this function so that the retrodiction itself needs no
    network and can be run against a fixture.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier

    years = np.asarray(features.column("fire_year"), dtype="int64")
    fire_ids = np.asarray(features.column("fire_id"))
    labels = np.asarray(features.column("high_severity"), dtype="int64")

    train = years < fire_year
    target = np.asarray([str(value) == fire_id for value in fire_ids])

    if not train.any():
        raise ValueError(f"nothing to train on before {fire_year}")
    if not target.any():
        raise ValueError(f"no cells found for fire {fire_id}")

    columns = columns_for(MODELS[CANDIDATE], list(features.column_names))
    matrix = _matrix(features, columns)
    categorical = [column in FEATURE_GROUPS["fuel"] for column in columns]

    model = HistGradientBoostingClassifier(
        categorical_features=categorical if any(categorical) else None,
        **{**HYPERPARAMETERS, "random_state": seed},
    )
    model.fit(matrix[train], labels[train])
    predicted = model.predict_proba(matrix[target])[:, 1]

    threshold = float(np.quantile(predicted, flag_quantile))
    flagged = predicted >= threshold
    observed = labels[target].astype(bool)

    cells = np.asarray(features.column("h3"))[target]
    dnbr = np.asarray(features.column("dnbr"), dtype="float64")[target]
    position = {str(cell): index for index, cell in enumerate(cells)}

    # Which cells the labelling kept at all, so a place that is not scored can be told why.
    # "Outside the perimeter" and "inside it but dropped during labelling" are different
    # facts about the ground, and only one of them is about the fire.
    elsewhere = {
        str(cell): str(fire)
        for cell, fire in zip(np.asarray(features.column("h3")), fire_ids, strict=True)
    }
    labelled_anywhere = set(elsewhere)

    outcomes: list[PlaceOutcome] = []
    notes: list[str] = []
    for h3, (name, lat, lon) in places.items():
        index = position.get(h3)
        if index is None:
            if h3 not in labelled_anywhere:
                reason = (
                    "its cell carries no severity label at all. The labelling drops cells "
                    "that are only partly inside a perimeter and cells with no usable "
                    "imagery either side of the fire, and a lakeshore community at a fire's "
                    "edge is exactly both of those"
                )
            else:
                reason = f"its cell is labelled under {elsewhere[h3]} rather than {fire_id}"
            notes.append(
                f"{name} sits at {lat:.5f}, {lon:.5f}, in cell {h3}, and is not scored because "
                f"{reason}. It is reported rather than dropped: the model was never asked "
                "about this ground, which is not the same as getting it right."
            )
            outcomes.append(PlaceOutcome(name, h3, lat, lon, None, False, None, None))
            continue

        outcomes.append(
            PlaceOutcome(
                name=name,
                h3=h3,
                lat=lat,
                lon=lon,
                predicted=float(predicted[index]),
                flagged=bool(flagged[index]),
                observed_high_severity=bool(observed[index]),
                dnbr=float(dnbr[index]) if np.isfinite(dnbr[index]) else None,
            )
        )

    hits = int((flagged & observed).sum())
    misses = int((~flagged & observed).sum())
    false_alarms = int((flagged & ~observed).sum())

    log.info(
        "retrodiction of %s as of %s: %d cells, %d flagged, %d burned severely, %d hits, %d misses",
        fire_id,
        as_of,
        int(target.sum()),
        int(flagged.sum()),
        int(observed.sum()),
        hits,
        misses,
    )

    return Retrodiction(
        fire_id=fire_id,
        as_of=as_of,
        n_cells=int(target.sum()),
        trained_on_years=sorted({int(year) for year in years[train]}),
        trained_on_cells=int(train.sum()),
        threshold=threshold,
        places=outcomes,
        flagged_cells=int(flagged.sum()),
        observed_severe_cells=int(observed.sum()),
        hits=hits,
        misses=misses,
        false_alarms=false_alarms,
        notes=notes,
    )


#: Reported as context, never as a target. No model here was fitted on any of it.
STRUCTURE_LOSS_CONTEXT: dict[str, Any] = {
    "structures_lost_reported_august_2023": 189,
    "structures_lost_revised_2025": 303,
    "area_burned_ha": 13_970,
    "insured_loss_cad": 480_000_000,
    "sources": (
        "Central Okanagan Emergency Operations Centre (August 2023); Province of British "
        "Columbia investigation (2025); Insurance Bureau of Canada and CatIQ."
    ),
    "caveat": (
        "Structure loss is case-study context. The target this model was trained on is "
        "remotely sensed burn severity, and no claims or structure-loss data was used at any "
        "point. Nothing here predicts a loss count."
    ),
}
