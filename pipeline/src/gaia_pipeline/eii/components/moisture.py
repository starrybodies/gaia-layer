"""Component D: fuel moisture, as a departure from this node's own fire season.

The Drought Code reaching 400 is a hard summer above Vernon and an ordinary August on the
Osoyoos valley floor, which reaches it most years. Reporting the level would produce a map
of the valley's climate and call it a warning. So the component is a departure, in the same
shape as Component B: today's codes against the same node's own distribution for the same
calendar date across the reference seasons.

Three parts, because they fail in different ways. The Drought Code is the deep-duff memory
of the whole season and moves slowly. The Buildup Index combines it with the Duff Moisture
Code, so it responds to the last fortnight as well — and, being derived from DC, it is not
independent of it, which is why it carries the smallest weight. Vapour pressure deficit is
atmospheric demand on the day, it is not part of the CFFDRS chain at all, and it is the
variable most of the recent severity literature actually reports.

**The sign, which is where this differs from Component B.** Water balance and soil moisture
fall as the ground dries; the codes climb. Both have to leave the standardisation oriented
the same way — positive is drier — so this module asks for `high_is_dry` and Component B
does not. Getting it backwards would produce a component that looks entirely reasonable and
points the wrong way.

The codes themselves come from `sources/weather.py`, which computes Van Wagner and Pickett's
equations and is checked against CWFIS's own published series. The known divergence there —
Duff Moisture Code running up to 23% above CWFIS because CWFIS suspends code advance for
snow and the published specification does not — passes through BUI into this component, and
is recorded as D-010.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pyarrow as pa

from ..archive import MethodRecord
from ..sources.weather import FwiState, fwi_series
from ..spine import Spine
from .reference import (
    MINIMUM_REFERENCE_SEASONS,
    season_flag_labels,
    standardise_against_seasons,
)

log = logging.getLogger(__name__)

VARIABLES: tuple[str, ...] = ("drought_code", "buildup_index", "vpd")

STRUCTURE_OF_THE_SIGN = (
    "Positive means drier than this node's own normal for this date in the season — the "
    "codes higher, the air thirstier — which is the direction associated with more severe "
    "fire."
)

#: All three run the same way once standardised: up is dry.
SIGN: dict[str, float] = {"drought_code": 1.0, "buildup_index": 1.0, "vpd": 1.0}

#: Not equal, because the parts are not independent. BUI is computed from DC and the Duff
#: Moisture Code, so DC and BUI share most of their information; giving them a third each
#: would count the deep-duff signal twice and let it outvote the atmosphere. VPD is the only
#: one of the three that is not part of the CFFDRS chain, so it carries a full share.
WEIGHTS: dict[str, float] = {"drought_code": 0.4, "buildup_index": 0.2, "vpd": 0.4}

#: When the codes start each season. The moisture codes are a running state with weeks of
#: memory, so a season begun in July would report July's weather as if it were the year's;
#: 1 March is early enough that the standard spring startup values have been forgotten by
#: the time a fire season begins.
SPINUP_START = (3, 1)

MOISTURE_METHOD = MethodRecord(
    method_id="eii_component_d_fuel_moisture_v1",
    name="Fire weather code and vapour pressure deficit departure",
    citation=(
        "Van Wagner, C.E. and Pickett, T.L. (1985). Equations and FORTRAN program for the "
        "Canadian Forest Fire Weather Index System. Canadian Forestry Service Forestry "
        "Technical Report 33. Van Wagner, C.E. (1987). Development and structure of the "
        "Canadian Forest Fire Weather Index System. Forestry Technical Report 35."
    ),
    version="1.0",
    formula=(
        "Codes run from a 1 March spring startup to the as-of date at each lattice node. "
        "z = (value - mean) / sd against the same date in each reference season, so that "
        "positive is dry. d_score = 0.4*z(DC) + 0.2*z(BUI) + 0.4*z(VPD) over the parts "
        "present, renormalised by the weight actually available."
    ),
    notes=(
        "A departure rather than a level, because the level maps the valley's climate "
        "rather than the year's. The weights are unequal on purpose: BUI is derived from "
        "DC and the two are not independent, so equal weights would count the deep-duff "
        "signal twice. The Duff Moisture Code computed here runs up to 23% above the CWFIS "
        "operational series because CWFIS suspends code advance for snow on the ground and "
        "the published specification does not; that difference passes through BUI into this "
        "component and is recorded as divergence D-010. A node whose season the archive "
        "does not reach scores nothing rather than zero."
    ),
)


def seasonal_codes(
    weather: pa.Table, *, n_points: int, as_of: date, spinup: tuple[int, int] = SPINUP_START
) -> dict[str, np.ndarray]:
    """Drought Code, Buildup Index and VPD at every node on `as_of`.

    Each node's season is run separately and from its own spring startup, because the codes
    are a running state: splicing two nodes' weather together, or beginning mid-season,
    carries moisture that never existed. A node whose series does not reach `as_of`, or has
    a gap in it, comes back missing rather than carrying the last observed day forward.
    """
    days = np.asarray(weather.column("date")).astype("datetime64[D]")
    point = np.asarray(weather.column("point"), dtype="int64")
    start = np.datetime64(date(as_of.year, *spinup))
    end = np.datetime64(as_of)

    columns = {name: np.full(n_points, np.nan) for name in ("dc", "bui", "vpd_kpa")}
    inside = (days >= start) & (days <= end)
    expected = int((end - start).astype(int)) + 1

    for node in range(n_points):
        rows = np.flatnonzero(inside & (point == node))
        if rows.size != expected:
            continue

        order = rows[np.argsort(days[rows])]
        series = fwi_series(
            [value.astype(object) for value in days[order]],
            np.asarray(weather.column("temp_c"), dtype="float64")[order],
            np.asarray(weather.column("rh_pct"), dtype="float64")[order],
            np.asarray(weather.column("wind_kmh"), dtype="float64")[order],
            np.asarray(weather.column("rain_mm"), dtype="float64")[order],
            start=FwiState(),
        )
        if series.num_rows == 0:
            continue
        last = series.num_rows - 1
        for name in columns:
            columns[name][node] = float(series.column(name)[last].as_py())

    return columns


def code_anomaly(
    current: np.ndarray,
    reference: np.ndarray,
    *,
    minimum_seasons: int = MINIMUM_REFERENCE_SEASONS,
) -> np.ndarray:
    """Standardise a code against the same date in other seasons. Positive is dry.

    `high_is_dry` is the whole point of this wrapper existing: the codes climb as conditions
    dry, unlike every other input to the index, and a component that inherited Component B's
    orientation would be exactly backwards.
    """
    return standardise_against_seasons(
        current, reference, minimum_seasons=minimum_seasons, high_is_dry=True
    )


def component_d(
    spine: Spine,
    *,
    dc_z: np.ndarray,
    bui_z: np.ndarray,
    vpd_z: np.ndarray,
    flags: np.ndarray | None = None,
) -> pa.Table:
    """Per-cell fuel-moisture departure: three standardised parts, weighted.

    The weights are renormalised over whichever parts the cell has, so a cell with VPD and
    no codes is on the same scale as a cell with all three rather than being scored as if
    the missing parts were at their means. `contributing_variables` is how a reader tells
    those cases apart.
    """
    n_cells = spine.n_cells
    parts = np.vstack(
        [
            _checked("dc_z", dc_z, n_cells),
            _checked("bui_z", bui_z, n_cells),
            _checked("vpd_z", vpd_z, n_cells),
        ]
    )
    mask = np.zeros(n_cells, dtype="int64") if flags is None else _checked("flags", flags, n_cells)

    signs = np.array([SIGN[name] for name in VARIABLES], dtype="float64").reshape(-1, 1)
    weights = np.array([WEIGHTS[name] for name in VARIABLES], dtype="float64").reshape(-1, 1)

    available = np.isfinite(parts)
    contributing = available.sum(axis=0)
    weight_present = np.where(available, weights, 0.0).sum(axis=0)
    scored = contributing > 0

    with np.errstate(invalid="ignore"):
        d_score = np.where(
            scored,
            np.nansum(parts * signs * weights, axis=0) / np.maximum(weight_present, 1e-12),
            np.nan,
        )
        # The parts are the same weather seen three ways, so the doubt on their combination
        # falls with the count rather than with its square root.
        spread = np.where(
            scored,
            np.where(mask > 0, 2.0, 1.0) / np.maximum(contributing, 1).astype("float64"),
            np.nan,
        )

    log.info(
        "component D: %d of %d cells scored, %.1f%% on a weak reference",
        int(scored.sum()),
        n_cells,
        100.0 * float(np.mean(mask > 0)),
    )

    rendered = {value: "|".join(season_flag_labels(value)) for value in range(8)}
    return pa.table(
        {
            "h3": spine.cells.column("h3"),
            "z_drought_code": pa.array(parts[0], pa.float32()),
            "z_buildup_index": pa.array(parts[1], pa.float32()),
            "z_vpd": pa.array(parts[2], pa.float32()),
            "d_score": pa.array(d_score, pa.float32()),
            "contributing_variables": pa.array(contributing.astype("uint8"), pa.uint8()),
            "uncertainty": pa.array(spread, pa.float32()),
            "flags": pa.array([rendered[int(value) & 0b111] for value in mask], pa.string()),
        }
    )


def _checked(name: str, values: np.ndarray, n_cells: int) -> np.ndarray:
    array = np.asarray(values, dtype="float64")
    if array.shape != (n_cells,):
        raise ValueError(f"{name} has shape {array.shape}, expected ({n_cells},)")
    return array
