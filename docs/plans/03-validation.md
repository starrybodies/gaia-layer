# Milestone 3 — Validation constraint engine

**Status:** done

## Order note

Built before milestone 2's ingestion rather than after it, which reverses the build
sequence in the prompt. The reason is a hard dependency, not a preference: the schema has
no representation for a value without a validation status, so the ingest could not write a
single row until the engine existed. Building ingestion first would have meant either
stubbing the status — which the prompt explicitly forbids for this component — or writing
rows the schema rejects.

The artefacts are the same; only the commit order changed.

## What the engine decides

Three outcomes, and the distinction between them is the whole design.

| Outcome | Meaning | Served? |
|---|---|---|
| `validated` | Passed every check. | Yes |
| `flagged` | May well be right; something about it is odd and the caller should know. | Yes, with the flags attached |
| `rejected` | Not a measurement. Corrupt input, or outside what the quantity physically can be. | Never |

Rejection is reserved for cases where arithmetic or physics forbids the value. Everything
else flags. A layer that rejected whatever surprised it could not see a heat dome, and the
whole point of measuring substrate is to catch the year that does not look like the others.

## The three constraint classes

**1. Hard physical bounds.** Properties of the quantity itself. A normalised difference of
two non-negative reflectances cannot leave [-1, 1] — so a value that does means the input
was corrupt, not that the vegetation did something interesting. NaN and infinity reject
here too.

**2. Plausible range.** Physically possible, ecologically extreme for the Coastal
Douglas-fir zone. VPD of 7.5 kPa is real somewhere; here it flags.

**3a. Temporal consistency.** Rate limits, **asymmetric by design**. Canopy moisture can
collapse in a month — fire, harvest, windthrow, a heat dome — but it cannot climb back by
the same amount in a month, because the leaves have to regrow first. So NDMI may fall at
0.70/month and rise at only 0.25/month. A rise that outpaces growth is far more likely to
be a masking failure in one of the two composites than a forest recovering in a fortnight.
This is the case the build prompt names, and it is a test.

Rates are per month, not per step, so a jump across a six-month gap is not treated like a
jump across one. Terrain is exempt: slope does not change between months, and a difference
would mean the elevation model was replaced.

**3b. Cross-variable coherence.** Three rules:
- Wet canopy (NDMI > 0.25) under high atmospheric demand (VPD > 2.0 kPa) with under 10 mm
  of rain in 30 days. The prompt's example. Almost always residual cloud.
- Wet surface soil under sustained drying.
- NDVI and NBR diverging by more than 1.0. They share their near-infrared term, so a gap
  that wide points at one of the shortwave-infrared bands.

Absent covariates produce no coherence flag. Absence of evidence is not evidence of
incoherence.

## Confidence

Four named components, each with a stated weight, combined as a weighted mean and then
reduced multiplicatively by each flag raised:

| Component | Weight | Rationale |
|---|---|---|
| spatial coverage | 0.35 | The failure that misleads most quietly — a value describing a quarter of an area looks like a normal answer. |
| observation count | 0.25 | Saturates at 6, about the best Sentinel-2 offers at this latitude. |
| clear sky | 0.25 | One minus mean cloud fraction over land. |
| revisit regularity | 0.15 | Ideal at 5 days, worthless by 45. |

Missing cloud or revisit information scores 0.5, not 1.0. Assuming the best about data you
do not have is how optimistic numbers get served.

## Tests

Written before the engine. 100 cases: one class per constraint class, plus confidence, plus
Hypothesis properties that must hold for any input at all —

- confidence always lands in [0, 1], for every indicator and any float
- status and flags can never disagree (a rejection carries an error flag; an error flag
  forces a rejection; a validated value carries no flags)
- the engine never raises and always decides
- a value inside [-1, 1] is never rejected on bounds

One test had to change: `test_normalised_indices_accept_their_whole_range` asserted that
NDVI of -1.0 validates cleanly. It does not, and should not — an NDVI of -1.0 over land is
not something vegetation does, and flagging it is the designed behaviour that the VPD test
asserts in the other direction. The test was over-broad and was narrowed to "never
rejected", with the flagging behaviour covered separately. Recorded here because weakening
a check to make a test pass would have been the wrong fix, and this was the opposite.
