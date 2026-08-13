# Deployment

**Live:** <https://gaia-layer.vercel.app>

**Intended:** <https://layer.gaiaai.xyz>, which needs one DNS record that does not exist yet —
see "The custom domain" below.

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

The **DuckDB lake** travels inside the function (about 90 MB with all eleven cell layers). It is read-only at serve time, and at
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
- **Cold starts open a ~90 MB database**, so the first request after idle is slow. Route
  `maxDuration` is 60 seconds for that reason.
- **DuckDB files do not shrink on delete.** A cell rebuild leaves the file roughly 50%
  larger than it needs to be; compact it before deploying:

  ```bash
  uv run --directory pipeline python - <<'EOF'
  import duckdb, os
  src, dst = "data/gaia.duckdb", "data/gaia.compact.duckdb"
  c = duckdb.connect(":memory:")
  c.execute(f"ATTACH '{src}' AS old (READ_ONLY)"); c.execute(f"ATTACH '{dst}' AS fresh")
  c.execute("COPY FROM DATABASE old TO fresh"); c.close()
  os.replace(dst, src)
  EOF
  ```
- **The Groq free tier is rate limited** at 8,000 tokens per minute across the whole
  deployment, so concurrent playground users will see each other's limit.

## The custom domain

`layer.gaiaai.xyz` is not serving this yet, and the missing piece is in Cloudflare rather
than in Vercel.

`gaiaai.xyz` uses Cloudflare nameservers (`amit`/`rita.ns.cloudflare.com`), not Vercel's, and
it has a wildcard `*.gaiaai.xyz` record proxied through Cloudflare. So `layer.gaiaai.xyz`
already resolves — to Cloudflare, which answers **525** because the origin behind the
wildcard cannot complete a TLS handshake. Nothing is served there; the name is simply caught
by the wildcard.

That wildcard is also why `vercel alias set` fails: Vercel cannot issue a certificate for a
name whose traffic Cloudflare is intercepting and whose DNS does not point at Vercel.

The fix is one record, and the pattern is the one `times.gaiaai.xyz` already uses:

| Type | Name | Content | Proxy |
|---|---|---|---|
| CNAME | `layer` | `cname.vercel-dns.com` | **DNS only** (grey cloud) |

Proxy status matters. `times` is DNS-only and works; `eor` and `futures` resolve straight to
Vercel's `76.76.21.21`. A proxied record puts Cloudflare in front of Vercel's certificate and
reproduces the 525.

Once the record exists:

```bash
vercel alias set <latest-production-deployment>.vercel.app layer.gaiaai.xyz
```

Vercel issues the certificate on its own once the name resolves to it, usually within a
minute.

## The 100 MB file limit, and what it forced

Vercel refuses to upload any single file over 100 MB. `data/gaia.duckdb` had grown to 107,
which is a deployment failure that arrives with no warning and no relationship to anything
recently changed — the lake simply crossed a line.

Almost all of it was one table. `indicator_cell` is 950,832 rows whose `cell_id` and
`value_id` columns average 45 and 28 characters and repeat a million times; inside DuckDB
that is about 100 MB, and as Parquet with dictionary encoding it is 17.8. So the grid lives
in `data/cells.parquet` and the lake beside it is 5.8 MB.

`connect()` decides where the grid comes from and every query just uses the name `CELLS`. A
read-only attachment cannot hold a new view, so the view is created in the connection's own
in-memory database; when the Parquet is absent it is defined over `lake.indicator_cell`
instead, so a lake built before the split still serves.

Re-splitting after an ingest:

```bash
uv run --directory pipeline python - <<'EOF'
import duckdb, os
c = duckdb.connect(":memory:")
c.execute("ATTACH '../data/gaia.duckdb' AS old (READ_ONLY)")
c.execute("COPY (SELECT * FROM old.indicator_cell) TO '../data/cells.parquet' "
          "(FORMAT PARQUET, COMPRESSION ZSTD)")
c.execute("ATTACH '../data/gaia.split.duckdb' AS fresh")
c.execute("COPY FROM DATABASE old TO fresh")
c.execute("DROP TABLE fresh.indicator_cell")
EOF
```

DuckDB does not reclaim the dropped table's pages, so the result has to be copied once more
into a fresh file before it is actually small. Check row counts both directions before
replacing anything: `EXCEPT` in both directions is the check that catches a lost column,
which a row count does not.
