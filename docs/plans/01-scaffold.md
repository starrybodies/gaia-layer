# Milestone 1 — Scaffold

**Status:** in progress
**Goal:** A monorepo skeleton where every layer of the whitepaper stack has a home, one schema source drives types on both sides of the language boundary, CI enforces lint/typecheck/test, and `make dev` boots all services empty.

---

## Blocking finding: the whitepaper is not in the repo

The build prompt says the whitepaper lives at `/docs/whitepaper.md` and is the source of truth for product intent. It does not exist on this machine — no `gaia-layer` repo existed at all, and a filesystem search for `*whitepaper*` under `~` turned up only an unrelated React component in a Symbiocene Labs wireframe.

Since the instruction is to pause for nothing, I have written `docs/whitepaper.md` as a **placeholder** reconstructed from the three lessons the prompt states verbatim:

1. Build the verified substrate before the instrument.
2. Never let a language model be the system of record for a quantitative claim.
3. Price the land, not just the sky.

Every architectural decision below traces to one of those three. The placeholder is clearly marked as such at the top and logged in `docs/DIVERGENCES.md`. **Drop the real whitepaper in over it and re-read this plan** — if the real document contradicts anything here, the whitepaper wins and I will amend.

---

## Repository location

`/Users/samu/gaia-layer` — standalone, not inside `~/symbai`. The prompt specifies its own repo root with `/docs/whitepaper.md`, its own pnpm workspace, and its own Python package. Symbai already has `gaia-app`, `gaia-frontend`, `gaia-homepage`, `gaian-news`; folding a fifth Gaia surface into that monorepo would inherit its Nx graph and 4-layer package rules for no benefit, and this product's dependency set (rasterio, GDAL, DuckDB spatial) has nothing in common with it.

## Layout

```
gaia-layer/
├── docs/               whitepaper, RUNBOOK, DIVERGENCES, plans/, methods/
├── pipeline/           Python — ingestion (L1) + validation (L2). uv-managed.
│   └── src/gaia_pipeline/
│       ├── config.py       AOI + settings, GeoJSON-configurable
│       ├── schemas/        Pydantic models — THE schema source
│       ├── sources/        STAC, climate, terrain adapters
│       ├── indices/        NDVI/NDMI/NBR/TWI math
│       ├── validation/     constraint engine + confidence
│       └── store/          DuckDB + COG data lake
├── core/               TS — Zod schemas + types GENERATED from pipeline JSON Schema
├── mcp-server/         TS — MCP tools (L3)
├── api/                TS — REST mirror (L3), Hono
├── console/            Next.js (L4)
└── data/               gitignored data lake
```

`mcp-server` and `api` both import a single `service/` layer that owns all query logic. The MCP tool handler and the REST route handler are each a thin adapter over the same function. This is the "no logic duplication" rule made structural rather than aspirational.

## Schema flow — one source, both languages

```
pipeline/src/gaia_pipeline/schemas/*.py   (Pydantic v2 — the source)
        └─ make schema ─> docs/schema/*.json   (JSON Schema draft 2020-12)
                └─ json-schema-to-zod ─> core/src/generated/*.ts   (Zod)
                        └─ z.infer ─> TS types
```

Generated files are committed (so a fresh clone typechecks without Python) and CI regenerates them and fails on drift. Hand-editing a generated file is caught by the same check.

This exists because of lesson 2. If the envelope shape is defined once and both runtimes are forced to it, there is no code path where a number reaches a consumer without `confidence`, `validation_status`, and `provenance`. That property is enforced by the type system rather than by reviewer discipline.

## The envelope

Every value crossing the service boundary is:

```jsonc
{
  "value": 0.412,
  "unit": "index",
  "confidence": 0.87,
  "validation_status": "validated",       // validated | flagged | rejected
  "flags": [],
  "provenance": [ /* ProvenanceStep[] — non-empty, always */ ],
  "method": { "name": "NDMI (Gao 1996)", "citation": "..." },
  "generated_at": "2026-08-07T09:14:00Z",
  "claim_id": "clm_01J..."
}
```

`claim_id` is what `get_provenance` takes. Claims are persisted to a DuckDB table on emission, so a number an agent quoted last week can still be traced.

Modelled in Pydantic as a generic `Envelope[T]` with `provenance: list[ProvenanceStep] = Field(min_length=1)`. Rejected values are not representable as a served answer — a separate `RejectedValue` type carries the reason and has no `value` field at all.

## Tooling decisions

| Choice | Decision | Alternative considered |
|---|---|---|
| Python version | **3.12**, pinned via uv | System is 3.14; rasterio/pyproj wheels lag on 3.14 and would force source builds against Homebrew GDAL. Not worth the fragility. |
| Python deps | uv + `pyproject.toml`, lockfile committed | Poetry — slower, and uv already installs the interpreter. |
| Raster IO | rasterio windowed reads over HTTP against COGs | Downloading whole S2 tiles (~1 GB/scene/band). Windowed reads pull only the AOI. Mac-mini-runnable is a hard constraint. |
| Query engine | DuckDB + `spatial` ext | PostGIS — a managed service dependency v0.1 explicitly forbids. |
| Tabular format | Parquet, Hive-partitioned by indicator/year/month | — |
| TS runtime | Node 22, ESM, `tsx` for dev | — |
| REST framework | **Hono** | Fastify. Hono's `@hono/zod-validator` reuses the generated Zod schemas directly for request validation, so the one-schema-source rule extends to the HTTP edge for free. |
| Test | pytest + Hypothesis (py), vitest (ts) | — |
| Lint | ruff (py), eslint + prettier (ts) | — |

## CI

GitHub Actions, one workflow, four jobs: `python` (ruff, mypy, pytest), `typescript` (eslint, tsc, vitest), `schema-drift` (regenerate and `git diff --exit-code`), `provenance-guard` (the grep test from the prompt — asserts no served response shape can carry a value without a provenance chain; runs as a real test against the service layer, not a text grep, so it cannot be fooled by formatting).

## `make dev`

Boots, concurrently: the API on :8787… **conflict.** `127.0.0.1:8787` is occupied by the user's persistent headroom proxy. Ports chosen to avoid it:

- API — **:8811**
- MCP server — stdio (no port)
- Console — **:3311**

## Assumptions I am proceeding under

1. Whitepaper reconstructed from prompt as above; real one overrides.
2. AOI default bbox for the Southern Gulf Islands + adjacent coastal Douglas-fir: `[-123.90, 48.40, -123.10, 49.00]` (EPSG:4326). Configurable by GeoJSON per the prompt.
3. Analysis CRS: EPSG:32610 (UTM 10N), 20 m grid. S2 20 m bands (B8A, B11, B12) and 10 m bands (B04, B08) resample to a common 20 m grid; 20 m is the native resolution of the SWIR bands NDMI and NBR depend on, so upsampling them to 10 m would be inventing detail — a lesson-2 violation in miniature.
4. Historical window: 12 months ending at the most recent complete month.
5. Single API key from env, no user model. Per scope.
6. No token, no parametric trigger, no eDNA. Per scope.

## Checkpoint

`make dev` starts API, MCP server, and console with no data; `make check` passes lint + typecheck + tests on all four packages; `make schema` is idempotent.
