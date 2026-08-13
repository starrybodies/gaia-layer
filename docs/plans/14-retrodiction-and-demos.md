# 14 — Kelowna 2023, and the two demos

**Status:** built and tested; the retrodiction runs against the real archive.

## The retrodiction

McDougall Creek ran on 15 August 2023 and took homes at Traders Cove and Wilson Landing. A
case study is worth nothing unless the model is asked the question in the order it actually
arrives, so this is a strict temporal hold-out: fit on every fire before 2023, predict the
McDougall Creek perimeter, and only then look at what burned.

### Two things that nearly made it dishonest

**Picking the fire by size.** The first version took the largest 2023 fire in the study area
and reported confidently on `2023_854` — Crater Creek, 36,013 ha, a hundred kilometres south
of Kelowna. McDougall Creek is `2023_834`, 12,969 ha, centred at 49.95 N. NBAC's ids are
opaque and the demo would have looked entirely correct while describing the wrong ground. The
fire is now identified by the ground it covers: whichever perimeter holds the labelled cell
that Traders Cove sits in.

**Typing the coordinates in.** The two communities the demonstration turns on would otherwise
have been the least verifiable numbers in the build, at the exact point where it makes its
claim, with nothing downstream able to catch a wrong one — a plausible latitude gives a
plausible cell and a plausible answer about the wrong place. They now come from the
province's own gazetteer (`sources/gazetteer.py`, anonymous, answering in BC Albers, which is
already the analysis CRS), and the lookup carries a source record like any other measurement.

### What it found

Against `2023_834`, trained on 2,681 cells from 2018–2022 and predicting 200 labelled cells:

| | |
|---|---|
| Traders Cove | p = 0.0016 — not flagged, and did not burn at high severity |
| Wilson Landing | **not scored** |
| cells that burned at high severity | 11 |
| cells flagged in advance | 40 |
| hits / misses / false alarms | 7 / 4 / 33 |
| recall | 0.636 |
| precision | 0.175 |

Wilson Landing is not scored because its cell carries no severity label at all. The labelling
drops cells only partly inside a perimeter and cells with no usable imagery either side of
the fire, and a lakeshore community at a fire's edge is exactly both. That is reported rather
than dropped: the model was never asked about this ground, which is not the same as getting
it right. Distinguishing "no label anywhere" from "labelled under a different fire" is a
separate branch, because they are different facts and only one is about the fire.

Precision of 0.175 against a recall of 0.636 is what it is. The flag is the top fifth of the
fire's own cells by predicted probability, stated before the result was looked at, and stated
as a share rather than a probability because the model's levels are known to be over-confident
in the tail while its ranking is what the gate tested.

### One caveat the framing must not swallow

The fire weather in the feature table is computed at the perimeter's **recorded** start date,
which is NBAC's date of first record — 1 July 2023 for this perimeter, not the 15 August
blow-up. So "as of 14 August" describes when the question is asked, not the vintage of every
input behind the answer. A codes-at-14-August retrodiction would be a different and probably
stronger exercise. Saying so is cheaper than being caught assuming it.

### What it does not establish

The target is remotely sensed burn severity. Only 11 of 200 labelled cells crossed the
high-severity threshold in a fire that destroyed homes, which is the clearest possible
statement that spectral severity and structure loss are different quantities. The
structure-loss figures — 189 reported by the Central Okanagan Emergency Operations Centre in
August 2023, revised to 303 in the province's 2025 investigation, 13,970 ha, CAD 480 million
insured per IBC and CatIQ — are case-study context, are labelled as such in the code and in
the output, and nothing was trained on them.

## The two demos

    pnpm demo:kelowna    # the retrodiction above, end to end
    pnpm demo:agent      # the walkthrough an agent would actually do

`demo:agent` goes in the order an agent should: read `eii://schema` and `eii://methodology`
*first*, because the one thing it cannot recover from is reading the scale backwards, then ask
what the index says, then why, then whether any of it beats the baseline, then scan a
portfolio, and finish on the audit entry that proves what was served.

Both fail with an explanation rather than a stack trace when their inputs are missing.

## Verification

`tests/validate/test_retrodiction.py`, 14 tests. The two pinned properties are the ones that
would make the case study a lie: that the fire's own year never enters the training set, and
that a place which burned unflagged is reported with the word MISSED rather than dropped.
