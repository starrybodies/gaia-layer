# Runbook

Cold start on a fresh macOS machine. Target: a working demo in under 30 minutes, most of
which is the pipeline downloading satellite data while you do something else.

---

## 1. Prerequisites

```bash
# Homebrew, if you do not already have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

brew install uv pnpm gdal
```

You need Node 22 or newer. Check with `node --version`; `brew install node` if missing.

`uv` installs the pinned Python 3.12 itself — do not install Python by hand.

No API keys, accounts or credentials are required for the pilot area. Every data source
v0.1 uses is open and anonymous. See `docs/DIVERGENCES.md` D-002 for why the climate data
comes through Open-Meteo rather than the Copernicus Climate Data Store.

## 2. Install

```bash
git clone <repo> gaia-layer && cd gaia-layer
make setup
```

Roughly two minutes. It provisions Python 3.12, syncs the pipeline dependencies including
rasterio and DuckDB, and installs the Node workspace.

## 3. Build the data lake

```bash
make seed
```

This ingests the pilot area of interest — the Southern Gulf Islands and the adjacent
Coastal Douglas-fir zone — for the last twelve complete months: Sentinel-2 spectral
indices, ERA5-Land climate and soil moisture, Copernicus DEM terrain, and a substrate
score per month. It reads only the windows of each satellite scene that intersect the
area, so it moves tens of megabytes rather than tens of gigabytes.

**Expect 45 to 60 minutes.** Measured on an M-series Mac mini: roughly three minutes per
month for Sentinel-2, which is the bulk of it, plus two or three minutes for everything
else. Most of that is not network — the windowed reads take about 1.5 seconds each — it is
compositing and compressing ten-megapixel float grids.

Two ways to cut it down:

```bash
# Half the scenes per tile per month. Faster, and a median of two is less robust to
# residual cloud than a median of three.
uv run --directory pipeline gaia ingest sentinel2 --max-scenes 2

# Fewer months.
GAIA_HISTORY_MONTHS=4 make seed
```

The seed is resumable: re-running skips periods already present, so an interrupted run can
be restarted with the same command.

**While a seed is running, the API cannot read the lake.** DuckDB permits one writer, and
the pipeline holds that lock for the length of the ingest. The API reports this as a
retryable `lake_unavailable` rather than pretending otherwise. Wait for the seed to finish
before running `make dev`.

Check what landed:

```bash
make coverage
```

## 4. Run the services

```bash
make dev
```

- REST API — <http://127.0.0.1:8811>, health at `/health`
- Console — <http://127.0.0.1:3311>

The MCP server speaks stdio and is started by its client rather than by `make dev`. To run
it by hand for debugging:

```bash
make mcp
```

## 5. Connect an agent

Register the MCP server with the Claude CLI:

```bash
claude mcp add gaia -- pnpm --dir "$(pwd)/mcp-server" start
```

Then ask it something an underwriter would ask:

> What is the vegetation dryness trend for the pilot area over the last six months, and how
> confident are you in it?

The answer should come back with numbers, confidence scores, and claim ids. Feed a claim id
back with `get_provenance` to trace it to the satellite scenes behind it.

## 6. Verify the build

```bash
make check
```

Runs lint, type-checks and tests across the Python pipeline and all TypeScript packages —
the same four jobs CI runs.

---

## Configuration

Everything is environment variables; none are required.

| Variable | Default | Purpose |
|---|---|---|
| `GAIA_DATA_DIR` | `./data` | Where the data lake lives. |
| `GAIA_DUCKDB_PATH` | `$GAIA_DATA_DIR/gaia.duckdb` | The database file. |
| `GAIA_API_KEY` | unset | When set, the REST API requires it in the `x-gaia-key` header. Unset means the API is open, and it says so on startup. |
| `API_PORT` | `8811` | REST API port. |
| `CONSOLE_PORT` | `3311` | Console port. |
| `GAIA_HISTORY_MONTHS` | `12` | Months of history `make seed` ingests. |
| `GAIA_STRICT_GUARD` | on | Set to `0` to disable the runtime provenance guard. Do not do this in a deployment. |
| `ANTHROPIC_API_KEY` | unset | Required only by the console's agent playground. |

## Working with a different area of interest

The pilot area is a default, not a constant.

```bash
uv run --directory pipeline gaia aoi add \
  --geojson /path/to/parcel.geojson \
  --id client-parcel \
  --name "Client parcel"

uv run --directory pipeline gaia ingest all --aoi client-parcel
```

The GeoJSON may be a bare geometry, a Feature, or a FeatureCollection.

## Troubleshooting

**`make dev` fails with "lake_unavailable".** The data lake has not been built. Run
`make seed`.

**A tool returns `aoi_not_ingested`.** The requested geometry does not match an ingested
area. This is deliberate: the layer will not serve a neighbouring area's average as if it
described your parcel. Register the geometry and ingest it, per the section above.

**Ingestion is slow or times out.** The STAC endpoint rate-limits. The pipeline retries with
backoff; if it gives up, re-run `make seed` and it resumes.

**Port 8811 or 3311 is in use.** Override with `API_PORT` or `CONSOLE_PORT`. Port 8787 is
avoided deliberately — see `docs/DIVERGENCES.md` D-004.

**Rebuilding from scratch.** `make clean-data` deletes the lake. It is destructive and
recoverable only by re-running `make seed`.
