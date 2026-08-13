"""The one-page summary an actuary can argue with.

The audience for this file is someone who prices risk for a living, has seen a great many
models presented as better than they were, and will decide in about ninety seconds whether
this one is worth a second meeting. What convinces that reader is not the headline number.
It is being told, without having to ask, what was predicted, against what truth, on what
ground, with what left out, and how the leakage was controlled.

So the report leads with the gate verdict in the gate's own words, states the negative
result as plainly as it would state a positive one, and puts the exclusions and the
uncertainty above the metrics table rather than in a footnote.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .experiment import (
    ATTRIBUTION_BASELINE,
    CANDIDATE,
    GATE_BASELINE,
    ExperimentResult,
)
from .metrics import Delta, wilson_interval

GATE_STATEMENT = (
    "Component A, added to baseline_3 (FWI + FBP fuel type), produces a positive "
    "delta AUC-PR whose bootstrap 95% confidence interval excludes zero under "
    "spatially-blocked cross-validation with 2-3 km buffers, and does not worsen "
    "calibration."
)


def _verdict(result: ExperimentResult) -> str:
    if result.gate_delta is None:
        return "NOT EVALUATED"
    return "PASS" if result.gate_passes else "FAIL"


def _delta_line(label: str, delta: Delta | None) -> str:
    if delta is None:
        return f"- **{label}:** not evaluated"
    significance = "excludes zero" if delta.excludes_zero else "includes zero"
    return f"- **{label}:** {delta.point:+.4f} (95% CI {delta.low:+.4f} to {delta.high:+.4f}, {significance})"


def _calibration_section(result: ExperimentResult, add: Any) -> None:
    """The candidate's own reliability curve, band by band.

    The models table gives three numbers for calibration and none of them says *where* a
    model is wrong. An underwriter prices the tail, so being told that the top band promises
    0.75 and delivers 0.22 is worth more than any summary statistic, and being told how many
    cells that band holds is what stops the observation being over-read.
    """
    model = result.models.get(CANDIDATE)
    if model is None or model.calibration is None:
        return
    curve = model.calibration

    add("## Where the candidate's probabilities are wrong")
    add("")
    add("| predicted band | cells | mean predicted | observed | 95% interval on observed |")
    add("|---|---|---|---|---|")

    over: list[tuple[str, int]] = []
    under: list[tuple[str, int]] = []
    for centre, predicted, observed, count in zip(
        curve.bin_centre, curve.predicted, curve.observed, curve.count, strict=True
    ):
        if count == 0:
            continue
        low, high = wilson_interval(observed, count)
        band = f"{centre - 0.05:.2f}-{centre + 0.05:.2f}"
        add(f"| {band} | {count:,} | {predicted:.3f} | {observed:.3f} | {low:.3f} to {high:.3f} |")
        if predicted > high:
            over.append((band, count))
        elif predicted < low:
            under.append((band, count))
    add("")

    total = sum(curve.count)

    def _describe(bands: list[tuple[str, int]], direction: str) -> None:
        if not bands:
            add(f"- **{direction}:** no band, by this test.")
            return
        cells = sum(count for _, count in bands)
        names = ", ".join(band for band, _ in bands)
        share = 100.0 * cells / total if total else float("nan")
        add(f"- **{direction}:** {names} — {cells:,} cells, {share:.1f}% of those scored.")

    add(
        "A band is listed below when its mean predicted probability falls outside the "
        "interval on its own observed frequency, which is to say the disagreement is larger "
        "than the band's population explains."
    )
    add("")
    _describe(over, "Promises more than it delivers")
    _describe(under, "Delivers more than it promises")
    add("")
    add(
        "The gate is a statement about ranking, and ranking is what AUC-PR measures: the "
        "order the candidate puts cells in is better than the baselines' by more than the "
        "folds' noise, and its Brier score and ECE are the best of the five. The levels are "
        "a separate claim and a weaker one. A cell scored above the bands listed as "
        "over-confident should be read as *high risk relative to the others*, not as a "
        "probability to multiply by an exposure."
    )
    add("")
    add(
        "The fix is a monotone recalibration fitted inside each training fold, and it is "
        "deliberately not applied here. Recalibrating changes the pooled out-of-fold "
        "probabilities, which changes the gate comparison, and the gate was written before "
        "the first model was fitted. Correcting the levels after seeing the verdict would "
        "make the verdict unfalsifiable. It belongs on the served score, where it can be "
        "fitted, held out and reported on its own terms, not inside the experiment that is "
        "supposed to be able to fail."
    )
    add("")


def write_report(
    result: ExperimentResult,
    path: Path,
    *,
    context: dict[str, Any] | None = None,
) -> Path:
    """Write the validation summary. Returns the path, for the caller to log."""
    context = context or {}
    verdict = _verdict(result)

    lines: list[str] = []
    add = lines.append

    add("# Component A validation summary")
    add("")
    add(f"**Generated:** {datetime.now(UTC).isoformat(timespec='seconds')}")
    add(f"**Study area:** {context.get('study_area', 'Okanagan and southern interior')}")
    add(f"**Fire years:** {context.get('years', 'not stated')}")
    add("")

    add("## The gate")
    add("")
    add("Written before the first model was fitted, and not adjusted afterwards:")
    add("")
    add(f"> {GATE_STATEMENT}")
    add("")
    add(f"### Verdict: {verdict}")
    add("")
    add(_delta_line("Gate comparison (candidate vs baseline_3)", result.gate_delta))
    add(_delta_line("Calibration (Brier, positive means better)", result.calibration_delta))
    add("")
    add(
        "The attribution comparison below is the stricter test and was not required by the "
        "specification. It gives terrain to both sides, so it isolates Component A from the "
        "elevation and slope that the candidate also carries. Where the two disagree, this "
        "is the one that describes the index."
    )
    add("")
    add(
        _delta_line(
            "Attribution (candidate vs baseline_4, terrain on both sides)", result.attribution_delta
        )
    )
    add("")

    if verdict == "FAIL":
        add(
            "**This is a negative result and is reported as one.** Component A did not "
            "clear the bar that was set for it before the data was seen. The honest reading "
            "is that vegetation structure deviation, measured this way at this resolution, "
            "does not add discrimination over fire weather and fuel type on this ground. "
            "What follows is the evidence for that statement rather than a search for a "
            "framing in which the result looks better."
        )
        add("")

    add("## What was predicted, against what truth")
    add("")
    add("- **Question:** given that this 500 m cell burned, did it burn at high severity?")
    add(
        "- **Label:** mean dNBR across the cell at or above 660, the Key and Benson (2006) "
        "high-severity break, from Sentinel-2 growing-season composites one year either side "
        "of the fire."
    )
    add(
        "- **Truth is a proxy.** This is remotely sensed severity, not insurance loss. No "
        "claims data was used and none is implied."
    )
    add(
        "- **Cells only inside fire perimeters.** Nothing outside NBAC's mapped footprint "
        "is scored, so this says nothing about where fires start."
    )
    add("")

    add("## Leakage controls")
    add("")
    for key in ("folds", "buffer_km", "block_size_km", "minimum_train_test_distance_m"):
        if key in context:
            add(f"- **{key.replace('_', ' ')}:** {context[key]}")
    add(
        "- Folds are blocks of ground, not cells. A random split of the same data leaves "
        "training cells adjacent to test cells and inflates AUC substantially; that number "
        "is not reported here because it would be misleading."
    )
    add("")

    add("## What was left out")
    add("")
    if "minimum_fire_ha" in context:
        add(
            f"- **Fires below {context['minimum_fire_ha']:,.0f} ha:** excluded by a stated "
            "floor rather than a silent one. Small fires carry too few cells for a "
            "within-fire severity comparison to mean anything."
        )
    for reason, count in (context.get("excluded") or {}).items():
        add(f"- **{reason.replace('_', ' ')}:** {count:,}")
    if context.get("exclusions_unrecorded_for"):
        years_missing = ", ".join(str(year) for year in context["exclusions_unrecorded_for"])
        add(
            f"- **Not recorded:** the exclusion counts for {years_missing} were not written "
            "when those labels were built and cannot be recovered without rebuilding them. "
            "They are missing from the totals above rather than counted as zero."
        )
    for note in result.notes:
        add(f"- {note}")
    add("")

    add("## Models")
    add("")
    add("| model | features | AUC-PR | AUC-ROC | Brier | ECE | worst bin | cells in it |")
    add("|---|---|---|---|---|---|---|---|")
    for name, model in result.models.items():
        summary = model.summary
        marker = " **(candidate)**" if name == CANDIDATE else ""
        if name == GATE_BASELINE:
            marker = " *(gate baseline)*"
        if name == ATTRIBUTION_BASELINE:
            marker = " *(attribution baseline)*"
        add(
            f"| `{name}`{marker} | {', '.join(model.groups)} | "
            f"{summary.get('auc_pr_overall', float('nan')):.4f} | "
            f"{summary.get('auc_roc_overall', float('nan')):.4f} | "
            f"{summary.get('brier_overall', float('nan')):.4f} | "
            f"{summary.get('calibration_expected_gap', float('nan')):.3f} | "
            f"{summary.get('calibration_max_gap', float('nan')):.3f} | "
            f"{int(summary.get('calibration_max_gap_count', 0)):,} |"
        )
    add("")
    add(
        "Calibration is reported three ways because the obvious one misleads. The worst "
        "bin is what an underwriter asks about first, but it is a maximum over ten bins "
        "that hold anything from a handful of cells to thousands, and a model that spreads "
        "its predictions across the full range is judged on a sparser tail than one that "
        "never leaves the bottom. The cell count beside it says how much of the data that "
        "number describes. ECE weights every bin by its population and is the column to "
        "compare models on."
    )
    add("")

    first = next(iter(result.models.values()), None)
    if first is not None:
        add(
            f"Scored on {int(first.summary.get('n_scored', 0)):,} cells with a "
            f"high-severity prevalence of {first.summary.get('prevalence', float('nan')):.3f}. "
            "AUC-PR leads because the positive class is the minority; its floor is the "
            "prevalence, not 0.5."
        )
        add("")

    _calibration_section(result, add)

    add("## Per-fold spread")
    add("")
    add("| model | AUC-PR mean | AUC-PR sd | folds |")
    add("|---|---|---|---|")
    for name, model in result.models.items():
        summary = model.summary
        add(
            f"| `{name}` | {summary.get('auc_pr_mean', float('nan')):.4f} | "
            f"{summary.get('auc_pr_sd', float('nan')):.4f} | {len(model.folds)} |"
        )
    add("")
    add(
        "A model that scores the same on every fold and one that swings widely can share a "
        "mean and are not the same model. The spread is the more useful column when it is "
        "large."
    )
    add("")

    add("## What this does not establish")
    add("")
    add("- Nothing about ignition probability. Only conditional severity.")
    add("- Nothing about structure loss or insured loss. The label is spectral.")
    add(
        "- Nothing outside the southern interior of British Columbia, or outside the fire "
        "years listed above."
    )
    add(
        "- The high-severity threshold is a North American convention rather than a "
        "calibration against field plots in this biogeoclimatic zone."
    )
    add("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    return path
