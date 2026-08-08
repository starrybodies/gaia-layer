# Milestone 4 — Agent interface

**Status:** done

## Shape

```
mcp-server/  ─┐
              ├─> @gaia/service ──> claims.duckdb (write) + gaia.duckdb (read-only attach)
api/         ─┘
```

Both transports are adapters. Every handler in either is parse, call, return. The prompt's
"no logic duplication" is made structural rather than aspirational: the service lives in
its own package, and neither transport can reach the database except through it.

## The five tools

| Tool | Answers |
|---|---|
| `get_ecological_state` | Condition and trend over a period, per indicator, with envelopes |
| `get_wildfire_substrate_score` | Composite score with full decomposition |
| `get_provenance` | Any previously served number, traced to source observations |
| `compare_periods` | Change between two periods, with significance |
| `list_coverage` | What the layer can currently answer for |

Two more routes exist for the console — cell geometry and the period list — and are marked
as such. They are not part of the agent surface.

## Decisions

**Claim ids are derived, not minted.** A claim id is a hash of the claim's content: kind,
geometry, indicator, period, algorithm version, and the value itself. Two consequences,
both wanted. Asking the same question twice returns the same id, so the claim table
converges instead of growing without bound. And a re-ingest that changes a number produces
a *different* id, leaving the old claim row intact with its original provenance — so a
figure someone cited last month still resolves to what they were actually shown.

The hash is SHA-256 truncated to 128 bits, encoded as 26 Crockford base32 characters.
BLAKE2b-128 was the first choice and had to be abandoned: Node's crypto exposes BLAKE2b
only at 512 bits, and BLAKE2b at a different digest length is a different function rather
than a truncation of one. Verified byte-identical across both languages before relying on
it.

**The claim ledger is a separate database file.** DuckDB permits one writer per file, and
the pipeline holds the lake's write lock for the length of an ingest. Co-locating the claim
table with the measurements meant the API could not record what it served while data was
arriving — it failed outright the first time both ran together. The service now owns
`claims.duckdb` and attaches `gaia.duckdb` read-only, which also allows several API
processes to read concurrently. See D-005.

**A geometry with no coverage is an error.** `aoi_not_ingested`, with the available areas
listed and the command to register a new one. The alternative — serving an enclosing area's
average — would give the caller a number they could not distinguish from a measurement of
their own parcel. That is exactly the undefendable figure this layer exists to refuse.

**Trends carry their significance.** Ordinary least squares on the monthly series, with a
two-sided t test on the slope, requiring at least four observations. Below that threshold,
or above p = 0.05, the direction is reported as `stable` rather than as a trend. A slope
smaller than 2% of the series' own range is also reported as stable, because "increasing at
1e-9 per month" is true and useless.

**Period comparison discounts autocorrelation.** Welch's t test on an *effective* sample
size, not the raw pixel count. Neighbouring 20 m pixels are not independent observations,
and treating 5.8 million of them as though they were would make a difference of 0.001 look
overwhelmingly significant. The discount assumes a 200 m decorrelation length and says so
in the `significance_method` field.

**Summaries are templates.** Every clause in a `summary` string is a direct function of a
validated number. No model writes them. A summary that paraphrased the numbers would be a
quantitative claim wearing a sentence, which is the failure mode lesson 2 exists to
prevent.

**The guard runs twice.** Once in the service layer before any response is returned, once
in the API as JSON leaves the process. Both walk the response structurally and fail if any
object carrying a `value` lacks a provenance chain, a validation status, a confidence, or a
method. Disabled only by `GAIA_STRICT_GUARD=0`, which the runbook says not to do.
