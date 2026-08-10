"""The constraint engine.

Three classes of check, in the order they run:

1. **Hard physical bounds.** A value outside them is not a measurement, it is a defect.
   These reject.
2. **Plausible range.** Physically possible but ecologically extreme for this bioregion.
   These flag — the world does occasionally do surprising things, and a layer that rejects
   surprise is a layer that cannot see a heat dome.
3. **Temporal consistency and cross-variable coherence.** The value is fine on its own but
   sits badly against its own history or against the other indicators. These flag.

The distinction between rejecting and flagging is the whole design. Rejection says "this
number is wrong". Flagging says "this number may be right and you should know what is odd
about it". Only the first is allowed to remove a value from service.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from ..schemas.common import IndicatorId, Severity
from ..schemas.envelope import ValidationFlag


@dataclass(frozen=True)
class Bounds:
    """Physical limits and the narrower range we expect in this bioregion."""

    hard_min: float
    hard_max: float
    plausible_min: float
    plausible_max: float
    reason: str


# Hard bounds are properties of the quantity itself. A normalised difference of two
# non-negative reflectances cannot leave [-1, 1] — arithmetic forbids it — so a value that
# does means the input was corrupt, not that the vegetation did something interesting.
BOUNDS: Mapping[IndicatorId, Bounds] = {
    IndicatorId.NDVI: Bounds(-1.0, 1.0, -0.3, 1.0, "normalised difference of two reflectances"),
    IndicatorId.NDMI: Bounds(-1.0, 1.0, -0.6, 0.8, "normalised difference of two reflectances"),
    IndicatorId.NBR: Bounds(-1.0, 1.0, -0.7, 1.0, "normalised difference of two reflectances"),
    IndicatorId.VPD_KPA: Bounds(
        0.0, 20.0, 0.0, 6.0, "vapour pressure deficit is non-negative by definition"
    ),
    IndicatorId.PRECIP_30D_MM: Bounds(
        0.0, 5000.0, 0.0, 900.0, "accumulated depth cannot be negative"
    ),
    IndicatorId.TEMP_MAX_C: Bounds(-70.0, 60.0, -25.0, 45.0, "recorded terrestrial extremes"),
    IndicatorId.DAYS_SINCE_RAIN: Bounds(0.0, 400.0, 0.0, 120.0, "a count of days"),
    IndicatorId.SOIL_MOISTURE_0_7CM: Bounds(
        0.0, 1.0, 0.02, 0.60, "volumetric fraction, bounded by total porosity"
    ),
    IndicatorId.SOIL_MOISTURE_7_28CM: Bounds(
        0.0, 1.0, 0.02, 0.60, "volumetric fraction, bounded by total porosity"
    ),
    IndicatorId.ELEVATION_M: Bounds(-500.0, 9000.0, -10.0, 2400.0, "terrestrial elevation range"),
    IndicatorId.SLOPE_DEG: Bounds(0.0, 90.0, 0.0, 75.0, "angle from horizontal"),
    IndicatorId.ASPECT_DEG: Bounds(0.0, 360.0, 0.0, 360.0, "compass bearing"),
    IndicatorId.TWI: Bounds(-10.0, 40.0, 0.0, 25.0, "log of contributing area over slope"),
    IndicatorId.HEAT_LOAD: Bounds(0.0, 1.6, 0.1, 1.2, "normalised potential annual heat load"),
    IndicatorId.LAND_COVER: Bounds(10.0, 100.0, 10.0, 100.0, "ESA WorldCover class code"),
}


# --------------------------------------------------------------------- rate limits


@dataclass(frozen=True)
class RateLimit:
    """How fast an indicator can plausibly move, per month, in each direction.

    Asymmetric on purpose. Vegetation loses moisture and greenness abruptly — fire,
    harvest, windthrow, a heat dome — but it regains them only as fast as it can grow new
    tissue. A rise that outpaces growth is far more likely to be a masking failure than a
    forest recovering in a fortnight.
    """

    max_rise_per_month: float
    max_fall_per_month: float
    note: str


RATE_LIMITS: Mapping[IndicatorId, RateLimit] = {
    IndicatorId.NDVI: RateLimit(
        0.30, 0.70, "canopy greening is limited by growth rate; loss is not"
    ),
    IndicatorId.NDMI: RateLimit(
        0.25, 0.70, "canopy water recovery requires new foliage; desiccation does not"
    ),
    IndicatorId.NBR: RateLimit(0.30, 0.80, "char and soil exposure appear faster than they heal"),
    IndicatorId.SOIL_MOISTURE_0_7CM: RateLimit(
        0.35, 0.35, "surface soil both wets and dries within days"
    ),
    IndicatorId.SOIL_MOISTURE_7_28CM: RateLimit(
        0.20, 0.20, "subsurface soil responds more slowly than the surface"
    ),
}

# Terrain does not change between months. A difference would mean the elevation model was
# replaced, which is a provenance event, not an ecological one.
STATIC_INDICATORS: frozenset[IndicatorId] = frozenset(
    {
        IndicatorId.ELEVATION_M,
        IndicatorId.SLOPE_DEG,
        IndicatorId.ASPECT_DEG,
        IndicatorId.TWI,
        IndicatorId.HEAT_LOAD,
        IndicatorId.LAND_COVER,
    }
)


# ------------------------------------------------------- cross-variable coherence rules

# A canopy reading wet while the atmosphere is pulling hard and nothing has fallen from the
# sky in a month is the signature of a masked cloud edge or a mis-scaled band, not of a
# forest. Thresholds are set for the coastal Douglas-fir zone in summer.
WET_CANOPY_NDMI = 0.25
HIGH_VPD_KPA = 2.0
DRY_PRECIP_30D_MM = 10.0

WET_SOIL_FRACTION = 0.35
SOIL_DRY_PRECIP_30D_MM = 5.0

# NDVI and NBR share their near-infrared term, so over vegetated ground they move together.
# A large divergence means one of the shortwave-infrared bands is misbehaving.
SPECTRAL_DIVERGENCE_THRESHOLD = 1.0


def check_bounds(indicator: IndicatorId, value: float) -> list[ValidationFlag]:
    """Class 1 and 2: is this a number, and is it inside the limits of the quantity?"""
    flags: list[ValidationFlag] = []

    if math.isnan(value):
        return [
            ValidationFlag(
                code="not_a_number",
                constraint="physical_bounds",
                severity=Severity.ERROR,
                message=f"{indicator.value} resolved to NaN, which is an absence of data, not a value.",
                expected="a finite number",
                confidence_penalty=1.0,
            )
        ]

    if math.isinf(value):
        return [
            ValidationFlag(
                code="not_a_number",
                constraint="physical_bounds",
                severity=Severity.ERROR,
                message=f"{indicator.value} resolved to infinity.",
                observed=value,
                expected="a finite number",
                confidence_penalty=1.0,
            )
        ]

    bounds = BOUNDS.get(indicator)
    if bounds is None:
        return flags

    if value < bounds.hard_min or value > bounds.hard_max:
        return [
            ValidationFlag(
                code="out_of_physical_bounds",
                constraint="physical_bounds",
                severity=Severity.ERROR,
                message=(
                    f"{indicator.value} = {value:.4g} lies outside its physical range "
                    f"[{bounds.hard_min:g}, {bounds.hard_max:g}]: {bounds.reason}."
                ),
                observed=value,
                expected=f"[{bounds.hard_min:g}, {bounds.hard_max:g}]",
                confidence_penalty=1.0,
            )
        ]

    if value < bounds.plausible_min or value > bounds.plausible_max:
        flags.append(
            ValidationFlag(
                code="outside_plausible_range",
                constraint="plausible_range",
                severity=Severity.WARN,
                message=(
                    f"{indicator.value} = {value:.4g} is physically possible but outside the "
                    f"range expected for this bioregion "
                    f"[{bounds.plausible_min:g}, {bounds.plausible_max:g}]. "
                    "Served, because unusual conditions are exactly what this layer exists "
                    "to detect, but treat it as unusual."
                ),
                observed=value,
                expected=f"[{bounds.plausible_min:g}, {bounds.plausible_max:g}]",
                confidence_penalty=0.35,
            )
        )

    return flags


def check_temporal(
    indicator: IndicatorId,
    value: float,
    history: Sequence[tuple[date, float]],
    period_end: date,
) -> list[ValidationFlag]:
    """Class 3a: does this value move from its own past at a plausible speed?"""
    if indicator in STATIC_INDICATORS:
        return []
    limit = RATE_LIMITS.get(indicator)
    if limit is None or not math.isfinite(value):
        return []

    prior = [(d, v) for d, v in history if d < period_end and math.isfinite(v)]
    if not prior:
        return []

    previous_date, previous_value = max(prior, key=lambda item: item[0])
    months = max((period_end - previous_date).days / 30.44, 1e-6)
    change = value - previous_value
    rate = change / months

    if rate > limit.max_rise_per_month:
        return [
            ValidationFlag(
                code="implausible_recovery_rate",
                constraint="temporal_consistency",
                severity=Severity.WARN,
                message=(
                    f"{indicator.value} rose {change:+.3f} over {months:.1f} months "
                    f"({rate:+.3f}/month), faster than the {limit.max_rise_per_month:.2f}/month "
                    f"ceiling: {limit.note}. Most often a masking failure in one of the two "
                    "composites rather than genuine recovery."
                ),
                observed=rate,
                expected=f"rise of at most {limit.max_rise_per_month:.2f}/month",
                confidence_penalty=0.45,
            )
        ]

    if -rate > limit.max_fall_per_month:
        return [
            ValidationFlag(
                code="implausible_change_rate",
                constraint="temporal_consistency",
                severity=Severity.WARN,
                message=(
                    f"{indicator.value} fell {change:+.3f} over {months:.1f} months "
                    f"({rate:+.3f}/month), beyond the {limit.max_fall_per_month:.2f}/month "
                    "ceiling. A stand-replacing disturbance would do this; so would a "
                    "cloud shadow that survived masking."
                ),
                observed=rate,
                expected=f"fall of at most {limit.max_fall_per_month:.2f}/month",
                confidence_penalty=0.40,
            )
        ]

    return []


def check_coherence(
    indicator: IndicatorId,
    value: float,
    covariates: Mapping[IndicatorId, float],
) -> list[ValidationFlag]:
    """Class 3b: does this value sit sensibly beside the others measured with it?"""
    if not math.isfinite(value) or not covariates:
        return []

    flags: list[ValidationFlag] = []
    vpd = covariates.get(IndicatorId.VPD_KPA)
    precip = covariates.get(IndicatorId.PRECIP_30D_MM)

    if (
        indicator is IndicatorId.NDMI
        and value > WET_CANOPY_NDMI
        and vpd is not None
        and precip is not None
        and vpd > HIGH_VPD_KPA
        and precip < DRY_PRECIP_30D_MM
    ):
        flags.append(
            ValidationFlag(
                code="moisture_atmosphere_incoherent",
                constraint="cross_variable_coherence",
                severity=Severity.WARN,
                message=(
                    f"Canopy moisture reads wet (NDMI {value:.3f}) while the atmosphere is "
                    f"drawing hard (VPD {vpd:.2f} kPa) and only {precip:.1f} mm of rain fell "
                    "in the preceding 30 days. Live fuel does not usually hold water under "
                    "those conditions; suspect residual cloud in the composite."
                ),
                observed=value,
                expected=(
                    f"NDMI at or below {WET_CANOPY_NDMI:.2f} when VPD exceeds "
                    f"{HIGH_VPD_KPA:.1f} kPa and 30-day precipitation is under "
                    f"{DRY_PRECIP_30D_MM:.0f} mm"
                ),
                confidence_penalty=0.40,
            )
        )

    if (
        indicator in {IndicatorId.SOIL_MOISTURE_0_7CM, IndicatorId.SOIL_MOISTURE_7_28CM}
        and value > WET_SOIL_FRACTION
        and vpd is not None
        and precip is not None
        and vpd > HIGH_VPD_KPA
        and precip < SOIL_DRY_PRECIP_30D_MM
    ):
        flags.append(
            ValidationFlag(
                code="soil_atmosphere_incoherent",
                constraint="cross_variable_coherence",
                severity=Severity.WARN,
                message=(
                    f"Soil moisture reads {value:.3f} m3/m3 after {precip:.1f} mm of rain in "
                    f"30 days under a VPD of {vpd:.2f} kPa. Surface soil dries within days "
                    "under that demand."
                ),
                observed=value,
                expected=f"below {WET_SOIL_FRACTION:.2f} m3/m3 under sustained drying",
                confidence_penalty=0.35,
            )
        )

    if indicator is IndicatorId.NDVI:
        nbr = covariates.get(IndicatorId.NBR)
        if nbr is not None and abs(value - nbr) > SPECTRAL_DIVERGENCE_THRESHOLD:
            flags.append(
                ValidationFlag(
                    code="spectral_divergence",
                    constraint="cross_variable_coherence",
                    severity=Severity.WARN,
                    message=(
                        f"NDVI {value:.3f} and NBR {nbr:.3f} diverge by "
                        f"{abs(value - nbr):.3f}. They share their near-infrared term and "
                        "move together over vegetated ground, so a gap this wide points at "
                        "one of the shortwave-infrared bands."
                    ),
                    observed=abs(value - nbr),
                    expected=f"agreement within {SPECTRAL_DIVERGENCE_THRESHOLD:.1f}",
                    confidence_penalty=0.35,
                )
            )

    return flags


CONSTRAINT_IDS: tuple[str, ...] = (
    "physical_bounds",
    "plausible_range",
    "temporal_consistency",
    "cross_variable_coherence",
)
