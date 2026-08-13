"""What is actually carrying the lift, and where it stops working.

The gate says Component A adds +0.1410 AUC-PR over fire weather and fuel type. That is one
number over one pooled set of out-of-fold predictions, and it is the most flattering true
thing that can be said. An underwriter's analyst will not ask whether the number is real;
they will ask *which part of it is real, and where*. This module answers that, and it is
built to be able to produce an unwelcome answer.

Four questions, in the order they matter:

**Which features carry it.** Permutation importance, shuffled within spatial folds rather
than across the whole table, because shuffling across folds moves a cell's value to another
part of the landscape and measures spatial autocorrelation rather than the feature.

One limitation has to travel with every number this produces: **permutation importance
measures what the model uses, not what carries signal.** A boosted tree fitted on a few
thousand rows will use a pure-noise column, and shuffling that column then changes its
predictions and registers as importance. Measured on a planted fixture where one feature
carries everything and the rest are random draws, a noise column still scored a drop of
0.025 AUC-PR against the real feature's 0.58. The ratio is the interpretable part, not the
absolute drop, and the thing that separates use from signal is the ablation below, which
refits without the group instead of scrambling it.

**Whether it survives a stricter split.** The gate uses spatially-blocked folds with a 3 km
buffer. Leave-one-fire-out is harder: every cell of a fire is held out together, so the model
cannot borrow a neighbouring cell of the same burn under the same weather. If the lift does
not survive that, what was measured is within-fire interpolation.

**Where it works.** Per fire, per year and per fuel type. A mean over the study area hides
the possibility that one large fire supplies the whole result. Strata with too few positives
to score are reported as unscorable rather than dropped, because a stratum the model cannot
be evaluated on is a hole in the claim, not an absence of one.

**Where it fails.** The cells it misses, characterised against the ones it catches.

Everything here is computed once, in Python, and persisted with the run that produced it.
Nothing downstream recomputes a headline number at render time: a statistic on a dashboard
that cannot be traced to a run is a statistic nobody can defend in a model review.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa

from .experiment import (
    CANDIDATE,
    FEATURE_GROUPS,
    GATE_BASELINE,
    HYPERPARAMETERS,
    MODELS,
    columns_for,
)
from .metrics import evaluate
from .splits import Fold

log = logging.getLogger(__name__)

#: How many times each feature is shuffled. Permutation importance is noisy at one repeat,
#: and the cost is linear, so this is the cheapest honesty available.
PERMUTATION_REPEATS = 5

#: Below this many positives a stratum cannot support an AUC-PR that means anything. Ten is
#: already thin; the point of the constant is that the threshold is stated and the strata
#: that fall under it are counted rather than quietly dropped.
MINIMUM_POSITIVES = 10

#: The smallest AUC-PR drop worth calling a finding. A ratio test on its own is not enough:
#: with few repeats the spread across shuffles can be tiny, so a drop of a thousandth passes
#: two sigma and gets reported as a feature that matters. Half a point of AUC-PR is inside
#: the noise of the whole experiment — the gate's own bootstrap interval is wider than that —
#: so anything under it is noise however consistently it reproduces.
MINIMUM_MEANINGFUL_DROP = 0.005


@dataclass(frozen=True)
class FeatureEffect:
    """One feature, and what the model loses without it."""

    name: str
    group: str
    auc_pr_drop: float
    auc_pr_drop_sd: float

    @property
    def matters(self) -> bool:
        """Larger than its own noise across repeats, *and* large enough to be worth saying."""
        return (
            self.auc_pr_drop >= MINIMUM_MEANINGFUL_DROP
            and self.auc_pr_drop > 2.0 * self.auc_pr_drop_sd
        )


@dataclass(frozen=True)
class StratumPerformance:
    """How the model does on one slice of the data, or why it cannot be said."""

    stratum: str
    value: str
    n: int
    positives: int
    prevalence: float
    auc_pr: float | None
    auc_pr_baseline: float | None
    scorable: bool
    reason: str = ""

    @property
    def lift(self) -> float | None:
        if self.auc_pr is None or self.auc_pr_baseline is None:
            return None
        return self.auc_pr - self.auc_pr_baseline


@dataclass(frozen=True)
class Diagnostics:
    """Everything the diagnostic found, including the parts that do not flatter the model."""

    n_cells: int
    prevalence: float
    auc_pr_candidate: float
    auc_pr_baseline: float
    features: list[FeatureEffect] = field(default_factory=list)
    groups: list[FeatureEffect] = field(default_factory=list)
    strata: list[StratumPerformance] = field(default_factory=list)
    leave_one_fire_out: dict[str, float] = field(default_factory=dict)
    misses: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _matrix(table: pa.Table, columns: list[str]) -> np.ndarray:
    return np.column_stack([np.asarray(table.column(name), dtype="float64") for name in columns])


def _fit(matrix: np.ndarray, labels: np.ndarray, columns: list[str], seed: int):  # type: ignore[no-untyped-def]
    from sklearn.ensemble import HistGradientBoostingClassifier

    categorical = [column in FEATURE_GROUPS["fuel"] for column in columns]
    model = HistGradientBoostingClassifier(
        categorical_features=categorical if any(categorical) else None,
        **{**HYPERPARAMETERS, "random_state": seed},
    )
    model.fit(matrix, labels)
    return model


def _out_of_fold(
    matrix: np.ndarray, labels: np.ndarray, folds: list[Fold], columns: list[str], seed: int
) -> np.ndarray:
    probability = np.full(labels.size, np.nan)
    for fold in folds:
        if fold.train.size == 0 or fold.test.size == 0:
            continue
        if labels[fold.train].sum() in (0, fold.train.size):
            continue
        model = _fit(matrix[fold.train], labels[fold.train], columns, seed)
        probability[fold.test] = model.predict_proba(matrix[fold.test])[:, 1]
    return probability


def _score(labels: np.ndarray, probability: np.ndarray) -> float | None:
    usable = np.isfinite(probability)
    if usable.sum() < 2:
        return None
    truth = labels[usable]
    if truth.sum() == 0 or truth.sum() == truth.size:
        return None
    return float(evaluate(truth, probability[usable]).auc_pr)


def permutation_importance(
    matrix: np.ndarray,
    labels: np.ndarray,
    folds: list[Fold],
    columns: list[str],
    *,
    repeats: int = PERMUTATION_REPEATS,
    seed: int = 0,
) -> list[FeatureEffect]:
    """Drop in pooled out-of-fold AUC-PR when each column is shuffled.

    Shuffled **within each fold**, not across the table. A cell's weather and structure are
    strongly autocorrelated in space, so shuffling globally moves a value to a different part
    of the landscape and the resulting drop measures how spatially structured the feature is
    rather than how much the model uses it. Within a fold, the shuffle stays inside one block
    of ground and the comparison is closer to fair.
    """
    rng = np.random.default_rng(seed)
    baseline = _score(labels, _out_of_fold(matrix, labels, folds, columns, seed))
    if baseline is None:
        return []

    group_of = {column: group for group, members in FEATURE_GROUPS.items() for column in members}

    effects: list[FeatureEffect] = []
    for position, name in enumerate(columns):
        drops: list[float] = []
        for _ in range(repeats):
            shuffled = matrix.copy()
            for fold in folds:
                block = shuffled[fold.test, position]
                shuffled[fold.test, position] = rng.permutation(block)
            score = _score(labels, _out_of_fold(shuffled, labels, folds, columns, seed))
            if score is not None:
                drops.append(baseline - score)

        if drops:
            effects.append(
                FeatureEffect(
                    name=name,
                    group=group_of.get(name, "unknown"),
                    auc_pr_drop=float(np.mean(drops)),
                    auc_pr_drop_sd=float(np.std(drops, ddof=1)) if len(drops) > 1 else 0.0,
                )
            )

    return sorted(effects, key=lambda effect: -effect.auc_pr_drop)


def group_ablation(
    table: pa.Table, labels: np.ndarray, folds: list[Fold], *, seed: int = 0
) -> list[FeatureEffect]:
    """What the candidate loses when a whole feature group is removed and refitted.

    Refitting rather than shuffling, because a boosted tree given a shuffled column can lean
    on a correlated survivor and report a smaller loss than the group is worth. Removing the
    group and starting again is the question actually being asked: would this model be worse
    if we had never collected these features?
    """
    available = list(table.column_names)
    full_columns = columns_for(MODELS[CANDIDATE], available)
    full = _score(
        labels, _out_of_fold(_matrix(table, full_columns), labels, folds, full_columns, seed)
    )
    if full is None:
        return []

    effects: list[FeatureEffect] = []
    for group in MODELS[CANDIDATE]:
        kept = tuple(other for other in MODELS[CANDIDATE] if other != group)
        columns = columns_for(kept, available)
        if not columns:
            continue
        without = _score(
            labels, _out_of_fold(_matrix(table, columns), labels, folds, columns, seed)
        )
        if without is None:
            continue
        effects.append(
            FeatureEffect(name=group, group=group, auc_pr_drop=full - without, auc_pr_drop_sd=0.0)
        )

    return sorted(effects, key=lambda effect: -effect.auc_pr_drop)


def leave_one_fire_out(table: pa.Table, labels: np.ndarray, *, seed: int = 0) -> dict[str, float]:
    """The strict split: every cell of a fire held out together.

    Spatially blocked folds with a 3 km buffer still let a model see most of a large fire
    while predicting the rest of it — same day, same weather, same crew. Holding out whole
    fires removes that, and the gap between the two numbers is the size of the within-fire
    interpolation the blocked folds were still permitting.
    """
    fires = np.asarray(table.column("fire_id"))
    available = list(table.column_names)

    answers: dict[str, float] = {}
    for name, groups in ((CANDIDATE, MODELS[CANDIDATE]), (GATE_BASELINE, MODELS[GATE_BASELINE])):
        columns = columns_for(groups, available)
        matrix = _matrix(table, columns)
        probability = np.full(labels.size, np.nan)

        for fire in np.unique(fires):
            test = fires == fire
            train = ~test
            if labels[train].sum() in (0, int(train.sum())) or test.sum() == 0:
                continue
            model = _fit(matrix[train], labels[train], columns, seed)
            probability[test] = model.predict_proba(matrix[test])[:, 1]

        score = _score(labels, probability)
        if score is not None:
            answers[name] = score

    if CANDIDATE in answers and GATE_BASELINE in answers:
        answers["delta"] = answers[CANDIDATE] - answers[GATE_BASELINE]
    return answers


def by_stratum(
    table: pa.Table,
    labels: np.ndarray,
    candidate: np.ndarray,
    baseline: np.ndarray,
    *,
    column: str,
    minimum_positives: int = MINIMUM_POSITIVES,
) -> list[StratumPerformance]:
    """Performance sliced by one column, with the unscorable slices named.

    A stratum with too few positives cannot support an AUC-PR, and reporting one anyway is
    how a model comes to look excellent on the four fires nobody checked. Those strata are
    returned with `scorable=False` and the reason, so the share of the archive the claim does
    *not* cover is visible rather than implied.
    """
    values = np.asarray(table.column(column))
    out: list[StratumPerformance] = []

    for value in np.unique(values):
        member = values == value
        truth = labels[member]
        positives = int(truth.sum())
        prevalence = float(truth.mean()) if truth.size else float("nan")

        if positives < minimum_positives or positives == truth.size:
            out.append(
                StratumPerformance(
                    stratum=column,
                    value=str(value),
                    n=int(member.sum()),
                    positives=positives,
                    prevalence=prevalence,
                    auc_pr=None,
                    auc_pr_baseline=None,
                    scorable=False,
                    reason=f"{positives} positives, below the floor of {minimum_positives}",
                )
            )
            continue

        out.append(
            StratumPerformance(
                stratum=column,
                value=str(value),
                n=int(member.sum()),
                positives=positives,
                prevalence=prevalence,
                auc_pr=_score(truth, candidate[member]),
                auc_pr_baseline=_score(truth, baseline[member]),
                scorable=True,
            )
        )

    return sorted(out, key=lambda row: -row.n)


def characterise_misses(
    table: pa.Table, labels: np.ndarray, candidate: np.ndarray, *, quantile: float = 0.80
) -> dict[str, Any]:
    """How the cells the model misses differ from the ones it catches.

    The comparison is between severe cells it flagged and severe cells it did not, holding
    the outcome constant. Comparing misses against the whole archive would mostly rediscover
    that severe cells differ from unburnt ones.
    """
    scored = np.isfinite(candidate) & np.isfinite(labels)
    if not scored.any():
        return {}

    threshold = float(np.quantile(candidate[scored], quantile))
    severe = scored & (labels == 1)
    hit = severe & (candidate >= threshold)
    missed = severe & (candidate < threshold)

    summary: dict[str, Any] = {
        "threshold": threshold,
        "severe_cells": int(severe.sum()),
        "hit": int(hit.sum()),
        "missed": int(missed.sum()),
        "recall": float(hit.sum() / severe.sum()) if severe.sum() else float("nan"),
        "differences": {},
    }

    for name in table.column_names:
        if name in {"h3", "fire_id", "high_severity"}:
            continue
        try:
            values = np.asarray(table.column(name), dtype="float64")
        except (pa.ArrowInvalid, ValueError):
            continue
        if not np.isfinite(values[hit]).any() or not np.isfinite(values[missed]).any():
            continue

        caught = float(np.nanmean(values[hit]))
        lost = float(np.nanmean(values[missed]))
        spread = float(np.nanstd(values[scored]))
        if spread > 0:
            summary["differences"][name] = {
                "hit_mean": caught,
                "missed_mean": lost,
                "standardised_gap": (lost - caught) / spread,
            }

    ranked = sorted(
        summary["differences"].items(),
        key=lambda item: -abs(item[1]["standardised_gap"]),
    )
    summary["most_different"] = [name for name, _ in ranked[:5]]
    return summary


def run_diagnostics(
    table: pa.Table, labels: np.ndarray, folds: list[Fold], *, seed: int = 0
) -> Diagnostics:
    """Every diagnostic, over one feature table. Slow on purpose; run once, persist, reuse."""
    available = list(table.column_names)
    candidate_columns = columns_for(MODELS[CANDIDATE], available)
    baseline_columns = columns_for(MODELS[GATE_BASELINE], available)

    candidate = _out_of_fold(
        _matrix(table, candidate_columns), labels, folds, candidate_columns, seed
    )
    baseline = _out_of_fold(_matrix(table, baseline_columns), labels, folds, baseline_columns, seed)

    log.info("permutation importance over %d features", len(candidate_columns))
    features = permutation_importance(
        _matrix(table, candidate_columns), labels, folds, candidate_columns, seed=seed
    )

    log.info("group ablation")
    groups = group_ablation(table, labels, folds, seed=seed)

    log.info("leave-one-fire-out")
    lofo = leave_one_fire_out(table, labels, seed=seed)

    strata: list[StratumPerformance] = []
    for column in ("fire_id", "fire_year", "fbp_fuel_type"):
        if column in available:
            strata.extend(by_stratum(table, labels, candidate, baseline, column=column))

    notes: list[str] = []
    # Per stratum dimension, not pooled across them. Summing scorable cells over fire, year
    # and fuel type at once counts every cell three times and produced "covers 9,836 of
    # 3,835" — a nonsense figure, and precisely the kind a diligence surface cannot carry.
    for dimension in sorted({row.stratum for row in strata}):
        rows = [row for row in strata if row.stratum == dimension]
        unscorable = [row for row in rows if not row.scorable]
        if not unscorable:
            continue
        covered = sum(row.n for row in rows if row.scorable)
        total = sum(row.n for row in rows)
        notes.append(
            f"{len(unscorable)} of {len(rows)} {dimension} strata could not be scored for "
            f"want of positives; the per-{dimension} claim covers {covered:,} of {total:,} cells"
        )

    return Diagnostics(
        n_cells=int(np.isfinite(labels).sum()),
        prevalence=float(np.mean(labels)),
        auc_pr_candidate=_score(labels, candidate) or float("nan"),
        auc_pr_baseline=_score(labels, baseline) or float("nan"),
        features=features,
        groups=groups,
        strata=strata,
        leave_one_fire_out=lofo,
        misses=characterise_misses(table, labels, candidate),
        notes=notes,
    )
