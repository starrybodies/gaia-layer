# Gaia — Ecological Intelligence Layer

**Live: <https://gaia-layer.vercel.app>** — the map, the substrate report, an agent querying
the layer with its full tool transcript shown, and the two v0.2 surfaces below.

Agent-native ecological ground truth. Validated, provenance-tracked ecological state for a
defined area, served to AI agents over MCP and to everything else over REST.

The layer is the product. The console is a window onto it.

**Start here:** [`docs/whitepaper.md`](docs/whitepaper.md) for intent,
[`docs/RUNBOOK.md`](docs/RUNBOOK.md) to run it, [`docs/plans/`](docs/plans) for what was
built and why.

## The rule that shapes everything

No number leaves this system without a provenance chain, a validation status, a quantified
confidence, and the citation for the method that produced it. A language model orchestrates
and explains; it never computes an ecological value and is never the system of record for
one.

That is enforced structurally, not by convention: the envelope type has no representation
for a bare value, rejected values have no `value` field at all, and CI walks every served
response to prove it.

## Layout

| Path | What it is |
|---|---|
| `pipeline/` | Python. Ingestion (layer 1) and the validation constraint engine (layer 2). The Pydantic schemas here are the single source of truth for every data shape. |
| `schema/lake.sql` | The DuckDB data lake DDL, shared by the Python writer and the TypeScript reader. |
| `core/` | TypeScript. Zod schemas and types generated from the Pydantic source, plus the provenance guard. |
| `service/` | TypeScript. All query logic, in one place. |
| `mcp-server/` | The MCP interface (layer 3). A thin adapter over `service/`. |
| `api/` | The REST mirror (layer 3). The same adapter over the same `service/`. |
| `console/` | Next.js. Map, substrate report, agent playground (layer 4). |

## Quick start

```bash
make setup && make seed && make dev
```

Full detail in [`docs/RUNBOOK.md`](docs/RUNBOOK.md). Deployment in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## v0.2 — the Ecosystem Integrity Index

An index for wildfire underwriting over the Okanagan: 43,303 H3 resolution-8 cells, five
components, each a *departure* from the same cell's own reference rather than a level.
Structure, water balance, riparian condition, fuel moisture, drought. Higher is worse.

Only Component A has been through a validation gate. The other four are built and served and
say so. The weights are equal because inventing a weighting to look sophisticated would be
inventing a finding.

### Two surfaces

**`/diligence`** — built for a model-validation team trying to break it. Every figure on it
is computed in the pipeline and persisted with the run, method record and source set that
produced it; the page renders and never calculates. It leads with the findings that weaken
the claim, because an analyst should not discover anything there that the page did not tell
them first:

- under leave-one-fire-out the gate's own baseline scores **0.1039** against a prevalence of
  **0.1064** — at or below the no-skill line, so the candidate's margin is not "beats a
  working fire-weather model";
- refitting without fire weather, and without fuel type, each score marginally *higher*;
- the Component A composite column has a permutation importance of **-0.0030** while its
  three inputs carry the lift;
- 7 of 42 fires are scorable, three study years carry no high-severity cell at all, and the
  misses concentrate where fire weather is worst (ISI 9.35 against 5.56).

**`/portfolio`** — a book of cells ranked at res-8, rolled up to res-7, and compared across
two as-of dates, on a map. A client sends H3 cell identifiers and their own exposure values;
there is no field for an address or a coordinate and no code path that would use one. Cells
the archive cannot score are named and drawn, never dropped — a portfolio statistic that
improves as coverage falls is the failure this surface exists to make visible.

The demo book is built from Overture's open building footprints. Its values are invented and
it says so in the label, the warning and the field name.

## Sources, and the discipline about them

Every source is open and read anonymously — no account, no token, no key. That constraint is
load-bearing and it is also where most of the work went: a source that catalogues fine and
returns nothing is the recurring failure of this build, and
[`docs/DIVERGENCES.md`](docs/DIVERGENCES.md) is twenty-one entries of it. ERA5-Land answering
`null` for variables it does not carry. A DEM declaring no nodata and fabricating a sea-level
plain. `h3-js` answering an invalid cell id with a hexagon in Arctic Russia. An archive
holding NaN where it meant NULL.

Read that file before trusting anything here. It is the most useful document in the
repository.

## v0.1 scope

One bioregion, one peril, one vertical slice. Southern British Columbia; wildfire substrate.
Out of scope by design: eDNA, biodiversity indices, flood, parametric triggers, tokens,
multi-tenancy, auth beyond a single API key.
