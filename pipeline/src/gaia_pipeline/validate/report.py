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
from .metrics import Delta

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
        "high-severity break, from Landsat growing-season composites one year either side "
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
    if context.get("excluded"):
        for reason, count in context["excluded"].items():
            add(f"- **{reason.replace('_', ' ')}:** {count:,}")
    for note in result.notes:
        add(f"- {note}")
    add("")

    add("## Models")
    add("")
    add("| model | features | AUC-PR | AUC-ROC | Brier | calibration gap |")
    add("|---|---|---|---|---|---|")
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
            f"{summary.get('calibration_max_gap', float('nan')):.3f} |"
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
