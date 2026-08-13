"""Component B: how far this season's water balance departs from this place's own normal.

A season that delivers 40 mm over ninety days is a drought in the Monashee foothills and an
ordinary July on the valley floor at Osoyoos. Absolute water balance cannot tell those
apart, and a hazard layer built on it would rediscover the valley's rainfall gradient and
present the rediscovery as a finding. So the component is a departure: this window's
climatic water balance against the same calendar window in forty years of the same node's
own record.

Two halves, at two resolutions, for a reason recorded as D-014. The balance itself is
precipitation minus reference evapotranspiration from ERA5 through Open-Meteo's seamless
blend, because ERA5-Land returns null for both. The antecedent soil moisture is ERA5-Land,
which does carry it. They are kept as separate columns rather than merged into one number,
so a consumer can see that one is a 25 km product and the other a 9 km one.

Shallow and deep soil moisture are also kept apart. A dry surface over a wet root zone is a
grass fire; a dry column is a fire that holds overnight. Averaging them would erase exactly
the distinction that makes soil moisture worth having.

**The sign.** Positive means drier than normal, which is the direction associated with more
severe fire, and it is the same orientation every component in the index uses. This is a
departure scale, not a virtue scale: a high value is bad news about the ground, and the
index is named for integrity only because the specification named it that.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pyarrow as pa

from ..archive import MethodRecord
from ..spine import Spine
from .reference import (
    MINIMUM_REFERENCE_SEASONS,
    season_flag_labels,
    standardise_against_seasons,
)

log = logging.getLogger(__name__)

#: The parts, in the order the output reports them.
VARIABLES: tuple[str, ...] = ("water_deficit", "soil_shallow", "soil_deep")

STRUCTURE_OF_THE_SIGN = (
    "Positive means drier than this node's own normal for this calendar window, which is "
    "the direction associated with more severe fire."
)

#: Every part already arrives oriented so that positive is dry; the entries are here so the
#: orientation is a value that can be inverted rather than a minus sign inside an expression.
SIGN: dict[str, float] = {
    "water_deficit": 1.0,
    "soil_shallow": 1.0,
    "soil_deep": 1.0,
}

#: Ninety days ending on the as-of date. Long enough to carry the spring that set the
#: season's starting moisture, short enough that a wet May cannot cancel a dry July.
DEFICIT_WINDOW_DAYS = 90

#: Thirty days for soil moisture, which is antecedent condition rather than season history —
#: the deep layer's own memory already reaches back further than the window does.
MOISTURE_WINDOW_DAYS = 30

WATER_METHOD = MethodRecord(
    method_id="eii_component_b_water_balance_v1",
    name="Climatic water balance and antecedent soil moisture departure",
    citation=(
        "Allen, R.G., Pereira, L.S., Raes, D. and Smith, M. (1998). Crop evapotranspiration: "
        "guidelines for computing crop water requirements. FAO Irrigation and Drainage Paper "
        "56. Hersbach, H. et al. (2020). The ERA5 global reanalysis. Q. J. R. Meteorol. Soc. "
        "146:1999-2049, doi:10.1002/qj.3803. Muñoz-Sabater, J. et al. (2021). ERA5-Land. "
        "Earth Syst. Sci. Data 13:4349-4383, doi:10.5194/essd-13-4349-2021."
    ),
    version="1.0",
    formula=(
        "D = sum(P - ET0) over the 90 days ending as_of; z = -(D - mean(D_ref)) / sd(D_ref) "
        "over the same calendar window in each reference year. Soil moisture: "
        "z = -(m - mean(m_ref)) / sd(m_ref) over the 30 days ending as_of. "
        "b_score = mean of the available z, each multiplied by its entry in SIGN."
    ),
    notes=(
        "Reference evapotranspiration, not actual: MODIS ET needs an Earthdata login and "
        "ERA5-Land does not expose an actual-ET flux through this route, so the FAO-56 "
        "reference crop demand stands in, which is also the quantity SPEI is defined on. "
        "The two halves sit at different native resolutions, roughly 25 km for the balance "
        "and 9 km for the soil moisture, and are reported as separate columns for that "
        "reason. Positive is dry. A cell with no measurement behind it scores nothing "
        "rather than zero, and a departure taken against fewer than five reference seasons "
        "is flagged thin_reference and carries doubled uncertainty."
    ),
)

#: How much wider the doubt is when the reference behind a departure is thin or degenerate.
#: Stated rather than derived, for the same reason Component A states its own: the error is
#: in the reference being wrong, not in the sample being small.
WEAK_REFERENCE_PENALTY = 2.0


def _window_total(
    days: np.ndarray, values: np.ndarray, point: np.ndarray, n_points: int, first: date, last: date
) -> np.ndarray:
    """Sum of `values` per node over [first, last]. A node with no days in it gets NaN."""
    inside = (days >= np.datetime64(first)) & (days <= np.datetime64(last)) & np.isfinite(values)
    if not inside.any():
        return np.full(n_points, np.nan)
    totals = np.bincount(point[inside], weights=values[inside], minlength=n_points)
    counts = np.bincount(point[inside], minlength=n_points)
    return np.asarray(np.where(counts > 0, totals, np.nan))


def deficit_anomaly(
    table: pa.Table,
    *,
    n_points: int,
    as_of: date,
    window_days: int = DEFICIT_WINDOW_DAYS,
    minimum_seasons: int = MINIMUM_REFERENCE_SEASONS,
) -> np.ndarray:
    """Standardised dryness of the window ending `as_of`, per lattice node.

    The reference is the same calendar window in every earlier year the series covers, which
    is what makes this a seasonal departure rather than a comparison against the annual mean.
    Comparing an August window against a year-round distribution would score every August as
    dry and say nothing about which Augusts were dry.

    Returned already oriented: positive is drier than normal.
    """
    days = np.asarray(table.column("date")).astype("datetime64[D]")
    point = np.asarray(table.column("point"), dtype="int64")
    balance = np.asarray(table.column("precipitation_mm"), dtype="float64") - np.asarray(
        table.column("et0_mm"), dtype="float64"
    )

    span = timedelta(days=window_days - 1)
    current = _window_total(days, balance, point, n_points, as_of - span, as_of)

    first_year = int(str(days.min())[:4])
    reference_years = [year for year in range(first_year, as_of.year) if year >= first_year]
    reference = np.full((n_points, len(reference_years)), np.nan)
    for column, year in enumerate(reference_years):
        try:
            end = as_of.replace(year=year)
        except ValueError:  # 29 February in a common year
            end = as_of.replace(year=year, day=28)
        reference[:, column] = _window_total(days, balance, point, n_points, end - span, end)

    return standardise_against_seasons(current, reference, minimum_seasons=minimum_seasons)


def moisture_anomaly(
    current: np.ndarray,
    reference: np.ndarray,
    *,
    minimum_seasons: int = MINIMUM_REFERENCE_SEASONS,
) -> np.ndarray:
    """Standardised dryness of a soil-moisture window against the same window in other years.

    `reference` is one row per node and one column per reference season. Seasons that are
    missing are dropped from that node's distribution rather than counted, because a year
    ERA5-Land did not report is not a year the soil was at zero.
    """
    return standardise_against_seasons(
        np.asarray(current, dtype="float64"), reference, minimum_seasons=minimum_seasons
    )


def component_b(
    spine: Spine,
    *,
    deficit_z: np.ndarray,
    soil_shallow_z: np.ndarray,
    soil_deep_z: np.ndarray,
    flags: np.ndarray | None = None,
) -> pa.Table:
    """Per-cell water-balance departure: three standardised parts and their combination.

    `b_score` is the mean of whichever parts the cell has, each multiplied by its entry in
    `SIGN`. A mean rather than a sum, so a cell with one part is on the same scale as a cell
    with three, and `contributing_variables` is how a reader tells those apart.

    `uncertainty` is the standard error of that mean in z units, widened wherever the
    reference behind a part was thin or flat. It grows as the number of contributing parts
    falls, which is the honest direction: one departure is a weaker claim than three
    agreeing ones.
    """
    n_cells = spine.n_cells
    parts = np.vstack(
        [
            _checked("deficit_z", deficit_z, n_cells),
            _checked("soil_shallow_z", soil_shallow_z, n_cells),
            _checked("soil_deep_z", soil_deep_z, n_cells),
        ]
    )
    mask = np.zeros(n_cells, dtype="int64") if flags is None else _checked("flags", flags, n_cells)

    signs = np.array([SIGN[name] for name in VARIABLES], dtype="float64").reshape(-1, 1)
    available = np.isfinite(parts)
    contributing = available.sum(axis=0)
    scored = contributing > 0

    with np.errstate(invalid="ignore"):
        b_score = np.where(
            scored, np.nansum(parts * signs, axis=0) / np.maximum(contributing, 1), np.nan
        )

    # Standard error of a mean of correlated departures. They are not independent — a dry
    # season and a dry soil column are the same weather seen twice — so the divisor is the
    # count rather than its square root, which is the conservative reading.
    penalty = np.where(mask > 0, WEAK_REFERENCE_PENALTY, 1.0)
    with np.errstate(invalid="ignore"):
        spread = np.where(
            scored, penalty / np.sqrt(np.maximum(contributing, 1).astype("float64")), np.nan
        )

    rendered = {value: "|".join(season_flag_labels(value)) for value in range(8)}
    log.info(
        "component B: %d of %d cells scored, %.1f%% on a weak reference",
        int(scored.sum()),
        n_cells,
        100.0 * float(np.mean(mask > 0)),
    )

    return pa.table(
        {
            "h3": spine.cells.column("h3"),
            "z_water_deficit": pa.array(parts[0], pa.float32()),
            "z_soil_shallow": pa.array(parts[1], pa.float32()),
            "z_soil_deep": pa.array(parts[2], pa.float32()),
            "b_score": pa.array(b_score, pa.float32()),
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
