# Gaia — Ecological Intelligence Layer

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

Full detail in [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

## v0.1 scope

One bioregion, one peril, one vertical slice. Southern British Columbia; wildfire substrate.
Out of scope by design: eDNA, biodiversity indices, flood, parametric triggers, tokens,
multi-tenancy, auth beyond a single API key.
