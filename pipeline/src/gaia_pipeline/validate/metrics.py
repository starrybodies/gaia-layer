"""Metrics, and the interval around them.

A point estimate of AUC is not evidence. The gate this project turns on is whether adding a
component to a baseline improves discrimination by more than the folds' own variability, so
every number here arrives with an interval, and the interval is computed by resampling the
same cells for both models rather than comparing two independent confidence bands. Paired
resampling is the difference between asking "could these two models differ" and "does this
model beat that one on this ground", and only the second question is the one being asked.

Discrimination is reported as AUC-PR alongside AUC-ROC because high-severity cells are the
minority class. ROC flatters a model on imbalanced data by rewarding it for the true
negatives it gets for free; precision-recall does not.

Calibration is reported because an underwriter cannot price a score that discriminates well
and is systematically wrong about level.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    roc_auc_score,
)

DEFAULT_BOOTSTRAP = 2000


@dataclass(frozen=True)
class Calibration:
    """Predicted probability against observed frequency, in bins.

    Three summaries, because one of them on its own misleads. `max_gap` is the worst a
    model gets, which is what an underwriter asks about first, but it is a maximum over
    bins that can hold anything from one cell to three thousand, and a boosted tree that
    spreads its predictions across the whole range is scored on a sparser tail than a
    timid model that never leaves the low bins. `max_gap_count` says how many cells stood
    behind that worst number, and `expected_gap` weights every bin by its population, so
    two models can be compared without the comparison turning on their emptiest bin.
    """

    bin_centre: list[float]
    predicted: list[float]
    observed: list[float]
    count: list[int]

    @property
    def _populated(self) -> list[tuple[float, int]]:
        return [
            (abs(p - o), n)
            for p, o, n in zip(self.predicted, self.observed, self.count, strict=True)
            if n > 0
        ]

    @property
    def max_gap(self) -> float:
        """The largest distance between what was promised and what happened."""
        gaps = self._populated
        return max(gap for gap, _ in gaps) if gaps else float("nan")

    @property
    def max_gap_count(self) -> int:
        """How many cells sat in the bin that produced `max_gap`."""
        gaps = self._populated
        return max(gaps)[1] if gaps else 0

    @property
    def expected_gap(self) -> float:
        """Population-weighted mean gap: the expected calibration error."""
        gaps = self._populated
        total = sum(n for _, n in gaps)
        if total == 0:
            return float("nan")
        return sum(gap * n for gap, n in gaps) / total


@dataclass(frozen=True)
class Metrics:
    auc_roc: float
    auc_pr: float
    brier: float
    prevalence: float
    n: int
    calibration: Calibration = field(repr=False)


@dataclass(frozen=True)
class Delta:
    """A paired difference between two models on the same cells."""

    metric: str
    point: float
    low: float
    high: float
    n_bootstrap: int

    @property
    def excludes_zero(self) -> bool:
        """The gate's condition, stated once so nothing downstream re-derives it."""
        return self.low > 0.0 or self.high < 0.0

    def __str__(self) -> str:
        return f"{self.metric} {self.point:+.4f} (95% CI {self.low:+.4f} to {self.high:+.4f})"


#: 95%, two-sided.
_Z = 1.959963984540054


def wilson_interval(rate: float, n: int) -> tuple[float, float]:
    """A 95% interval on an observed frequency, by Wilson's score method.

    The normal approximation collapses to a zero-width interval when a bin observes no
    events at all, which is exactly the bin a calibration argument turns on. Wilson does
    not, so it is the one that can answer whether a bin holding twenty-three cells is
    genuinely over-confident or merely small.
    """
    if n <= 0 or not np.isfinite(rate):
        return (float("nan"), float("nan"))

    denominator = 1.0 + _Z * _Z / n
    centre = (rate + _Z * _Z / (2 * n)) / denominator
    half = (_Z / denominator) * float(np.sqrt(rate * (1.0 - rate) / n + _Z * _Z / (4 * n * n)))
    return (max(0.0, centre - half), min(1.0, centre + half))


def calibration_curve(y_true: np.ndarray, y_prob: np.ndarray, *, bins: int = 10) -> Calibration:
    """Observed frequency per predicted-probability bin.

    Empty bins are kept rather than dropped. A model that never predicts above 0.4 is saying
    something, and silently omitting the top bins hides it.
    """
    edges = np.linspace(0.0, 1.0, bins + 1)
    index = np.clip(np.digitize(y_prob, edges[1:-1]), 0, bins - 1)

    centre, predicted, observed, count = [], [], [], []
    for bin_id in range(bins):
        members = index == bin_id
        n = int(members.sum())
        centre.append(float((edges[bin_id] + edges[bin_id + 1]) / 2))
        count.append(n)
        predicted.append(float(y_prob[members].mean()) if n else float("nan"))
        observed.append(float(y_true[members].mean()) if n else float("nan"))

    return Calibration(bin_centre=centre, predicted=predicted, observed=observed, count=count)


def evaluate(y_true: np.ndarray, y_prob: np.ndarray, *, bins: int = 10) -> Metrics:
    """Discrimination and calibration for one model on one test set."""
    y_true = np.asarray(y_true).astype(int)
    y_prob = np.asarray(y_prob, dtype="float64")

    if y_true.size != y_prob.size:
        raise ValueError("labels and predictions must be the same length")
    if y_true.size == 0:
        raise ValueError("cannot evaluate an empty test set")

    positives = int(y_true.sum())
    if positives in (0, y_true.size):
        raise ValueError(
            "a test set with one class has no discrimination to measure; "
            "report the fold as unusable rather than scoring it"
        )

    return Metrics(
        auc_roc=float(roc_auc_score(y_true, y_prob)),
        auc_pr=float(average_precision_score(y_true, y_prob)),
        brier=float(brier_score_loss(y_true, y_prob)),
        prevalence=float(positives / y_true.size),
        n=int(y_true.size),
        calibration=calibration_curve(y_true, y_prob, bins=bins),
    )


_SCORERS = {
    "auc_pr": average_precision_score,
    "auc_roc": roc_auc_score,
    "brier": brier_score_loss,
}


def delta_with_ci(
    y_true: np.ndarray,
    prob_candidate: np.ndarray,
    prob_baseline: np.ndarray,
    *,
    metric: str = "auc_pr",
    n_bootstrap: int = DEFAULT_BOOTSTRAP,
    seed: int = 0,
) -> Delta:
    """Paired bootstrap of the difference between two models on identical cells.

    Both models are scored on the same resampled cells every iteration. Resampling them
    independently would add variance that has nothing to do with the difference and would
    widen the interval until nothing could ever be shown.

    For Brier, lower is better, so the difference is oriented as baseline minus candidate.
    Every metric here therefore reads the same way: positive means the candidate won.
    """
    if metric not in _SCORERS:
        raise ValueError(f"unknown metric {metric!r}; expected one of {sorted(_SCORERS)}")

    y_true = np.asarray(y_true).astype(int)
    scorer = _SCORERS[metric]
    lower_is_better = metric == "brier"

    def difference(indices: np.ndarray) -> float | None:
        labels = y_true[indices]
        if labels.sum() in (0, labels.size):
            return None
        candidate = float(scorer(labels, prob_candidate[indices]))
        baseline = float(scorer(labels, prob_baseline[indices]))
        return baseline - candidate if lower_is_better else candidate - baseline

    point = difference(np.arange(y_true.size))
    if point is None:
        raise ValueError("cannot compare models on a single-class test set")

    rng = np.random.default_rng(seed)
    draws: list[float] = []
    for _ in range(n_bootstrap):
        sample = rng.integers(0, y_true.size, y_true.size)
        value = difference(sample)
        if value is not None:
            draws.append(value)

    if len(draws) < n_bootstrap // 2:
        raise ValueError(
            "too few usable bootstrap resamples; the positive class is too rare "
            "for this test set to support an interval"
        )

    low, high = np.percentile(draws, [2.5, 97.5])
    return Delta(
        metric=metric, point=float(point), low=float(low), high=float(high), n_bootstrap=len(draws)
    )


def pooled(metrics: list[Metrics]) -> dict[str, float]:
    """Fold metrics summarised as a mean and a spread.

    The spread across folds is reported because it is often the more interesting number:
    a model that scores 0.72 on every fold and a model that scores 0.45 and 0.99 have the
    same mean and are not the same model.
    """
    if not metrics:
        raise ValueError("no folds to pool")

    return {
        "auc_roc_mean": float(np.mean([m.auc_roc for m in metrics])),
        "auc_roc_sd": float(np.std([m.auc_roc for m in metrics])),
        "auc_pr_mean": float(np.mean([m.auc_pr for m in metrics])),
        "auc_pr_sd": float(np.std([m.auc_pr for m in metrics])),
        "brier_mean": float(np.mean([m.brier for m in metrics])),
        "brier_sd": float(np.std([m.brier for m in metrics])),
        "prevalence_mean": float(np.mean([m.prevalence for m in metrics])),
        "n_total": int(sum(m.n for m in metrics)),
    }
