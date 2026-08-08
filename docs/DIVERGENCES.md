# Divergences

Where engineering reality departs from the build prompt or the whitepaper. Engineering
reality wins in code; the departure is recorded here.

Format: what was specified, what was built, why, and what it would take to close the gap.

---

## D-001 — The whitepaper was not in the repository

**Specified:** `docs/whitepaper.md` is the source of truth for product intent; read it first.

**Reality:** No `gaia-layer` repository existed on this machine, and a filesystem search
under `~` for `*whitepaper*` returned only an unrelated React component. The document was
not available at any point during the v0.1 build.

**Built:** `docs/whitepaper.md` is a placeholder, headed by a warning block, reconstructed
solely from the three lessons the build prompt quotes verbatim. It asserts nothing beyond
what the prompt asserts — no data, no figures, no external claims.

**To close:** Drop the real whitepaper over the placeholder and re-read `docs/plans/`.
Anything in the plans that the real document contradicts gets amended, and the amendment
gets an entry here.

**Raised:** 2026-08-07 · **Status:** open

---

## D-002 — ERA5 reanalysis is read through Open-Meteo, not the Copernicus CDS

**Specified:** "ERA5 or ERA5-Land reanalysis for precipitation, temperature, vapor pressure
deficit" and "SMAP or ERA5-Land soil moisture."

**Reality:** The Copernicus Climate Data Store API requires a registered account and a
credential in `~/.cdsapirc`, and CDS retrievals are queued rather than synchronous —
minutes to hours per request. NASA Earthdata, which serves SMAP, likewise requires
credentials. Neither can run on a cold-start machine inside the 30-minute RUNBOOK budget,
and neither can run unattended without the operator first creating accounts.

**Built:** ERA5 and ERA5-Land variables are read from the Open-Meteo Historical Weather
Archive API (`archive-api.open-meteo.com`), which serves ERA5 and ERA5-Land as an
anonymous, synchronous, rate-limited HTTP endpoint. It supplies 2 m temperature, 2 m
dewpoint (from which vapour pressure deficit is computed), precipitation, and ERA5-Land
volumetric soil moisture at 0–7 cm, 7–28 cm, 28–100 cm, and 100–255 cm.

The underlying reanalysis is the same product. The provenance chain records both the
originating dataset (`ECMWF/ERA5-Land`) and the access route (`open-meteo-archive`), so a
consumer can tell that the value was served through an intermediary rather than pulled
from ECMWF directly.

**Cost of the choice:** Open-Meteo delivers point time series at the ERA5-Land grid cell
containing a coordinate, not native gridded fields. For an area of interest the size of the
pilot this is sampled at a coarse lattice of points and interpolated, which is adequate at
ERA5-Land's ~9 km resolution but would not be adequate for a much smaller parcel. Rate
limits also cap ingestion throughput.

**To close:** Add a CDS adapter behind the same `ClimateSource` interface once the operator
has supplied CDS credentials, and switch the default. The provenance route field already
distinguishes the two, so historical claims stay interpretable across the switch.

**Raised:** 2026-08-07 · **Status:** accepted for v0.1

---

## D-003 — Python is pinned to 3.12 rather than the system 3.14

**Specified:** Nothing explicit; "must run on a Mac mini."

**Reality:** The system interpreter is Python 3.14.0. Several geospatial dependencies
(rasterio, pyproj, and their transitive binary stack) lag on new interpreter releases, so
3.14 risks source builds against Homebrew GDAL.

**Built:** `pipeline/.python-version` pins 3.12, which uv installs automatically. This is
invisible to the operator — `make setup` provisions the interpreter.

**Raised:** 2026-08-07 · **Status:** accepted

---

## D-004 — Service ports avoid 8787

**Specified:** Nothing explicit.

**Reality:** `127.0.0.1:8787` is occupied by a persistent proxy on this machine.

**Built:** REST API on `:8811`, console on `:3311`, MCP server over stdio. Both ports are
overridable by environment variable.

**Raised:** 2026-08-07 · **Status:** accepted
