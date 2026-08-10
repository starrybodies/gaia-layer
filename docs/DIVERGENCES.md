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

---

## D-005 — The claim ledger lives in a second database file

**Specified:** A local DuckDB data lake, with claims recorded so `get_provenance` can
answer for a number served earlier.

**Reality:** DuckDB permits a single writer per file. The pipeline holds the lake's write
lock for the length of an ingest — 45 minutes or more for a twelve-month seed. With the
`claim` table inside the lake, the API could not record what it served while data was
arriving, and could not read either. This was not theoretical: it failed the first time the
service was pointed at a lake with an ingest in progress.

**Built:** The `claim` table moved to `schema/claims.sql` and its own file, `claims.duckdb`.
The service opens that read-write and attaches the measurement lake read-only, addressing
its tables as `lake.indicator_value` and so on. Ownership is now clean — the pipeline owns
measurements, the service owns claims — and because the attach is read-only, several API
processes can serve concurrently.

**Residual limitation:** While an ingest is running, the lake's write lock still blocks the
read-only attach, so the API returns a retryable `lake_unavailable` for the duration. This
is reported honestly rather than papered over, and the runbook says to let a seed finish
before starting the services. Closing it properly means having the pipeline take the write
lock per batch rather than per run, which is a worthwhile change and not a v0.1 one.

**Raised:** 2026-08-07 · **Status:** accepted for v0.1, residual limitation documented

---

## D-006 — The topographic wetness index is computed at 100 m, not 20 m

**Specified:** Terrain derivatives on the analysis grid.

**Reality:** TWI needs flow routed downhill across the whole area, which is a sequential
walk over every cell — ten million steps at 20 m over the pilot area, in Python.

**Built:** Elevation is block-averaged to 100 m, flow is accumulated by D8 routing there,
and the index is resampled back to the analysis grid. Elevation, slope and aspect stay at
20 m; only TWI is coarsened, and the coarsening factor is recorded in its provenance chain.

TWI describes hillslope position rather than fine texture, so the coarser grid costs little
— and computing it honestly at 100 m is better than approximating it badly at 20 m.

**To close:** A vectorised or compiled flow-accumulation routine, or `richdem`, would allow
the native grid. Neither is worth a dependency in v0.1.

**Raised:** 2026-08-07 · **Status:** accepted

---

## D-007 — Land cover is a 2021 epoch under 2025 spectral data

**Specified:** Every indicator describes the period it is served for.

**Reality:** ESA WorldCover is published as single-year global epochs. v200 is 2021, and
there is no monthly or annual product at 10 m that is open and anonymous. The spectral
indices this layer sits beside are current to the ingested window.

**Built:** Land cover is ingested once, as a static indicator, with the acquisition date in
its provenance chain set to the 2021 epoch rather than to the analysis window. It is used
to say what kind of ground a cell is, never to say what changed.

The consequence is stated rather than hidden: ground cleared, burned or built since 2021
still reads as whatever it was in 2021. On a twelve-month wildfire window that mostly
matters where harvest has been heavy, which is also where the dNBR layer will show the
disturbance the cover class missed.

**To close:** A second epoch — WorldCover v100 for 2020, or a Dynamic World composite —
would turn one label into a change detection. That is a different indicator with a
different validation story, not a version bump on this one.

**Raised:** 2026-08-09 · **Status:** accepted for v0.1, limitation stated in the layer note

---

## D-008 — Canopy height comes from GLAD, not ETH

**Specified:** ETH's 10 m global canopy height (GEDI + Sentinel-2 fused) for Component A.

**Reality:** The documented host, `share.phys.ethz.ch/~pf/nlangdata/`, redirects to a DOI
that returns 403 to any non-browser client. A Nextcloud file-share link still serves the
tiles, but it is a personal share that has already moved once.

**Built:** GLAD/Potapov 2019 forest canopy height, 30 m, North America mosaic, over anonymous
HTTPS with working range requests. It is native 30 m, which is the analysis grid exactly, so
the read is like-for-like rather than a downsample. The mosaic is strip-organised rather than
tiled, so a window read pulls full continental scanlines — affordable because it happens once
for a single epoch.

**To close:** If ETH republishes at a stable URL, 10 m would resolve structure inside a hex
rather than across it. The flag-handling would need rewriting; ETH uses a different nodata
convention.

**Raised:** 2026-08-10 · **Status:** accepted

---

## D-009 — Burn severity comes from Sentinel-2, because Landsat's bucket is Requester Pays

**Specified:** Landsat Collection 2 Level 2 through Earth Search, anonymous.

**Reality:** Earth Search catalogues `landsat-c2-l2` anonymously and its assets are not
anonymous. Every href points at `usgs-landsat.s3`, which is Requester Pays: 403 without AWS
credentials, billed with them. Recon checked that the collection existed and did not check
that an asset could be read, which is the difference that mattered. The first full labelling
run produced zero labels for 2023 before this was found.

**Built:** Sentinel-2 L2A through Earth Search, which is genuinely anonymous and is the route
v0.1 already uses. NBR from B8A and B12, both native 20 m — finer than the 30 m planned —
with the scene classification band for cloud, shadow, snow and water masking.

The reflectance offset introduced with processing baseline 04.00 is handled explicitly: NBR
is a normalised difference, so a common additive offset does not cancel, and reading a 2023
scene with the pre-2022 convention would shift every severity value rather than failing.

**Residual limitation:** Sentinel-2B launched in March 2017. A pre-fire season for a 2017
fire rests on a single satellite at a ten-day repeat, and 2015 and 2016 fire years are not
usable at all. The archive reports which years it has rather than interpolating across the
ones it does not.

**To close:** Microsoft's Planetary Computer serves Landsat Collection 2 with an anonymous
SAS token and would restore the full decade. It adds a token-refresh dependency on a third
party, which was judged the worse trade for a first pass.

**Raised:** 2026-08-10 · **Status:** accepted, coverage limitation documented

---

## D-010 — Fire weather codes are computed, and differ from CWFIS in a known way

**Specified:** CFFDRS codes from CWFIS or the `cffdrs` package.

**Reality:** CWFIS publishes FWI grids for the current day only; there is no downloadable
gridded archive for 2015-2024. Nothing on PyPI computes the codes — `PyFWI` is seismic
full-waveform inversion, `fwi` is an empty 0.0.0 placeholder — the reference implementation
is R, and NRCan's own Python code implements the next-generation hourly system, which needs
hourly weather this build does not have.

**Built:** The Van Wagner and Pickett (1985) equations, in Python, validated against CWFIS's
own station archive, which publishes observed weather alongside the codes CWFIS computed from
it. Over ten station-seasons at five Okanagan stations in 2021 and 2023: Drought Code within
0.7 units across a whole season, FFMC within 4.5, ISI within 0.8, FWI within 3.

**Known difference:** Duff Moisture Code runs up to 23% above the CWFIS series, carried into
BUI. Fitting the day-length factor back out of CWFIS's own increments reproduces the
published table from July onward and falls short only in April and May, which is when CWFIS
suspends code advance for snow on the ground. Their series encodes an operational policy;
this one encodes the published specification. The tests pin both the magnitude and the rank
correlation so the difference cannot grow quietly.

**Raised:** 2026-08-10 · **Status:** accepted, difference quantified

---

## D-011 — BCGW's WFS is paged spatially, because `startIndex` does not work

**Specified:** Standard WFS paging for the provincial inventory layers.

**Reality:** Four of the five layers used here — VRI and all three Freshwater Atlas layers —
return 504 after sixty seconds for any request carrying `startIndex`, with or without
`sortBy`. The identical request without it returns in 0.3 s. VRI additionally refuses natural
ordering: *"Cannot do natural order without a primary key."* Only BEC has a primary key and
pages normally.

**Built:** Spatial paging. Request a bounding box; if `numberMatched` exceeds
`numberReturned`, quarter the box and recurse, with a depth floor below which the tile is
fetched whole. Feature ids are minted per request for the keyless layers, so the same polygon
fetched from two overlapping boxes has no id in common — deduplication is by content hash.

**Raised:** 2026-08-10 · **Status:** accepted; this is how the service works
