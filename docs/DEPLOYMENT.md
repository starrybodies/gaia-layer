# Deployment

**Live:** <https://gaia-layer.vercel.app>

| Path | What |
|---|---|
| `/` | Map — indicator cell grid, click for the full envelope |
| `/report` | Substrate report — score, decomposition, trends, provenance |
| `/playground` | Agent querying the layer live, with the full tool transcript |
| `/api/v1/*` | The layer's REST interface, same one an agent uses |

## Shape

One Vercel deployment serves both the console and the REST API. The Hono app from
`@gaia/api` is mounted at `/api` by a Next route handler — the same configured app that
binds to a port when run standalone, not a reimplementation. The service layer underneath is
untouched, so the two interfaces cannot drift.

```
Vercel function
├── Next pages (map, report, playground)
├── /api/v1/*        → @gaia/api Hono app → @gaia/service
└── data/gaia.duckdb  (47 MB, read-only, bundled)
```

The MCP server is not deployed and does not need to be. It speaks stdio, so an agent runs it
locally against either the local lake or the live REST API.

## What ships, and what does not

The **47 MB DuckDB lake** travels inside the function. It is read-only at serve time, and at
that size shipping it is simpler and faster than fronting it with object storage.

The **900 MB of COG rasters do not ship.** Nothing served reads them — they are pipeline
inputs and outputs, and every answer comes from DuckDB. `.vercelignore` excludes them.

## Three things this forced, all improvements

**`get_provenance` reconstructs claims from the lake.** A serverless filesystem is read-only,
so the claim ledger cannot be written. Because claim ids are derived from claim content, a
served value can be re-identified by recomputing ids over the stored measurements. The ledger
became an index rather than the system of record — which is the correct relationship, since
the measurements are the system of record. Production answers `reconstructed_from_lake: true`
and traces to the same source scenes.

**DuckDB is externalised by prefix in webpack.** `serverExternalPackages` does not reach
through workspace symlinks, so the bundler followed `@gaia/service` into DuckDB and failed on
the eight platform bindings that are not installed. It is also declared as a direct
dependency of the console, because the console genuinely loads it now — and because as a
transitive dependency it linked under `service/node_modules`, which the function's module
resolution never reaches.

**Tool results are compacted for the model.** Groq's free tier caps at 8,000 tokens per
minute and a full ecological-state response is about 20,000. The model receives every number
with its confidence, status, claim id and method; the visitor's transcript keeps the raw
response. See `console/src/app/api/chat/compact.ts`.

## Environment

Set in the Vercel project:

| Variable | Value | Why |
|---|---|---|
| `GROQ_API_KEY` | — | The playground's model. Free tier. |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Follows the citation rules well and does tool calls. |
| `GAIA_CLAIMS_PATH` | `/tmp/gaia-claims.duckdb` | The only writable path. Ephemeral, and that is fine. |
| `NEXT_PUBLIC_API_BASE` | `/api` | Same origin. |

`GAIA_API_KEY` is deliberately unset, so the demo API is open. Set it to require
`x-gaia-key` on every route except `/health`.

## Redeploying

```bash
vercel deploy --prod
```

Builds `@gaia/core`, `@gaia/service` and `@gaia/api` first, then the console. After
re-running the pipeline, the new `data/gaia.duckdb` is picked up by the next deploy — there
is no separate data step.

## Limits worth knowing

- **Deployment protection is off**, so the URL is public. Turn it back on in project settings
  if that changes.
- **The lake is a snapshot.** Nothing on Vercel runs the pipeline; the deployed data is
  whatever was in `data/gaia.duckdb` at deploy time. Keeping it current means re-running the
  ingest locally and redeploying.
- **Cold starts open a 47 MB database**, so the first request after idle is slow. Route
  `maxDuration` is 60 seconds for that reason.
- **The Groq free tier is rate limited** at 8,000 tokens per minute across the whole
  deployment, so concurrent playground users will see each other's limit.
