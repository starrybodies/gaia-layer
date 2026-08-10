"""The experiment: four models, one gate, and no room to move the goalposts afterwards.

The claim under test is that landscape structure explains burn severity beyond what fire
weather and a static fuel map explain. Testing it means fitting the same learner four times
on four feature sets and asking whether the one with Component A in it wins by more than
the folds' own noise.

A note on the fourth model, which the specification did not ask for. The spec's gate is
`candidate` against `baseline_3` (weather and fuel type). But `candidate` also carries
terrain, and terrain is not Component A — if elevation and slope alone lifted the score,
`candidate` would win the spec's comparison while the index itself had contributed nothing.
So `baseline_4` adds terrain to `baseline_3`, and the difference between `candidate` and
`baseline_4` isolates Component A on its own. Both are reported. Where they disagree, the
stricter one is the true statement about the index, and the report says so rather than
quoting whichever is larger.

The learner is deliberately the same everywhere: identical hyperparameters, identical folds,
identical seed. The only thing that varies between models is which columns they can see.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from .metrics import Delta, Metrics, delta_with_ci, evaluate, pooled
from .splits import Fold

log = logging.getLogger(__name__)

#: Columns grouped by what they represent, so a model is defined by which groups it may see
#: rather than by a hand-maintained list that drifts from the data.
FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "weather": ("ffmc", "dmc", "dc", "isi", "bui", "fwi", "vpd_kpa"),
    "fuel": ("fbp_fuel_type",),
    "terrain": ("elevation_m", "slope_deg", "aspect_deg", "heat_load", "twi"),
    "structure": ("z_canopy_height", "z_crown_closure", "z_stand_age", "a_score"),
}

#: Which groups each model is allowed. The names are load-bearing: they appear in the report
#: and in the gate statement.
MODELS: dict[str, tuple[str, ...]] = {
    "baseline_1_fwi": ("weather",),
    "baseline_2_fbp": ("fuel",),
    "baseline_3_fwi_fbp": ("weather", "fuel"),
    "baseline_4_fwi_fbp_terrain": ("weather", "fuel", "terrain"),
    "candidate_with_component_a": ("weather", "fuel", "terrain", "structure"),
}

#: The comparison the specification names, and the stricter one that isolates the component.
GATE_BASELINE = "baseline_3_fwi_fbp"
ATTRIBUTION_BASELINE = "baseline_4_fwi_fbp_terrain"
CANDIDATE = "candidate_with_component_a"

#: Fixed for every model. Tuning per model would let the comparison measure hyperparameter
#: search rather than features.
HYPERPARAMETERS: dict[str, Any] = {
    "max_iter": 300,
    "learning_rate": 0.06,
    "max_leaf_nodes": 31,
    "min_samples_leaf": 40,
    "l2_regularization": 1.0,
    "early_stopping": True,
    "validation_fraction": 0.15,
    "random_state": 0,
}


@dataclass(frozen=True)
class ModelResult:
    name: str
    groups: tuple[str, ...]
    columns: list[str]
    folds: list[Metrics] = field(repr=False)
    out_of_fold_probability: np.ndarray = field(repr=False)
    summary: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ExperimentResult:
    models: dict[str, ModelResult]
    labels: np.ndarray = field(repr=False)
    evaluated: np.ndarray = field(repr=False)
    gate_delta: Delta | None = None
    attribution_delta: Delta | None = None
    calibration_delta: Delta | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def gate_passes(self) -> bool:
        """The specification's condition, evaluated exactly as it was written down.

        Positive delta AUC-PR whose interval excludes zero, and calibration no worse. The
        calibration term uses the same paired bootstrap, oriented so positive is better.
        """
        if self.gate_delta is None or self.calibration_delta is None:
            return False
        improves = self.gate_delta.point > 0.0 and self.gate_delta.excludes_zero
        calibration_holds = not (
            self.calibration_delta.point < 0.0 and self.calibration_delta.excludes_zero
        )
        return improves and calibration_holds


def columns_for(groups: tuple[str, ...], available: list[str]) -> list[str]:
    """Which of a model's permitted columns are actually present in the feature table."""
    wanted: list[str] = []
    for group in groups:
        wanted.extend(name for name in FEATURE_GROUPS[group] if name in available)
    return wanted


def _fit_predict(
    features: np.ndarray,
    labels: np.ndarray,
    folds: list[Fold],
    categorical: list[bool],
) -> tuple[np.ndarray, np.ndarray]:
    """Out-of-fold probabilities, and a mask of which rows ever got predicted.

    A cell that lands in a buffer for every fold is never trained on and never predicted;
    it is excluded from scoring rather than counted as a miss.
    """
    probability = np.full(labels.size, np.nan, dtype="float64")

    for index, fold in enumerate(folds):
        if fold.train.size == 0 or fold.test.size == 0:
            log.warning("fold %d has an empty side; skipping", index)
            continue
        if labels[fold.train].sum() in (0, fold.train.size):
            log.warning("fold %d trains on a single class; skipping", index)
            continue

        model = HistGradientBoostingClassifier(
            categorical_features=categorical if any(categorical) else None,
            **HYPERPARAMETERS,
        )
        model.fit(features[fold.train], labels[fold.train])
        probability[fold.test] = model.predict_proba(features[fold.test])[:, 1]

    return probability, np.isfinite(probability)


def run_experiment(
    table: dict[str, np.ndarray],
    labels: np.ndarray,
    folds: list[Fold],
    *,
    n_bootstrap: int = 2000,
    seed: int = 0,
) -> ExperimentResult:
    """Fit every model on the same folds and compare them on the cells they all predicted.

    `table` maps column name to values; only columns named in FEATURE_GROUPS are used, so
    the caller cannot accidentally leak the target or a cell identifier into a model.
    """
    labels = np.asarray(labels).astype(int)
    available = list(table.keys())
    notes: list[str] = []

    results: dict[str, ModelResult] = {}
    predicted_everywhere = np.ones(labels.size, dtype=bool)

    for name, groups in MODELS.items():
        columns = columns_for(groups, available)
        if not columns:
            notes.append(f"{name} has no available columns and was not fitted")
            continue

        matrix = np.column_stack([np.asarray(table[column], dtype="float64") for column in columns])
        categorical = [column in FEATURE_GROUPS["fuel"] for column in columns]

        probability, evaluated = _fit_predict(matrix, labels, folds, categorical)
        predicted_everywhere &= evaluated

        results[name] = ModelResult(
            name=name,
            groups=groups,
            columns=columns,
            folds=[],
            out_of_fold_probability=probability,
        )
        log.info("%s fitted on %d columns", name, len(columns))

    if not results:
        raise ValueError("no model could be fitted; the feature table has no usable columns")

    # Score every model on exactly the same cells, so the comparison is like for like.
    scored = predicted_everywhere & np.isfinite(labels)
    if labels[scored].sum() in (0, int(scored.sum())):
        raise ValueError("the commonly predicted cells contain only one class")

    for name, result in list(results.items()):
        metrics = [
            evaluate(
                labels[fold.test][scored[fold.test]],
                result.out_of_fold_probability[fold.test][scored[fold.test]],
            )
            for fold in folds
            if scored[fold.test].sum() > 0
            and 0 < labels[fold.test][scored[fold.test]].sum() < scored[fold.test].sum()
        ]
        overall = evaluate(labels[scored], result.out_of_fold_probability[scored])
        results[name] = ModelResult(
            name=result.name,
            groups=result.groups,
            columns=result.columns,
            folds=metrics,
            out_of_fold_probability=result.out_of_fold_probability,
            summary={
                **(pooled(metrics) if metrics else {}),
                "auc_roc_overall": overall.auc_roc,
                "auc_pr_overall": overall.auc_pr,
                "brier_overall": overall.brier,
                "prevalence": overall.prevalence,
                "calibration_max_gap": overall.calibration.max_gap,
                "n_scored": float(overall.n),
            },
        )

    def compare(baseline: str, metric: str) -> Delta | None:
        if CANDIDATE not in results or baseline not in results:
            return None
        return delta_with_ci(
            labels[scored],
            results[CANDIDATE].out_of_fold_probability[scored],
            results[baseline].out_of_fold_probability[scored],
            metric=metric,
            n_bootstrap=n_bootstrap,
            seed=seed,
        )

    excluded = int((~scored).sum())
    if excluded:
        notes.append(
            f"{excluded} cells were never predicted by every model and are excluded from scoring"
        )

    return ExperimentResult(
        models=results,
        labels=labels,
        evaluated=scored,
        gate_delta=compare(GATE_BASELINE, "auc_pr"),
        attribution_delta=compare(ATTRIBUTION_BASELINE, "auc_pr"),
        calibration_delta=compare(GATE_BASELINE, "brier"),
        notes=notes,
    )
