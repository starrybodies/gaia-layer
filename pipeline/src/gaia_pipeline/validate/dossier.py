"""Every number the diligence surface shows, computed here and persisted with its run.

The workbench this feeds is built for a model-validation team trying to break it. That
imposes one architectural rule and it is not negotiable: **the dashboard renders, it never
computes.** A figure worked out in a browser has no run behind it, no method record, no
source set, and cannot be reproduced by anyone who was not looking at the screen at the
time. Every statistic below is computed once, in Python, and written with the run id that
produced it — the same discipline the components are held to.

It imposes a second rule, which is editorial rather than architectural: **the analyst must
not find anything here that this file did not tell them first.** Three findings in the
current evidence are unflattering, and all three are sections of their own rather than
footnotes:

* Under leave-one-fire-out, the gate's own baseline scores 0.1039 against a prevalence of
  0.1064. A model that predicts the base rate everywhere scores prevalence. So the baseline
  the gate was written against has no demonstrated cross-fire skill, and the candidate's
  +0.1638 over it is not "beats a working model by 0.16".
* Refitting without fire weather, and refitting without fuel type, each *improve* the score
  slightly. Within this evaluated set the two variables the industry prices on carry no
  measurable lift.
* The composite Component A score, as a single column, has a permutation importance of
  -0.0030 while the three standardised structure inputs it is built from carry +0.0414,
  +0.0232 and -0.0011. The model does not use the composite; it uses the parts.

The dossier is a rendering of two persisted artifacts — the gate's `validation.json` and the
diagnostics run's `diagnostics.json` — and it recomputes only what is arithmetic on them:
counts, shares, orderings. Where a figure it needs is absent it says so and refuses, rather
than emitting a section with a null in it. A blank on a diligence screen is read as zero.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..eii.archive import MethodRecord, SourceRecord

#: Bumped when the wording or the selection of findings changes, so a screenshot can be tied
#: to the version of this file that produced it.
DOSSIER_VERSION = "1.0.0"

METHOD = MethodRecord(
    method_id="eii.diligence_dossier",
    name="Diligence dossier",
    citation=(
        "Assembled from the Component A gate (docs/plans/10-component-a-validation.md) and "
        "the diagnostics run described in pipeline/src/gaia_pipeline/validate/diagnostics.py"
    ),
    version=DOSSIER_VERSION,
    notes=(
        "Arithmetic on two persisted artifacts only: counts, shares and orderings. No model "
        "is fitted here and no metric is recomputed. Findings that weaken the claim are "
        "sections rather than footnotes, and are ordered before the findings that support it."
    ),
)


class MissingEvidenceError(RuntimeError):
    """A figure the dossier is required to show is not in the artifacts it was given.

    Raised rather than defaulted. A diligence surface with a blank where a confidence
    interval should be is worse than no surface, because a blank reads as a zero and a zero
    reads as a passed test.
    """


@dataclass(frozen=True)
class Figure:
    """One number, with the string to print it as and where it came from.

    `source` names the artifact and the path inside it. The run, method and source-set ids
    are stamped on at assembly, because the served payload has to satisfy the provenance
    guard figure by figure: a number that carries no way to be checked must not leave the
    layer, and "the ids are further up the document" is not a way to check one number.
    """

    label: str
    value: float | int | None
    display: str
    note: str = ""
    interval: dict[str, Any] | None = None
    source: str = ""


@dataclass(frozen=True)
class Section:
    """One block of the workbench. `disclosure` sections render first and render marked."""

    id: str
    title: str
    statement: str
    kind: str = "figures"
    disclosure: bool = False
    figures: list[Figure] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    caveat: str = ""


def _need(payload: dict[str, Any], *path: str) -> Any:
    """Fetch a required figure, or say which one is missing and stop."""
    cursor: Any = payload
    for key in path:
        if not isinstance(cursor, dict) or key not in cursor or cursor[key] is None:
            raise MissingEvidenceError(
                "the dossier requires " + ".".join(path) + " and the artifact does not carry it"
            )
        cursor = cursor[key]
    return cursor


def _signed(value: float, places: int = 4) -> str:
    return f"{value:+.{places}f}"


def _interval(delta: dict[str, Any]) -> dict[str, Any]:
    return {
        "low": float(delta["low"]),
        "high": float(delta["high"]),
        "excludes_zero": bool(delta["excludes_zero"]),
        "display": f"{float(delta['low']):+.4f} to {float(delta['high']):+.4f}",
    }


# ------------------------------------------------------------------ the sections


def _verdict(validation: dict[str, Any]) -> Section:
    gate = _need(validation, "gate_delta")
    attribution = _need(validation, "attribution_delta")
    calibration = _need(validation, "calibration_delta")
    verdict = _need(validation, "verdict")

    return Section(
        id="verdict",
        title="The gate, and what it did and did not test",
        statement=(
            f"{verdict}. The gate was written down before the first model was fitted, and it "
            "is a single pre-registered comparison rather than a search over comparisons. It "
            "tests one thing: whether adding Component A to a baseline of fire weather and "
            "fuel type improves ranking under spatially-blocked cross-validation without "
            "making calibration worse. It does not test whether the index is well calibrated, "
            "whether it generalises beyond the study area, or whether the baseline is any "
            "good — that last one is the subject of the next section."
        ),
        figures=[
            Figure(
                label="Gate delta, AUC-PR",
                value=float(gate["point"]),
                display=_signed(float(gate["point"])),
                interval=_interval(gate),
                note="candidate minus baseline_3, pooled out-of-fold, bootstrap 95%",
                source="validation.json#gate_delta",
            ),
            Figure(
                label="Attribution delta, AUC-PR",
                value=float(attribution["point"]),
                display=_signed(float(attribution["point"])),
                interval=_interval(attribution),
                note="candidate minus the same model refitted without the structure group",
                source="validation.json#attribution_delta",
            ),
            Figure(
                label="Calibration delta, ECE",
                value=float(calibration["point"]),
                display=_signed(float(calibration["point"])),
                interval=_interval(calibration),
                note=(
                    "positive means the candidate's expected calibration error is larger than "
                    "the baseline's; the gate requires only that it not be worse in a way the "
                    "interval can distinguish"
                ),
                source="validation.json#calibration_delta",
            ),
        ],
        caveat=_need(validation, "gate_statement"),
    )


def _cross_fire_skill(validation: dict[str, Any], diagnostics: dict[str, Any]) -> Section:
    """The disclosure. Computed, not asserted: the wording follows the comparison."""
    lofo = _need(diagnostics, "leave_one_fire_out")
    prevalence = float(_need(diagnostics, "prevalence"))
    baseline = float(_need(lofo, "baseline_3_fwi_fbp"))
    candidate = float(_need(lofo, "candidate_with_component_a"))
    blocked = float(_need(validation, "gate_delta", "point"))

    no_skill = baseline <= prevalence
    if no_skill:
        statement = (
            f"Under leave-one-fire-out the gate's baseline scores {baseline:.4f} AUC-PR "
            f"against a prevalence of {prevalence:.4f}. A model that predicts the base rate "
            "everywhere scores prevalence, so on this split the baseline has no demonstrated "
            "skill at all — it is at or below the no-skill line. That has a direct "
            f"consequence for how the headline is read: the candidate's {candidate:.4f} is a "
            f"real score and the {_signed(float(lofo['delta']))} margin over the baseline is "
            "arithmetic, but the margin is not evidence that Component A improves a working "
            "fire-weather model across fires. It is evidence that Component A has cross-fire "
            "skill and that this baseline, as configured on this evaluated set, does not. "
            "Anyone comparing these numbers against a commercial cat model's fire-weather "
            "component should treat the baseline here as a floor, not as a peer."
        )
    else:
        statement = (
            f"Under leave-one-fire-out the gate's baseline scores {baseline:.4f} AUC-PR "
            f"against a prevalence of {prevalence:.4f}, so it clears the no-skill line, and "
            f"the candidate's margin of {_signed(float(lofo['delta']))} is a margin over a "
            "baseline with demonstrated cross-fire skill."
        )

    return Section(
        id="cross_fire_skill",
        title="Read this before the headline: the baseline has no cross-fire skill",
        statement=statement,
        disclosure=True,
        figures=[
            Figure(
                label="Prevalence (the no-skill line)",
                value=prevalence,
                display=f"{prevalence:.4f}",
                note="share of evaluated cells that burned at high severity",
                source="diagnostics.json#prevalence",
            ),
            Figure(
                label="baseline_3 (FWI + FBP fuel), leave-one-fire-out",
                value=baseline,
                display=f"{baseline:.4f}",
                note=("below the no-skill line" if no_skill else "above the no-skill line"),
                source="diagnostics.json#leave_one_fire_out.baseline_3_fwi_fbp",
            ),
            Figure(
                label="Candidate with Component A, leave-one-fire-out",
                value=candidate,
                display=f"{candidate:.4f}",
                source="diagnostics.json#leave_one_fire_out.candidate_with_component_a",
            ),
            Figure(
                label="Leave-one-fire-out delta",
                value=float(lofo["delta"]),
                display=_signed(float(lofo["delta"])),
                note="margin over a baseline that is itself at the floor",
                source="diagnostics.json#leave_one_fire_out.delta",
            ),
            Figure(
                label="Spatially-blocked delta, for comparison",
                value=blocked,
                display=_signed(blocked),
                note="the gate's own split, 3 km buffer",
                source="validation.json#gate_delta.point",
            ),
        ],
        caveat=(
            "The two splits are not measured over the same set of cells: leave-one-fire-out "
            "holds out whole fires and can only score fires that carry positives, while the "
            "blocked folds drop cells inside the buffer. The deltas are comparable in "
            "direction and in rough size, not to the fourth decimal."
        ),
    )


def _split_comparison(validation: dict[str, Any], diagnostics: dict[str, Any]) -> Section:
    lofo = _need(diagnostics, "leave_one_fire_out")
    gate = _need(validation, "gate_delta")
    return Section(
        id="split_comparison",
        title="Does the lift survive a harder split",
        statement=(
            "The gate's spatially-blocked folds keep a 3 km buffer between train and test, "
            "which stops a cell borrowing from its immediate neighbour but still lets the "
            "model see other parts of the same fire under the same weather. Leave-one-fire-out "
            "removes that: every cell of a fire is held out together. The lift is larger under "
            "the harder split, not smaller, which is the direction that argues against the "
            "result being within-fire interpolation. It is not proof of that — see the "
            "coverage section for how few fires the harder split can actually score."
        ),
        kind="table",
        columns=["split", "delta AUC-PR", "95% interval", "what it holds out"],
        rows=[
            [
                "spatially blocked, 3 km buffer",
                _signed(float(gate["point"])),
                _interval(gate)["display"],
                "cells within a block, buffered",
            ],
            [
                "leave-one-fire-out",
                _signed(float(lofo["delta"])),
                "not bootstrapped",
                "every cell of one fire at a time",
            ],
        ],
        caveat=(
            "The leave-one-fire-out figures carry no confidence interval. They are a single "
            "pass over the fires that can be scored, and reporting one would imply a "
            "resampling that was not done."
        ),
    )


def _group_ablation(diagnostics: dict[str, Any]) -> Section:
    groups = _need(diagnostics, "groups")
    ordered = sorted(groups, key=lambda row: -float(row["auc_pr_drop"]))
    unhelpful = [row["name"] for row in ordered if float(row["auc_pr_drop"]) <= 0.0]

    statement = (
        "Each group is removed and the model refitted without it, rather than shuffled in "
        "place, so what is measured is what the group contributes and not merely what the "
        "fitted model happens to lean on. Structure carries the result. Terrain carries "
        "about half as much."
    )
    if unhelpful:
        statement += (
            " The uncomfortable half of this table is the bottom of it: "
            + " and ".join(unhelpful)
            + " cost nothing to remove — the refits without them score marginally *higher*. "
            "Within this evaluated set, of severe-burn cells inside fires that already "
            "happened, the two variables the industry prices on carry no measurable lift. "
            "That is a statement about conditional severity given that a fire occurred and "
            "reached this cell. It is emphatically not a statement that fire weather does not "
            "matter to fire: the labels here exist only where fire weather was already "
            "sufficient for a fire, so the range of weather in this table is narrow by "
            "construction."
        )

    return Section(
        id="group_ablation",
        title="What is carrying the lift",
        statement=statement,
        kind="table",
        columns=["group", "AUC-PR lost when removed and refitted"],
        rows=[[row["name"], _signed(float(row["auc_pr_drop"]))] for row in ordered],
        caveat=(
            "Refit-without, not permutation. The ablation is the measure that separates use "
            "from signal; the per-feature table below is permutation importance and measures "
            "only what the fitted model uses."
        ),
    )


def _a_score_decomposition(diagnostics: dict[str, Any]) -> Section:
    """The second disclosure: the composite column earns nothing, its parts earn the lift."""
    features = _need(diagnostics, "features")
    by_name = {row["name"]: row for row in features}
    if "a_score" not in by_name:
        raise MissingEvidenceError("the dossier requires a permutation entry for a_score")

    composite = float(by_name["a_score"]["auc_pr_drop"])
    parts = [row for row in features if row["group"] == "structure" and row["name"] != "a_score"]
    parts.sort(key=lambda row: -float(row["auc_pr_drop"]))
    best = max(float(row["auc_pr_drop"]) for row in parts) if parts else 0.0

    return Section(
        id="a_score_decomposition",
        title="The Component A composite column earns nothing; its inputs earn the lift",
        statement=(
            f"Permutation importance for `a_score`, the single composite column, is "
            f"{_signed(composite)} — shuffling it does not make the model worse, and slightly "
            "improves it, which is what a column the model has learned to ignore looks like. "
            f"The three standardised inputs it is built from carry up to {_signed(best)} "
            "each. The reading is that the model uses the parts directly and the composite "
            "adds nothing on top of them, so the attribution result belongs to the structure "
            "*group* and not to the composite score as a construct. Dropping the composite "
            "column from the feature table would cost nothing measurable. It is kept because "
            "it is the number the index reports and the model review should see the column it "
            "is being asked about, not a reconstruction of it."
        ),
        disclosure=True,
        kind="table",
        columns=["feature", "group", "AUC-PR drop when shuffled", "sd over folds"],
        rows=[
            [
                row["name"],
                row["group"],
                _signed(float(row["auc_pr_drop"])),
                f"{float(row['auc_pr_drop_sd']):.4f}",
            ]
            for row in [by_name["a_score"], *parts]
        ],
        caveat=(
            "Permutation importance measures what the model uses, not what carries signal. On "
            "a planted fixture where one feature carried everything and the rest were random "
            "draws, a pure-noise column still scored a drop of 0.025 against the real "
            "feature's 0.58. Read the ratios in this table, not the absolute drops, and read "
            "the group ablation above for the measure that refits rather than scrambles."
        ),
    )


def _coverage(diagnostics: dict[str, Any]) -> Section:
    strata = _need(diagnostics, "strata")
    dimensions: list[list[Any]] = []
    for dimension in sorted({row["stratum"] for row in strata}):
        rows = [row for row in strata if row["stratum"] == dimension]
        scorable = [row for row in rows if row["scorable"]]
        # Counted inside the dimension. Summing scorable cells across fire, year and fuel type
        # counts every cell three times, which is how an earlier artifact came to claim it
        # covered 9,836 of 3,835 cells.
        dimensions.append(
            [
                dimension,
                len(scorable),
                len(rows),
                sum(int(row["n"]) for row in scorable),
                sum(int(row["n"]) for row in rows),
            ]
        )

    years = [row for row in strata if row["stratum"] == "fire_year"]
    barren = sorted(row["value"] for row in years if int(row["positives"]) == 0)
    fires = next(row for row in dimensions if row[0] == "fire_id")

    return Section(
        id="coverage",
        title="How much of the claim is actually evidenced",
        statement=(
            f"The per-fire claim rests on {fires[1]} of {fires[2]} fires. The rest carry too "
            "few high-severity cells to score a precision-recall curve on at all, and they are "
            "named as unscorable rather than dropped, because a stratum the model cannot be "
            "evaluated on is a hole in the claim and not an absence of one. "
            + (
                "Three study years — "
                + ", ".join(str(value) for value in barren)
                + " — contain no high-severity cells in this area whatsoever, so they "
                "contribute nothing to any of the numbers above and cannot contradict them "
                "either."
                if barren
                else "Every study year contributes at least one high-severity cell."
            )
        ),
        kind="table",
        columns=[
            "stratum",
            "scorable strata",
            "strata",
            "cells in scorable strata",
            "cells",
        ],
        rows=dimensions,
        caveat=(
            "Coverage is counted within each stratum dimension. The same cell appears once "
            "under its fire, once under its year and once under its fuel type; summing across "
            "the three would count it three times and produce a figure larger than the table."
        ),
    )


def _per_stratum(diagnostics: dict[str, Any]) -> Section:
    strata = _need(diagnostics, "strata")
    rows: list[list[Any]] = []
    for row in sorted(strata, key=lambda item: (item["stratum"], -int(item["n"]))):
        rows.append(
            [
                row["stratum"],
                str(row["value"]),
                int(row["n"]),
                int(row["positives"]),
                f"{float(row['prevalence']):.4f}",
                "unscorable" if not row["scorable"] else f"{float(row['auc_pr']):.4f}",
                "" if row["auc_pr_baseline"] is None else f"{float(row['auc_pr_baseline']):.4f}",
                row.get("reason") or "",
            ]
        )
    return Section(
        id="per_stratum",
        title="Every stratum, including the ones that could not be scored",
        statement=(
            "One large fire supplying the whole result would be invisible in a pooled mean. "
            "This is the table that would show it. Unscorable strata are listed with the "
            "reason, at full length rather than truncated to the interesting rows."
        ),
        kind="table",
        columns=[
            "stratum",
            "value",
            "cells",
            "positives",
            "prevalence",
            "candidate AUC-PR",
            "baseline AUC-PR",
            "why unscorable",
        ],
        rows=rows,
    )


def _where_it_fails(diagnostics: dict[str, Any]) -> Section:
    misses = _need(diagnostics, "misses")
    differences = _need(misses, "differences")
    ranked = _need(misses, "most_different")

    isi = differences.get("isi")
    statement = (
        f"At the threshold that reproduces the observed positive rate, the index catches "
        f"{int(misses['hit'])} of {int(misses['severe_cells'])} cells that burned at high "
        f"severity and misses {int(misses['missed'])} — a recall of "
        f"{float(misses['recall']):.3f}. The misses are not scattered."
    )
    if isi is not None:
        statement += (
            f" Initial Spread Index averages {float(isi['missed_mean']):.2f} in the cells it "
            f"misses against {float(isi['hit_mean']):.2f} in the cells it catches. The index fails most "
            "where fire weather is most extreme, which is exactly where an underwriter needs "
            "it to hold. Read plainly: this measures landscape condition, and when spread is "
            "driven by wind and dryness rather than by the state of the ground, landscape "
            "condition stops being the binding constraint. A book priced on this alone would "
            "be under-reserved for the worst weather days."
        )

    rows = []
    for name in ranked:
        entry = differences.get(name)
        if entry is None:
            continue
        rows.append(
            [
                name,
                f"{float(entry['missed_mean']):.3f}",
                f"{float(entry['hit_mean']):.3f}",
                _signed(float(entry["standardised_gap"]), 3),
            ]
        )

    return Section(
        id="where_it_fails",
        title="Where it fails, and in which direction",
        statement=statement,
        disclosure=True,
        kind="table",
        columns=["feature", "mean in missed cells", "mean in caught cells", "standardised gap"],
        rows=rows,
        caveat=(
            f"Threshold {float(misses['threshold']):.4f}, chosen to reproduce the observed "
            "positive rate rather than to flatter recall. A different threshold moves every "
            "number in this section."
        ),
    )


def _models(validation: dict[str, Any]) -> Section:
    models = _need(validation, "models")
    rows = []
    for name in sorted(models):
        model = models[name]
        rows.append(
            [
                name,
                ", ".join(model["features"]),
                f"{float(model['auc_pr_overall']):.4f}",
                f"{float(model['auc_roc_overall']):.4f}",
                f"{float(model['brier_overall']):.4f}",
                f"{float(model['calibration_expected_gap']):.4f}",
                f"{float(model['calibration_max_gap']):.4f}"
                f" (n={int(model['calibration_max_gap_count'])})",
            ]
        )
    return Section(
        id="models",
        title="Every model that was fitted, not only the two the gate compares",
        statement=(
            "Five models, all fitted on the same folds with the same hyperparameters. The "
            "gate compares two of them. The other three are here so that the choice of "
            "baseline can be argued with rather than taken on trust."
        ),
        kind="table",
        columns=[
            "model",
            "feature groups",
            "AUC-PR",
            "AUC-ROC",
            "Brier",
            "ECE",
            "worst-bin gap",
        ],
        rows=rows,
        caveat=(
            "ECE is the column models are compared on; it weights every probability bin by "
            "how many cells fall in it. The worst-bin gap is shown beside its cell count "
            "because an unweighted worst-bin figure rewards a model for never making a "
            "confident prediction."
        ),
    )


def _exclusions(validation: dict[str, Any], diagnostics: dict[str, Any]) -> Section:
    excluded = validation.get("excluded") or {}
    unrecorded = validation.get("exclusions_unrecorded_for") or []
    leakage = _need(validation, "leakage")

    rows: list[list[Any]] = [[key, value] for key, value in sorted(excluded.items())]
    rows.append(["evaluated cells", int(_need(diagnostics, "n_cells"))])
    rows.append(["folds", int(_need(validation, "folds"))])
    rows.append(["buffer between train and test, m", int(float(leakage["buffer_m"]))])
    rows.append(
        [
            "measured minimum train-test distance, m",
            round(float(leakage["minimum_train_test_distance_m"]), 3),
        ]
    )
    rows.append(["leakage check holds", "yes" if float(leakage["holds"]) >= 1.0 else "NO"])

    statement = (
        "The buffer is not asserted, it is measured: the closest a training cell gets to a "
        "test cell is reported above, and it is at or beyond the buffer the split claims."
    )
    if unrecorded:
        statement += (
            " Against that, the exclusion bookkeeping is incomplete. No per-year exclusion "
            "counts were recorded for "
            + ", ".join(str(year) for year in unrecorded)
            + ", so this dossier cannot tell you how many cells were dropped before scoring "
            "in those years or why. That is a gap in the evidence, not a statement that "
            "nothing was dropped."
        )

    return Section(
        id="exclusions",
        title="Exclusions, leakage and what the bookkeeping does not cover",
        statement=statement,
        kind="table",
        columns=["item", "value"],
        rows=rows,
    )


def _notes(diagnostics: dict[str, Any]) -> Section:
    notes = diagnostics.get("notes") or []
    return Section(
        id="notes",
        title="Notes carried from the diagnostics run",
        statement=("Written by the run itself rather than by this file, and reproduced verbatim."),
        kind="list",
        rows=[[note] for note in notes],
        columns=["note"],
    )


# ------------------------------------------------------------------ assembly


def build_dossier(
    validation: dict[str, Any],
    diagnostics: dict[str, Any],
    *,
    run_id: str,
    source_set_id: str,
    generated: datetime | None = None,
) -> dict[str, Any]:
    """The whole workbench payload, ordered so the disclosures come first.

    Ordering is part of the contract. A finding that weakens the claim placed after the
    finding that supports it has been buried, whatever the words around it say.
    """
    sections = [
        _cross_fire_skill(validation, diagnostics),
        _verdict(validation),
        _split_comparison(validation, diagnostics),
        _group_ablation(diagnostics),
        _a_score_decomposition(diagnostics),
        _where_it_fails(diagnostics),
        _coverage(diagnostics),
        _per_stratum(diagnostics),
        _models(validation),
        _exclusions(validation, diagnostics),
        _notes(diagnostics),
    ]
    disclosures = [section for section in sections if section.disclosure]
    rest = [section for section in sections if not section.disclosure]

    return {
        "generated": (generated or datetime.now(UTC)).isoformat(timespec="seconds"),
        "run_id": run_id,
        "method_id": METHOD.method_id,
        "method_version": METHOD.version,
        "source_set_id": source_set_id,
        "verdict": validation.get("verdict"),
        "gate_statement": validation.get("gate_statement"),
        "disclosure_count": len(disclosures),
        "sections": [
            _as_dict(section, run_id=run_id, source_set_id=source_set_id)
            for section in [*disclosures, *rest]
        ],
    }


def _as_dict(section: Section, *, run_id: str, source_set_id: str) -> dict[str, Any]:
    return {
        "id": section.id,
        "title": section.title,
        "statement": section.statement,
        "kind": section.kind,
        "disclosure": section.disclosure,
        "caveat": section.caveat,
        "columns": section.columns,
        "rows": section.rows,
        "figures": [
            {
                "label": figure.label,
                "value": figure.value,
                "display": figure.display,
                "note": figure.note,
                "interval": figure.interval,
                "source": figure.source,
                "run_id": run_id,
                "method_id": METHOD.method_id,
                "source_set_id": source_set_id,
            }
            for figure in section.figures
        ],
    }


def artifact_sources(validation_path: Path, diagnostics_path: Path) -> list[SourceRecord]:
    """The two persisted artifacts, recorded as sources so the dossier resolves like a fact."""
    return [
        SourceRecord(
            dataset="EII Component A gate",
            version=str(validation_path.name),
            access_route="local-artifact",
            uri=str(validation_path),
            citation="docs/plans/10-component-a-validation.md",
            native_timestep="one gate run",
        ),
        SourceRecord(
            dataset="EII diagnostics",
            version=str(diagnostics_path.name),
            access_route="local-artifact",
            uri=str(diagnostics_path),
            citation="pipeline/src/gaia_pipeline/validate/diagnostics.py",
            native_timestep="one diagnostics run",
        ),
    ]


def write_dossier(directory: Path) -> Path:
    """Build the dossier from the archive's own artifacts and register the run behind it."""
    from ..eii.archive import (
        finish_run,
        open_catalog,
        register_method,
        register_sources,
        start_run,
    )

    validation_path = directory / "validation.json"
    diagnostics_path = directory / "diagnostics.json"
    validation = json.loads(validation_path.read_text())
    diagnostics = json.loads(diagnostics_path.read_text())

    conn = open_catalog(directory / "catalog.duckdb")
    register_method(conn, METHOD)
    source_set_id = register_sources(conn, artifact_sources(validation_path, diagnostics_path))
    run_id = start_run(
        conn,
        command="write_dossier",
        component="diligence",
        method_id=METHOD.method_id,
        source_set_id=source_set_id,
        parameters={"validation": str(validation_path), "diagnostics": str(diagnostics_path)},
    )
    try:
        payload = build_dossier(validation, diagnostics, run_id=run_id, source_set_id=source_set_id)
        path = directory / "dossier.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    except Exception as error:
        finish_run(conn, run_id, status="failed", error=str(error))
        raise
    finish_run(conn, run_id, status="succeeded")
    return path
