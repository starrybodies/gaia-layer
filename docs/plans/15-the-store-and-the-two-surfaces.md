# 15 — The climate store, and the two surfaces built on it

**Status:** built and tested. Components B, D and E have their archive; the diligence
workbench and the portfolio surface render it.

## The acquisition strategy was the bug

Milestone 11 shipped Components B, D and E code-complete and data-empty. The reason was
recorded as D-016 and treated as a quota to be waited out. It was not. Open-Meteo's free
archive tier meters by *call weight*, roughly `ceil(days / 14) * ceil(variables / 10) *
locations`, and a thirty-eight year water balance at eighty-eight lattice nodes is about
ninety thousand weighted calls against a minutely allowance near six hundred. No batching,
pacing or backoff changes that arithmetic. Three runs died proving it.

Open-Meteo publishes the archive its own API serves from. `.om` files on an anonymous S3
bucket, readable by HTTP byte range, no account and no metering. `sources/om_archive.py`
reads it; `sources/climate.py` sits on top with the same public surface it always had; the
retry, pacing, weight-estimation and daily-quota machinery is **deleted** rather than tuned.

The whole eighty-eight node lattice, one variable, one year is about two hundred range
requests and under a megabyte. The backfill that could not finish in a day now finishes in
twenty minutes, and five of those are the parts that were never the problem.

### Verified before it was built on

This repository has twice adopted a source that catalogued fine and returned nothing (D-009,
D-014), so the store was checked variable by variable against the API it replaces before a
line of `climate.py` changed. `docs/climate-store.md` is that check, written by
`scripts/verify_climate_store.py`:

| variable | agreement with the API |
|---|---|
| precipitation, shortwave radiation, both soil moisture layers | exact |
| 10 m wind, from the u and v components | 0.05 km/h, which is the API's rounding |
| temperature, dew point | **3.0 K colder**, every hour |

That last row is the API's elevation downscale, not a defect: the ERA5 cell sits at 959 m,
the API corrects to the coordinate's 499 m, and 460 m at the standard lapse rate is 2.99 K
against 2.97 K measured. It is left uncorrected, because B, D and E are every one of them a
departure from the same node's own record and a constant offset cancels in a departure.
Recorded as **D-017**, along with what that costs anyone wanting an absolute reading.

Reference evapotranspiration and relative humidity are not in the store — the API computes
them — so they are computed here, by published equations, through `refet` for ET0 and the
FAO-56 vapour pressure curve for humidity. Both are measured rather than assumed: ET0 agrees
at r = 0.9912 with a bias of -0.13 mm/day and to within 1.5% through the fire season, and
humidity to 0.27 percentage points. Recorded as **D-018**.

## Two bugs the backfill found, both of the same family

**Component E fitted zero of eighty-eight nodes.** `monthly_balance` refuses a month it has
only part of — a half-summed August reads as a dry August — and `spei_at` asked it for the
month containing the as-of date, which for any as-of that is not a month end guarantees the
answer is missing. Nothing raised. The fix is that SPEI reports the last month *complete* at
the as-of date, so 14 August 2023 carries July 2023's drought. That lag is inherent to a
monthly index and now says so in the method record.

**Four hundred and sixty NaNs were in the archive where NULL was meant.** A reader counting
`value IS NOT NULL` scored them, a reader sorting on value put them wherever its collation
happened to, and JSON turned them into `null` anyway — three answers to one question. Non-
finite floats are now nulled at the archive boundary, which is the one place that can be
sure it is the boundary.

Both are the shape D-012 records for elevation: a missing measurement that arrives as a
number and is therefore never questioned.

## What the backfill then found about Component E

SPEI-1 fits 88 of 88 nodes and SPEI-12 fits 85. **SPEI-3 fits 13.** Not a data gap — every
node has a complete accumulation and a 38-season reference. The three-month summer balance in
this valley is left-skewed, and Vicente-Serrano's log-logistic is right-skewed by
construction. The estimator says so by returning a shape below one, and the honest response
is the refusal rather than a forced fit. **D-019** records it, including what would close it
and why closing it tonight would have been worse than leaving it open: switching distribution
family invalidates the SPEIbase agreement in D-015, which is the only evidence that this
implementation computes the same quantity SPEIbase publishes.

## Surface B — the diligence workbench

Built for a model-validation team trying to break it, which sets one architectural rule and
one editorial one.

**The dashboard renders; it never computes.** Every figure, every ordering and every sentence
of interpretation is produced by `validate/dossier.py`, written to `data/eii/dossier.json`
with the run id, method record and source set behind it, and reproduced verbatim. There is no
arithmetic in the page. A figure screenshotted off it resolves to a run; a figure computed in
a browser resolves to nobody.

Enforcing that ran into the provenance guard, which refuses to serve a `value` without a
chain beside it. The right answer was not to exempt the route. The v0.2 archive stores
provenance *by reference* — `run_id`, `method_id`, `source_set_id` — because inlining a chain
onto twenty-five million rows costs gigabytes to repeat one paragraph, and a figure carrying
those three references is citable in exactly the sense the guard protects. The guard now
accepts that second shape and still refuses a number carrying neither.

**The analyst must not find anything the dossier did not tell them first.** Three findings
are unflattering and all three are sections of their own, rendered ahead of the verdict and
marked as disclosures:

- Under leave-one-fire-out the gate's own baseline scores **0.1039** against a prevalence of
  **0.1064**. A model predicting the base rate everywhere scores prevalence, so the baseline
  is at or below the no-skill line and the candidate's +0.1638 margin is not "beats a working
  fire-weather model by 0.16". The wording is derived from the comparison rather than typed
  in — a test flips the inputs and requires the sentence to flip with them.
- Refitting without fire weather (-0.0046) and without fuel type (-0.0040) each score
  marginally *higher*. Within this evaluated set the two variables the industry prices on
  carry no measurable lift, stated together with the range restriction that explains it: the
  labels exist only where fire weather was already sufficient for a fire.
- `a_score`, the composite column, has a permutation importance of **-0.0030** while its
  three standardised inputs carry +0.0414, +0.0232 and -0.0011. The model uses the parts, not
  the composite.

Beside them: the coverage table (7 of 42 fires scorable; 2019, 2020 and 2022 carry no
high-severity cell at all), the miss characterisation (ISI 9.35 in missed cells against 5.56
in caught ones — it fails worst where fire weather is worst), every stratum including the
unscorable ones with their reasons, all five models rather than the two the gate compares,
and the exclusion bookkeeping including the years for which there is none.

The diagnostics artifact was rebuilt on the way, because the one on disk predated the
per-dimension coverage fix and still claimed to cover "9,836 of 3,835 cells" — a figure that
counts every cell once per stratum dimension. The dossier computes coverage inside each
dimension and a test pins that it cannot pool again.

## Surface C — the portfolio

**C3, ranking.** A book of H3 cell identifiers and the client's own exposure weights goes to
`portfolioRanking`, which reads each cell's persisted value with its run, ranks descending so
rank 1 is the cell furthest in the direction associated with more severe fire, rolls up to
res-7 parents, writes an audit row and returns its own method justification. Cells the
archive cannot score carry a null rank and are listed by id: an unmeasured cell is not a good
cell, and a book mean that improves as coverage falls is the failure this surface exists to
make visible.

**C2, change.** `portfolioChange` compares two as-of dates across the archive's year
partitions. A cell scored in one period and not the other is *not comparable* and contributes
nothing to the mean, because a change computed against a missing value is a change invented
by the arithmetic. Both run ids travel with every cell, so a change spanning a method change
can be seen rather than assumed away.

**The privacy shape is the API shape.** These endpoints take cell identifiers. There is no
parameter for an address, a coordinate or a policy number and no code path that would use
one. A res-8 cell is about 0.74 km², which is the finest thing this layer needs to know about
a risk, so it is the only thing it accepts.

The demo book is built from Overture's open building footprints — anonymous, release
2026-07-22.0, 174,218 buildings over the study area — reduced to 400 res-8 cells. Every value
in it is synthetic and it says so at the top level, in the warning, and in the field name.
Its first version had every value at zero: `ST_Area_Spheroid` returns NaN for Overture's
typed geometry column and the aggregation treated a non-finite area as no area. The same
silent-zero family again, caught this time by a test that requires every cell to carry a
positive value.

## The agent surface, kept in step

The rule this codebase states for itself is that a behaviour present in one transport and
absent from the other is in the wrong place. Adding three REST endpoints without adding three
MCP tools would have broken it quietly, so `portfolio_ranking`, `portfolio_change` and
`read_dossier` are dispatched from the single `callEiiTool` switch that both transports go
through, and the first test in the new mcp-server suite compares the advertised tool list
against the dispatched one **in both directions**. Adding a tool to one side now fails a test
rather than a support thread.

That suite is also what closes the last failing gate: `pnpm test` used to fail on
mcp-server having no test files at all.

## The map, and the hexagon in the Arctic

The portfolio surface now draws the book. A ranked table answers *which cells*; it does not
answer *where*, and four hundred cells spread across a valley versus stacked on one interface
are the same table and a very different accumulation. Unmeasured cells are drawn in grey
rather than omitted, because a map that leaves them out shows a portfolio with no holes in
it.

Turning a cell id into a ring moved into `@gaia/core` on the way, for a reason worth
recording: **`h3-js` does not refuse a bad cell id.** Given `"not-a-cell"`, `"zzz"` or the
empty string, `cellToBoundary` returns the same well-formed hexagon at 69 N, 31 E. A book
with one mistyped cell would render a polygon in Arctic Russia, and on a map that is
indistinguishable from a layer that failed to draw. `cellToRing` checks `isValidCell` before
the call rather than inspecting what came back, and does the `[lat, lng]` to `[lng, lat]`
swap in one tested place instead of at each call site.

## Verification

- `uv run pytest -q` over the pipeline, including the new `om_archive`, rewritten `climate`,
  `dossier`, `demo_book`, drought-month and archive-null suites.
- `pnpm lint`, `pnpm typecheck`, and `pnpm test` across all four packages: core 25 (guard
  including the new archived-figure shape, and the H3 ring), service 63 (portfolio ranking
  and change against a two-year fixture archive), mcp-server 15 (new), api 7.
- Both surfaces driven in a real browser against the real archive: the diligence page renders
  all three disclosures ahead of the verdict, and the portfolio page ranks 400 of 400 cells
  into 30 res-7 parents.
- `make check` passes. The mcp-server "no test files" failure it used to carry is closed by
  the suite above.
