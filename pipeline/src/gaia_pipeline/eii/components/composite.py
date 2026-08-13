"""The index itself: five components, equal weights, and nothing hidden in the blend.

The specification is explicit that the EII is a documented weighted sum with equal weights
to begin with, refined by validation, and never a black-box combination. So this module is
deliberately the least clever file in the build. It averages whichever component scores a
cell has, records which ones those were, and propagates the doubt.

**Equal weights are a stated ignorance, not a finding.** Nothing in this build establishes
that vegetation structure matters as much as drought. Component A is the only one that has
been through a gate; B through E have not, and inventing weights for them would be
presenting a guess as a result. When there is evidence for a weighting, it goes here as
numbers with a citation beside them.

**The orientation, once, for all five.** Every component is a departure oriented so that
positive is the direction associated with more severe fire: more structure than context,
drier than normal, less riparian influence than normal, higher codes than normal, deeper
drought than normal. A high EII is bad news about the ground. The name is inherited from the
specification; the scale is a departure scale, and a reader who takes "integrity" to mean
"higher is healthier" would read every map backwards.

**Missing is not average.** A cell with no Component C is scored on the components it has,
not on four components and a zero. Zero is the middle of a z scale and the strongest possible
claim of ordinariness, which is exactly the wrong thing to say about something never
measured. `contributing_components` is what separates a cell scored on five from a cell
scored on one, and no consumer should treat those as the same number.
"""

from __future__ import annotations

import logging

import numpy as np
import pyarrow as pa

from ..archive import MethodRecord
from ..spine import Spine

log = logging.getLogger(__name__)

#: The components, in the order the index reports them.
COMPONENTS: tuple[str, ...] = ("a_structure", "b_water", "c_riparian", "d_moisture", "e_drought")

#: Equal, and equal on purpose. See the module docstring.
WEIGHTS: dict[str, float] = {name: 1.0 / len(COMPONENTS) for name in COMPONENTS}

#: Below this many components the index is a restatement of one measurement rather than an
#: index. It is still served — a single component with its provenance is a useful answer —
#: but it is flagged, because an underwriter comparing two cells needs to know that one of
#: them is a composite and the other is a single reading wearing a composite's name.
MINIMUM_COMPONENTS = 3

#: Fewer than `MINIMUM_COMPONENTS` behind the value.
THIN_INDEX = 0b01
#: No component at all: the cell is unscored.
NO_COMPONENTS = 0b10

_FLAG_NAMES: tuple[tuple[int, str], ...] = (
    (THIN_INDEX, "thin_index"),
    (NO_COMPONENTS, "no_components"),
)

COMPOSITE_METHOD = MethodRecord(
    method_id="eii_composite_v1",
    name="Ecosystem Integrity Index, equal-weighted component mean",
    citation=(
        "Composite of this build's own components; see the method record of each for its "
        "own citations. Weighting follows the v0.2 design specification, which requires a "
        "documented weighted sum with equal initial weights rather than a fitted blend."
    ),
    version="1.0",
    formula="eii = mean of the available component scores, each already oriented so that positive is the fire-severe direction",
    notes=(
        "Equal weights are an admission that nothing here establishes a ranking among the "
        "components, not a claim that they matter equally. Only Component A has been "
        "through a validation gate. A cell missing a component is scored on the components "
        "it has rather than having the gap filled with a zero, because zero on a departure "
        "scale is a claim of ordinariness and a gap is not one. The uncertainty is the "
        "quadrature mean of the contributing components' own, widened where fewer than "
        "three contributed. Positive means worse condition: the name is inherited from the "
        "specification and the scale is a departure scale."
    ),
)


def flag_labels(mask: int) -> tuple[str, ...]:
    return tuple(name for bit, name in _FLAG_NAMES if mask & bit)


def compose(
    spine: Spine,
    *,
    scores: dict[str, np.ndarray],
    uncertainties: dict[str, np.ndarray] | None = None,
) -> pa.Table:
    """Combine component scores into the index, keeping every component beside it.

    `scores` maps component name to a per-cell array; a component that was not built at all
    is simply absent from the mapping, which is different from being present and NaN. The
    first says the pipeline did not run it, the second says it ran and could not measure
    this cell, and both end up as missing here but only the second is a fact about the cell.
    """
    n_cells = spine.n_cells
    present = [name for name in COMPONENTS if name in scores]
    if not present:
        raise ValueError("the index cannot be composed from no components")

    values = np.vstack([_checked(name, scores[name], n_cells) for name in present])
    weights = np.array([WEIGHTS[name] for name in present], dtype="float64").reshape(-1, 1)

    available = np.isfinite(values)
    contributing = available.sum(axis=0)
    weight_present = np.where(available, weights, 0.0).sum(axis=0)
    scored = contributing > 0

    with np.errstate(invalid="ignore"):
        index = np.where(
            scored, np.nansum(values * weights, axis=0) / np.maximum(weight_present, 1e-12), np.nan
        )

    doubt = np.full(n_cells, np.nan)
    if uncertainties:
        stack = np.vstack(
            [
                _checked(f"{name} uncertainty", uncertainties[name], n_cells)
                if name in uncertainties
                else np.full(n_cells, np.nan)
                for name in present
            ]
        )
        # Quadrature over the components that both scored and reported a doubt. The
        # components are not independent — they are five readings of one season on one
        # piece of ground — so this understates rather than overstates, and the widening
        # below is the acknowledgement of that.
        usable = available & np.isfinite(stack)
        counted = usable.sum(axis=0)
        with np.errstate(invalid="ignore"):
            doubt = np.where(
                counted > 0,
                np.sqrt(np.nansum(np.where(usable, stack, 0.0) ** 2, axis=0))
                / np.maximum(counted, 1),
                np.nan,
            )

    flags = np.zeros(n_cells, dtype="int64")
    flags |= np.where(~scored, NO_COMPONENTS, 0)
    flags |= np.where(scored & (contributing < MINIMUM_COMPONENTS), THIN_INDEX, 0)
    doubt = np.where(flags & THIN_INDEX, doubt * 1.5, doubt)

    log.info(
        "EII: %d of %d cells scored, %.1f%% on fewer than %d components (built: %s)",
        int(scored.sum()),
        n_cells,
        100.0 * float(np.mean(scored & (contributing < MINIMUM_COMPONENTS))),
        MINIMUM_COMPONENTS,
        ", ".join(present),
    )

    rendered = {value: "|".join(flag_labels(value)) for value in range(4)}
    columns: dict[str, pa.Array] = {"h3": spine.cells.column("h3")}
    for position, name in enumerate(present):
        columns[name] = pa.array(values[position], pa.float32())
    columns["eii"] = pa.array(index, pa.float32())
    columns["contributing_components"] = pa.array(contributing.astype("uint8"), pa.uint8())
    columns["uncertainty"] = pa.array(doubt, pa.float32())
    columns["flags"] = pa.array([rendered[int(value) & 0b11] for value in flags], pa.string())
    return pa.table(columns)


def _checked(name: str, values: np.ndarray, n_cells: int) -> np.ndarray:
    array = np.asarray(values, dtype="float64")
    if array.shape != (n_cells,):
        raise ValueError(f"{name} has shape {array.shape}, expected ({n_cells},)")
    return array
