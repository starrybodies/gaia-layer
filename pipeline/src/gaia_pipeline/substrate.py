"""The wildfire substrate score.

This is the layer's opinion, and it is stated in one place so it can be argued with.

What it is: a 0-100 summary of how predisposed the *ground* is to carry fire, given that
fire arrives. What it is not: a probability of ignition, a forecast, or a statement about
fire weather on any particular day. The whitepaper's third lesson is to price the land and
not just the sky; this scores the land.

Two rules govern the design.

First, no black box. Every component's measured value, its normalisation, its weight and
the points it contributed are returned with the score. A land manager who cannot see which
indicator is driving their number cannot act on it, and an underwriter who cannot decompose
it cannot defend it.

Second, the weights are a judgement, not a measurement. They are set out below with the
reasoning for each, versioned as a named scheme, and returned with every score. When they
change, the scheme name changes, and old scores stay interpretable.
"""

from __future__ import annotations

from dataclasses import dataclass

from .schemas.common import IndicatorId
from .schemas.provenance import Method

WEIGHTING_SCHEME = "gaia-wildfire-substrate-v1"


@dataclass(frozen=True)
class Component:
    """One indicator's place in the score."""

    indicator: IndicatorId
    weight: float
    #: Value mapping to 0.0 — the least fire-prone end.
    benign: float
    #: Value mapping to 1.0 — the most fire-prone end.
    severe: float
    rationale: str

    def normalise(self, value: float) -> float:
        """Rescale to [0, 1] where 1 is the most fire-prone, clamping outside the range."""
        span = self.severe - self.benign
        if span == 0:
            return 0.0
        return max(0.0, min(1.0, (value - self.benign) / span))

    def normalisation_description(self) -> str:
        return (
            f"linear from {self.benign:g} (scored 0) to {self.severe:g} (scored 1), "
            "clamped outside that range"
        )


# Anchors are set for the Coastal Douglas-fir zone in the fire season. They are not global
# constants; a different bioregion needs a different scheme, which is why the scheme is
# named and versioned rather than assumed.
COMPONENTS: tuple[Component, ...] = (
    Component(
        indicator=IndicatorId.NDMI,
        weight=0.30,
        benign=0.40,
        severe=-0.10,
        rationale=(
            "Canopy liquid water is the closest open-data proxy for live fuel moisture, "
            "which is the single strongest control on whether a fire entering a stand "
            "spreads through the crowns or drops to the ground. It carries the most weight "
            "because it is the most direct measurement of the thing that matters."
        ),
    ),
    Component(
        indicator=IndicatorId.VPD_KPA,
        weight=0.22,
        benign=0.4,
        severe=3.0,
        rationale=(
            "Vapour pressure deficit is the atmosphere's pull on fuel moisture. It leads "
            "the spectral indicators: the air dries the fuel before the satellite can see "
            "the fuel has dried, so it carries information the reflectance bands do not "
            "have yet."
        ),
    ),
    Component(
        indicator=IndicatorId.SOIL_MOISTURE_0_7CM,
        weight=0.18,
        benign=0.35,
        severe=0.06,
        rationale=(
            "Surface soil moisture governs the duff and litter layer where most ignitions "
            "establish, and where a fire smoulders between runs. Dry surface soil is what "
            "turns a spot fire into a persistent one."
        ),
    ),
    Component(
        indicator=IndicatorId.DAYS_SINCE_RAIN,
        weight=0.12,
        benign=2.0,
        severe=45.0,
        rationale=(
            "Time since measurable rain captures the drying of dead fine fuels, which "
            "respond within hours and are invisible to a monthly composite. Two months "
            "with identical rainfall totals are different propositions if one of them "
            "ended dry."
        ),
    ),
    Component(
        indicator=IndicatorId.NDVI,
        weight=0.08,
        benign=0.80,
        severe=0.25,
        rationale=(
            "Greenness stands in for how much of the standing vegetation has cured. It is "
            "weighted lightly on purpose: NDVI saturates over the closed canopy that covers "
            "much of this area, so it discriminates poorly at exactly the high-biomass end "
            "where the fuel load is greatest."
        ),
    ),
    Component(
        indicator=IndicatorId.TWI,
        weight=0.06,
        benign=12.0,
        severe=3.0,
        rationale=(
            "Topographic wetness marks the convergent hollows that stay damp and the "
            "shedding slopes that do not. It is static, so it explains where the moisture "
            "indicators will read low in the same places every year."
        ),
    ),
    Component(
        indicator=IndicatorId.SLOPE_DEG,
        weight=0.04,
        benign=2.0,
        severe=35.0,
        rationale=(
            "Fire spreads faster uphill, so steeper ground is more predisposed. Weighted "
            "lightly because slope describes how fire would move rather than whether the "
            "substrate will carry it, and this is a substrate score."
        ),
    ),
)

SUBSTRATE_METHOD = Method(
    name=f"Gaia wildfire substrate score ({WEIGHTING_SCHEME})",
    citation=(
        "Composite index defined by this layer. Component indicators carry their own "
        "citations, returned with each component. Anchors follow fuel-moisture and "
        "fire-danger practice for the Coastal Douglas-fir zone: Lawson, B.D. and Armitage, "
        "O.B. (2008). Weather Guide for the Canadian Forest Fire Danger Rating System. "
        "Natural Resources Canada, Canadian Forest Service, Northern Forestry Centre."
    ),
    formula="score = 100 x sum(normalise(indicator) x weight)",
    notes=(
        "A substrate score, not a fire risk score. It describes the condition of the "
        "ground a fire would arrive at, and says nothing about ignition probability or "
        "fire weather on a given day. Weights are a stated judgement, versioned by scheme "
        "name, not an empirical fit — no fire-occurrence training was performed."
    ),
)

CAVEATS: tuple[str, ...] = (
    "Ignition probability is not modelled. This describes receptiveness, not likelihood.",
    "Wind is not included. Wind is the dominant control on fire behaviour once burning, "
    "and it is a weather variable rather than a substrate one.",
    "Fuel load and stand structure are not measured. Two areas with identical spectral "
    "moisture can carry very different tonnes per hectare.",
    "Weights are a stated judgement anchored to the Coastal Douglas-fir zone, not a fit to "
    "observed fire occurrence. Applying this scheme to another bioregion without "
    "re-anchoring it would be a mistake.",
    "Values are monthly composites. A score cannot resolve a drying event inside a month.",
)

# Below this share of the scheme's weight present, no score is produced. A composite built
# from a third of its inputs is not a weaker version of the score, it is a different index
# wearing the same name.
MINIMUM_WEIGHT_PRESENT = 0.6


def band_for(score: float) -> str:
    if score < 20:
        return "low"
    if score < 40:
        return "moderate"
    if score < 60:
        return "elevated"
    if score < 80:
        return "high"
    return "extreme"


def interpretation_for(score: float, band: str, top: str) -> str:
    lead = {
        "low": "Substrate is well hydrated with limited cured fuel.",
        "moderate": "Substrate shows seasonal drying within the normal range for this zone.",
        "elevated": "Fuel moisture is drawn down and the ground is more receptive than typical.",
        "high": "Substrate is substantially cured and dry across most of the area.",
        "extreme": "Substrate is at the dry end of the observed range across the whole area.",
    }[band]
    return f"{lead} Score {score:.1f} of 100, driven most by {top}."
