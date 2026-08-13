# 13 — The agent surface

**Status:** built and tested against a real archive; 26 tests.

## What was built

Four resources and five tools in `service/src/eii.ts`, exposed through the existing MCP
server. Both surfaces bind to the same functions, which is the only way an MCP client and an
HTTP client can be guaranteed to say the same thing about the same cell.

| Resource | Answers |
|---|---|
| `eii://schema` | what a fact row means, what null means, **and which way the scale runs** |
| `eii://catalog` | which components and years the archive holds, and when each was written |
| `eii://methodology` | every method record, its formula and citation, and why the weights are equal |
| `eii://validation` | the pre-registered gate, its verdict, and every model's metrics |

| Tool | Answers |
|---|---|
| `get_eii` | the composite for one cell, with provenance, uncertainty and what it cannot support |
| `get_component` | one component on its own |
| `explain_score` | why this cell scored what it did, from stored contributions |
| `compare_baseline` | what the index adds over fire weather and a fuel map |
| `portfolio_scan` | the index across a book of exposures |

## The three fields that matter more than the value

**Provenance**, resolved from the archive's dimension tables rather than stored per row. A
caller receives the full chain — datasets, versions, access routes, native resolutions,
citations, retrieval times — while the archive stays the size of the measurements rather than
the size of the paperwork.

**Uncertainty**, as the component's own standard error, flat rather than nested. This is not a
style choice: the provenance guard treats any object carrying a `value` key as a served claim
and requires a chain beside it, so `uncertainty: { value }` reads as a second claim with no
provenance. Carving an exception into the guard to allow the nesting would weaken the one
mechanism this product is actually about. The guard caught this during the first test run.

**`method_justification`**, which says what the number cannot support. A cell is 0.74 km2 and
the reanalysis behind three of the five components is 9 to 25 km, interpolated. An agent that
reads the value without reading this will put a parcel-level claim on a landscape-level
measurement, and nothing else in the response would stop it.

## The orientation, repeated everywhere

"Ecosystem Integrity Index" reads as a scale where higher is healthier. It is the opposite: a
departure scale where higher is the direction associated with more severe fire. That sentence
is attached to every tool response and every relevant resource, not left in documentation an
agent may have loaded an hour ago and dropped since. An agent that assumes the intuitive
reading gets every conclusion backwards.

## Decisions

**`explain_score` reads stored contributions, never a live model.** An explanation regenerated
per call can differ from the one in last week's underwriting file. It also names the
components it could not measure rather than giving them a share of zero, which would read as
a component that contributed nothing rather than one that was never measured.

**`portfolio_scan` names the cells it could not score.** A scan that quietly means over what
it could measure gets better-looking as coverage gets worse, and the caller has no way to see
it happening.

**The audit log is append-only and cannot fail a call.** There is no update and no delete on
that path, because a log that can be edited answers no question worth asking. The response is
stored as a SHA-256 digest rather than a copy — the payload can be large and can carry the
caller's own geometry, and the digest is enough to prove a served response matches a recorded
one. A caller can recompute it: the digest is over the response body with the audit block
removed. If the log cannot be written the failure goes to stderr and the answer still goes
out, because an agent that cannot get an answer is worse off than one that gets an answer
nobody recorded.

**Two spines, one server.** v0.1's tools measure a 20 m projected grid over the coastal pilot;
these measure H3 hexes over the interior. They are listed together so an agent can see both
and choose, and dispatched apart so neither can answer for the other.

## Verification

`pnpm --filter @gaia/service test` — 26 EII tests against a fixture archive built by DuckDB in
the same layout the pipeline writes, so the SQL under test is the production SQL. The
provenance guard runs over a real response as one of the assertions.
