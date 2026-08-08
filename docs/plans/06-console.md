# Milestone 6 — Console

**Status:** done

Three views, all reading the same REST API an agent uses. The console gets no privileged
path into the data, so what a visitor sees on screen is what an agent receives.

## Map

MapLibre GL with a Carto raster basemap — no Mapbox token, nothing to configure. The
indicator layer is the 500 m cell grid the pipeline writes; full-resolution COGs stay on
disk and are never shipped to a browser.

Two decisions worth recording:

**Colour ramp reverses per indicator.** The fire-prone end is always the warm end. NDMI
falling and VPD rising both mean drier, and a reader should not have to remember which
indicator runs which way to read the map.

**Stops are percentiles, not min and max.** One outlier cell against a linear min–max ramp
flattens everything else into a single colour.

**A clicked cell is citable.** The cell reading is a display aggregate, so the response
carries the parent envelope with it — clicking shows the cell's number and the full
provenance of the area value it came from. The alternative, a bare number in a popup, is
the failure mode this layer exists to avoid, in miniature.

## Substrate report

The artefact for an insurer or conservation call. Headline score and band, the full
decomposition table (measured, normalised, weight, points, and why each indicator is in the
scheme), trends with significance, and the claim id of every figure.

Two things are on the page rather than in a footnote: values the constraint engine
**rejected**, reported as rejected with the reason, and the five things the score does not
model. A report that hid either would be more persuasive and less defensible.

The provenance chain shown in full is the score's heaviest component, not the
alphabetically first indicator — otherwise the reader meets "days since rain" where they
came to see how the satellite work is done.

## Agent playground

A bounded tool-use loop (8 turns) with the same five tools bound to the same REST API. The
system prompt forbids stating any ecological figure that did not come back from a tool
call, and requires confidence, validation status and claim id alongside every number.

The full transcript of calls and raw responses renders below the answer, so a visitor can
check the model's prose against what the layer actually said. That is the point of the
view: not that the model sounds knowledgeable, but that its claims are checkable.

Needs `ANTHROPIC_API_KEY`. Without it the view says so plainly and the other two keep
working, because they never needed a model.

## Design

Institutional rather than ecological — dark neutral base, one accent, IBM Plex, tabular
figures. Colour is reserved for meaning: validation status and score band, never chrome.
Every number that appears anywhere is accompanied by its confidence and a route to its
provenance. That is lesson 2 expressed in the interface and not only in the schema.
