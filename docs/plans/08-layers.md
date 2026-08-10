# Milestone 8 — Heat load, land cover, and what the year did

**Status:** pipeline done; console and service wired; categorical landscape reading open

Milestone 6 left the map with eleven layers, all of which answered the same shape of
question: what is this place like *now*. Four gaps were visible as soon as the landscape
readings existed to expose them.

## What was added

**Heat load (`heat_load`).** McCune and Keon (2002), from slope, aspect and latitude. The
moisture layers keep producing aspect gradients — the dry decile sits on one set of faces —
and until now the layer could describe that pattern without naming its cause. Heat load is
the cause, measured on the same grid, so a reading can correlate against it instead of
gesturing at insolation.

Aspect is folded about the northeast–southwest axis, so southwest scores highest rather
than due south. Afternoon sun falls on ground the morning already warmed; treating south as
the peak is the common mistake and it moves the hot faces by 45 degrees.

**Land cover (`land_cover`).** ESA WorldCover 10 m, 2021 epoch, from the public S3 bucket
over anonymous HTTPS. This was the single largest gap in the layer's interpretive power.
Every reading the engine produced — *the driest decile sits at 100 m on south-facing
slopes* — meant something quite different depending on whether that ground was closed
conifer, a regenerating clearcut, or pasture. The layer had no way to tell the difference.

**Annual minimum and seasonal swing (`*_annual_min`, `*_amplitude`).** Both come free from
the monthly stack already in memory during a cell rebuild. The minimum is how bad it gets
rather than how it is; the amplitude separates fuels that cure from canopy that holds
moisture through the season. Computed for the three spectral indices only, and only where
at least six months exist.

**Burn severity (`dnbr`).** NBR differenced between the first and last month on record,
which is the actual severity product — standing NBR is only its input. Over a twelve-month
window this mostly shows harvest rather than fire. That is still the disturbance that
changed the fuel, and it is also the correction for D-007: where cover class is stale
because ground was cleared after 2021, dNBR is where that shows up.

Twenty cell layers now, across 858,000 cells.

## Decisions worth recording

**A categorical layer is never averaged.** Land cover cells take the majority class, not
the block mean. The mean of grassland (30) and built-up (50) is 40, which is cropland: a
real code, a plausible-looking map, and a wrong answer. `CellGrid.majority` returns the winning class
and the share of the cell it covers, so a cell that is 51 per cent forest can be told from
one that is 100 per cent forest.

**The refusal extends to interpretation.** `interpretLayer` throws
`indicator_unavailable` for a categorical layer rather than computing quantiles, zonal
means and a Pearson correlation against slope. Those are arithmetic on labels. The map
draws land cover as flat class colours with no interpolation anywhere in the paint
expression, because a gradient between two class codes would imply an ordering the data
does not have.

**One drying direction, derived rather than tabulated.** Which end of a layer is the dry
end lived in three places and had already drifted between them. It is now
`dryingDirection()` in `@gaia/core`: a table for measured indicators, and a rule for the
derived ones — a departure and an annual minimum run with their parent, a seasonal swing
always runs the other way, because a wide annual range is fuel that cures whichever index
measured it.

**Heat load is clipped at zero.** The published equation is a regression fit and takes
near-vertical northeast faces in the tropics a fraction below zero. A negative annual heat
load is not a thing. Clipping the artefact keeps the hard validation bound at zero
meaningful, rather than widening the bound to admit it.

## Tests

`pipeline/tests/test_layers.py` covers the physics rather than the plumbing: southwest is
the hottest aspect and northeast the coolest, the folding is symmetric about that axis,
flat ground ignores aspect entirely, a steeper facet widens the spread, missing slope stays
missing, and a Hypothesis sweep of every slope, aspect and latitude stays inside the
validated envelope. For majority aggregation: the commonest class wins, no class that is
absent can be invented, masked pixels do not vote, and an empty cell is missing rather than
zero.

## Not done

- **A categorical landscape reading.** Land cover currently refuses interpretation instead
  of offering the reading that suits it: class composition, the dominant class per
  elevation band and aspect octant, and which classes the fire-prone decile actually sits
  on. That is the version of the feature worth having.
- **Land cover in the substrate score.** `FUEL_CHARACTER` states how each class behaves as
  fuel, and nothing reads it yet. Adding a cover term to the composite is a change to the
  weights, which is a stated judgement and needs its own justification.
- **Redeploy.** The lake grew with the new layers and needs compacting before it ships
  inside the serverless function.
