"""Terrain derivatives: slope, aspect and the topographic wetness index.

Terrain is the half of wildfire substrate that does not change month to month, and it is
the half a land manager already knows about their property. Its value here is that it
explains where the moisture indicators will be wrong in the same place every year: north
aspects hold water, convergent hollows hold water, steep south-facing ground does not.
"""

from __future__ import annotations

import numpy as np

from ..schemas.provenance import Method

SLOPE_METHOD = Method(
    name="Slope and aspect (Horn 1981)",
    citation=(
        "Horn, B.K.P. (1981). Hill shading and the reflectance map. Proceedings of the "
        "IEEE, 69(1), 14-47. doi:10.1109/PROC.1981.11918"
    ),
    doi="10.1109/PROC.1981.11918",
    formula="slope = atan(sqrt((dz/dx)^2 + (dz/dy)^2)), aspect = atan2(dz/dy, -dz/dx)",
    notes=(
        "Third-order finite difference over the eight neighbours of each cell, the method "
        "used by GDAL and by most GIS packages, so values are comparable with them."
    ),
)

TWI_METHOD = Method(
    name="Topographic wetness index (Beven and Kirkby 1979)",
    citation=(
        "Beven, K.J. and Kirkby, M.J. (1979). A physically based, variable contributing "
        "area model of basin hydrology. Hydrological Sciences Bulletin, 24(1), 43-69. "
        "doi:10.1080/02626667909491834"
    ),
    doi="10.1080/02626667909491834",
    formula="TWI = ln(a / tan(beta))",
    notes=(
        "Specific catchment area over the tangent of slope. High values mark convergent, "
        "low-gradient ground that stays wet; low values mark steep divergent ground that "
        "sheds water. Flow accumulation is computed by single-direction (D8) routing."
    ),
)


def slope_and_aspect(elevation: np.ndarray, cell_size_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Slope in degrees and aspect in degrees clockwise from north.

    Aspect on flat ground is undefined rather than zero — returning north for a horizontal
    plane would put a spurious cold, wet aspect across every valley bottom.
    """
    padded = np.pad(elevation, 1, mode="edge")

    a = padded[:-2, :-2]
    b = padded[:-2, 1:-1]
    c = padded[:-2, 2:]
    d = padded[1:-1, :-2]
    f = padded[1:-1, 2:]
    g = padded[2:, :-2]
    h = padded[2:, 1:-1]
    i = padded[2:, 2:]

    dz_dx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * cell_size_m)
    dz_dy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * cell_size_m)

    rise_run = np.hypot(dz_dx, dz_dy)
    slope = np.degrees(np.arctan(rise_run)).astype("float32")

    aspect = np.degrees(np.arctan2(dz_dy, -dz_dx))
    aspect = np.where(
        aspect < 0, 90.0 - aspect, np.where(aspect > 90.0, 450.0 - aspect, 90.0 - aspect)
    )
    aspect = np.where(rise_run == 0, np.nan, aspect % 360.0).astype("float32")

    return slope, aspect


# D8 offsets, clockwise from north, with their travel distances in cell widths.
_D8 = (
    (-1, 0, 1.0),
    (-1, 1, np.sqrt(2)),
    (0, 1, 1.0),
    (1, 1, np.sqrt(2)),
    (1, 0, 1.0),
    (1, -1, np.sqrt(2)),
    (0, -1, 1.0),
    (-1, -1, np.sqrt(2)),
)


def flow_accumulation(elevation: np.ndarray) -> np.ndarray:
    """Cells draining through each cell, by single-direction (D8) routing.

    Cells are processed from highest to lowest, so every contributor has already been
    settled by the time a cell is visited and one pass suffices. Sinks — cells with no
    lower neighbour — terminate the flow rather than being filled; on a coastal landscape
    with real water bodies, filling would invent drainage that does not exist.
    """
    rows, cols = elevation.shape
    accumulation = np.ones(elevation.size, dtype=np.float32)

    flat = elevation.ravel()
    finite = np.isfinite(flat)
    order = np.argsort(np.where(finite, -flat, np.inf), kind="stable")

    row_of = np.arange(elevation.size) // cols
    col_of = np.arange(elevation.size) % cols

    for index in order:
        if not finite[index]:
            break
        r = row_of[index]
        c = col_of[index]
        z = flat[index]

        steepest = 0.0
        target = -1
        for dr, dc, distance in _D8:
            nr = r + dr
            nc = c + dc
            if not (0 <= nr < rows and 0 <= nc < cols):
                continue
            neighbour = nr * cols + nc
            if not finite[neighbour]:
                continue
            drop = (z - flat[neighbour]) / distance
            if drop > steepest:
                steepest = drop
                target = neighbour

        if target >= 0:
            accumulation[target] += accumulation[index]

    return accumulation.reshape(elevation.shape)


# Slope is clamped away from zero before the logarithm. A perfectly flat cell would give an
# infinite index, which is not a wetter place, only an undefined one.
MIN_SLOPE_RADIANS = np.radians(0.1)


def topographic_wetness_index(
    elevation: np.ndarray, slope_degrees: np.ndarray, cell_size_m: float
) -> np.ndarray:
    """TWI = ln(a / tan(beta)), with `a` the specific catchment area."""
    accumulation = flow_accumulation(elevation)
    specific_area = (accumulation * cell_size_m * cell_size_m) / cell_size_m

    beta = np.maximum(np.radians(slope_degrees), MIN_SLOPE_RADIANS)
    with np.errstate(divide="ignore", invalid="ignore"):
        twi = np.log(np.maximum(specific_area, cell_size_m) / np.tan(beta))
    return np.where(np.isfinite(elevation), twi, np.nan).astype("float32")


HEAT_LOAD_METHOD = Method(
    name="Heat load index (McCune and Keon 2001)",
    citation=(
        "McCune, B. and Keon, D. (2002). Equations for potential annual direct incident "
        "radiation and heat load. Journal of Vegetation Science, 13(4), 603-606. "
        "doi:10.1111/j.1654-1103.2002.tb02087.x"
    ),
    doi="10.1111/j.1654-1103.2002.tb02087.x",
    formula=(
        "HLI = 0.339 + 0.808 cos(lat) cos(slope) - 0.196 sin(lat) sin(slope) "
        "- 0.482 cos(A') sin(slope), A' = 180 - |aspect - 225|"
    ),
    notes=(
        "Potential annual heat load from slope, aspect and latitude. Aspect is folded about "
        "the northeast-southwest axis so that southwest — the hottest aspect in the northern "
        "hemisphere, because afternoon sun falls on already-warm ground — scores highest, "
        "rather than treating due south as the peak. This is the physical mechanism behind "
        "the aspect gradients the moisture indices show."
    ),
)


def heat_load_index(
    slope_degrees: np.ndarray, aspect_degrees: np.ndarray, latitude_degrees: float
) -> np.ndarray:
    """Potential annual heat load, roughly 0 to 1.

    Flat ground has no aspect, and the equation reduces correctly there: the aspect terms
    carry sin(slope), which is zero, so a flat cell scores on latitude alone.
    """
    lat = np.radians(latitude_degrees)
    slope = np.radians(np.nan_to_num(slope_degrees, nan=0.0))

    # Folded aspect: distance from northeast, so southwest is the maximum.
    folded = np.radians(180.0 - np.abs(np.nan_to_num(aspect_degrees, nan=225.0) - 225.0))

    hli = (
        0.339
        + 0.808 * np.cos(lat) * np.cos(slope)
        - 0.196 * np.sin(lat) * np.sin(slope)
        - 0.482 * np.cos(folded) * np.sin(slope)
    )

    # The published equation is a regression fit, and near-vertical northeast faces in the
    # tropics take it a fraction below zero. A negative annual heat load is not a thing;
    # the floor is the fit's error, not a measurement, and clipping keeps the hard bound at
    # zero meaningful rather than padding it to admit an artefact.
    hli = np.maximum(hli, 0.0)
    return np.where(np.isfinite(slope_degrees), hli, np.nan).astype("float32")
