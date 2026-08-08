# Milestone 5 — Climate, soil moisture, terrain, and the substrate score

**Status:** done

## Sources

| Family | Source | Route | Native resolution |
|---|---|---|---|
| Climate, soil | ERA5-Land | Open-Meteo historical archive (D-002) | ~9 km, daily |
| Terrain | Copernicus DEM GLO-30 | Earth Search STAC, HTTPS not `s3://` | 30 m |

The DEM's STAC item advertises an `s3://` href, which GDAL will only follow with
credentials configured. The bucket is public over HTTPS, so the adapter rewrites the URI.
One line, and it is the difference between a cold start that works and one that asks the
operator for AWS keys.

## Indicators added

**Climate.** Vapour pressure deficit, maximum temperature, monthly precipitation, days
since measurable rain. VPD comes from the daily *maximum* temperature rather than the mean,
because fuel dries fastest in the afternoon and it is the afternoon that carries fire.

Days since rain earns its place separately from the precipitation total. Two months with
identical rainfall are different fire propositions if one of them ended dry, and a monthly
sum cannot tell them apart.

**Soil.** ERA5-Land volumetric moisture at 0–7 cm and 7–28 cm. The shallow layer governs
the duff and litter where most ignitions establish.

**Terrain.** Elevation, slope, aspect (Horn 1981), and topographic wetness (Beven and
Kirkby 1979). Sea surface is removed by discarding elevations at or below 0.5 m — the DEM
reports the strait as zero, which would otherwise render it as flat, maximally wet land.
Aspect on flat ground is NaN rather than north, because returning north for a horizontal
plane would put a spurious cold, wet aspect across every valley bottom.

TWI is computed at 100 m and resampled; see D-006.

## The substrate score

Seven components, weighted. The weights are a stated judgement, not an empirical fit, and
that is said plainly in the method notes returned with every score — no fire-occurrence
training was performed, and claiming otherwise would be the exact failure this layer
exists to avoid.

| Indicator | Weight | Why |
|---|---|---|
| NDMI | 0.30 | The most direct available measurement of live fuel moisture, which is the strongest control on whether fire entering a stand spreads through the crowns |
| VPD | 0.22 | Leads the spectral indicators — the air dries the fuel before the satellite can see that it has |
| Soil moisture 0–7 cm | 0.18 | Governs the duff layer where fires establish and smoulder |
| Days since rain | 0.12 | Dead fine fuels respond in hours, invisible to a monthly composite |
| NDVI | 0.08 | Saturates over closed canopy, so it discriminates poorly at exactly the high-fuel end |
| TWI | 0.06 | Static; explains where moisture reads low in the same places every year |
| Slope | 0.04 | Describes how fire would move rather than whether the substrate carries it |

Anchors are set for the Coastal Douglas-fir zone. They are not global constants, which is
why the scheme is named and versioned (`gaia-wildfire-substrate-v1`) and returned with
every score. A different bioregion needs re-anchoring, and that is stated in the caveats.

**Renormalisation.** Weights are rescaled across the components actually present, and the
missing ones are listed in the response. Below 60% of the scheme's weight, no score is
produced at all: a composite built from a third of its inputs is not a weaker version of
the score, it is a different index wearing the same name.

**Confidence is the minimum across contributing indicators**, not the mean. A composite is
no stronger than the weakest measurement inside it, and averaging would let a confident
static terrain layer paper over a month of solid cloud.

**Rejected indicators are treated as absent, not as zero.** Zero is a measurement; absence
is not.

## What the score is not

Returned with every response, not buried in documentation:

- Ignition probability is not modelled. This is receptiveness, not likelihood.
- Wind is absent. It dominates behaviour once burning, and it is weather, not substrate.
- Fuel load and stand structure are not measured. Identical spectral moisture can sit over
  very different tonnes per hectare.
- Weights are a judgement anchored to one bioregion.
- Values are monthly composites and cannot resolve a drying event inside a month.
