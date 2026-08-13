# 11 — Components B through E, and the composite

**Status:** built and tested. The archive is now built too — the quota problem described
below turned out to be an acquisition-strategy bug rather than something to wait out, and it
is closed in milestone 15. Read "What was not done then" with that in mind.
**Gated on:** milestone 10's gate, which passed at +0.1410 (95% CI +0.1086 to +0.1773).

## What was built

Four components and the index that combines them, each a departure rather than a level.

| Component | Measures | Reference it departs from |
|---|---|---|
| B — water balance | 90-day climatic water balance, plus 30-day antecedent soil moisture at two depths | the same node's own record for the same calendar window, 1985 onward |
| C — riparian | share of the cell within 30 m of mapped water, weighted by how the corridor's canopy compares with the matrix around it | the cell's own BEC subzone-variant |
| D — fuel moisture | Drought Code, Buildup Index and vapour pressure deficit | the same node's own distribution for the same date in the season |
| E — drought | SPEI at 1, 3 and 12 months | a log-logistic fitted to the same calendar month across the reference years |
| composite | equal-weighted mean of whichever components a cell has | — |

Everything is a departure for one reason. A component reporting levels produces a map of the
Okanagan's climate gradient and calls it a hazard finding: the valley floor runs a Drought
Code of 400 most Augusts, and a layer that flags it every year has told an underwriter
nothing they could not read off a rainfall map.

## Decisions worth arguing with

**Equal weights are an admission, not a result.** Only Component A has been through a gate.
Nothing in this build establishes that structure matters as much as drought, and inventing a
weighting to look sophisticated would be inventing a finding. The weights are a constant in
one file with a note saying exactly this.

**One orientation for all five.** Positive is the direction associated with more severe fire.
Component C's raw quantity runs the other way — more intact riparian ground is better
condition — so its `SIGN` is negative and the inversion is a single constant rather than a
minus sign inside an expression. Component D's inputs climb as things dry while Component B's
fall, which is why the shared standardiser takes a `high_is_dry` argument rather than
assuming a direction.

**Missing is not average.** A cell without Component C is scored on the components it has.
Zero is the middle of a departure scale and the strongest available claim of ordinariness,
which is the worst possible thing to say about something never measured.
`contributing_components` is how a reader tells a five-component composite from a
one-component reading wearing the same name.

**The parts survive beside the combination.** Every component persists its own inputs — three
z-scores for B, extent and vigour for C, three codes for D, three timescales for E. A model
that disagrees with a weighting can only ignore it if the pieces are still there.

## Two source findings, both recorded as divergences

**D-014.** Open-Meteo's `era5_land` returns `null` for `precipitation_sum` and
`et0_fao_evapotranspiration` over this area — the same partial-variable-set behaviour that
returned nulls for 10 m wind and silently took FFMC, ISI and FWI with it. It does carry soil
moisture. So Component B's balance comes from `era5_seamless` at about 25 km and its soil
moisture from `era5_land` at about 9 km, and the two halves are reported as separate columns
because they are separate measurements.

**D-015.** SPEIbase v2.11 is anonymously reachable and reads fine over HTTP byte ranges —
`sources/speibase.py` pulls the study cells in about twenty requests rather than a 376 MB
download — but its record ends in December 2022, and the case study is an August 2023 fire.
So Component E computes SPEI by Vicente-Serrano's published method and is checked against the
published product over the 2015–2022 overlap:

| timescale | n | correlation | mean difference | mean absolute difference | agrees on SPEI < -1 |
|---|---|---|---|---|---|
| 1 month | 384 | 0.824 | -0.002 | 0.503 | 88.3% |
| 3 month | 305 | 0.872 | -0.020 | 0.458 | 89.2% |
| 12 month | 328 | 0.754 | +0.112 | 0.463 | 91.5% |

Unbiased and it ranks the same months dry, but half a SPEI unit of mean absolute difference
is half a standard deviation of the quantity itself. Three reasons, none a defect: forty
years of reference against SPEIbase's hundred and twenty, FAO-56 reference evapotranspiration
against Penman-Monteith potential, and ERA5 at 25 km against CRU at half a degree. The bounds
are pinned in `tests/eii/test_drought.py`.

## What was not done then, and is now

> **Closed 2026-08-13.** Everything in this section is history. Open-Meteo publishes the
> archive its own API serves from, on an anonymous S3 bucket with no metering; the backfill
> that could not finish in a day now finishes in twenty minutes. See milestone 15, D-016,
> D-017 and D-018.


The B, D and E **archive** is not built. The code is complete and tested against recorded
fixtures; the fetch is not, because Open-Meteo's daily quota was exhausted partway through.
The reason is worth writing down rather than treating as bad luck: the archive meters by call
weight, roughly `ceil(days / 14) * locations`, so eighty-eight lattice nodes over thirty-eight
years is about ninety thousand weighted calls no matter how they are batched. That is hours
against a free tier, and one afternoon's recon plus two aborted runs spent the day's budget.

Three things now make the resumption cheap rather than a restart:

- fetches are cached per chunk of eight nodes, so what was fetched stays fetched;
- request batches are sized by estimated call weight rather than by how many points fit in a
  URL, which is what stopped ten-location requests being refused outright;
- a *daily* refusal now raises `DailyQuotaExhaustedError` immediately instead of retrying
  eight times over nine minutes against a limit that clears tomorrow.

Resume with the same call; it will skip every chunk already on disk.

## Verification

`uv run pytest -q` covers the lattice, all four components and the composite: 500 tests, all
passing. The Component E agreement bounds are measured rather than asserted. No test touches
the network.
