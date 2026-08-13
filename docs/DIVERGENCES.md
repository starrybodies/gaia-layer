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

---

## D-012 — A windowed read used to fabricate zeroes, and the first DEM tile hid the other eight

**Specified:** Copernicus DEM GLO-30 mosaicked across the study area, elevation and its
derivatives on the 30 m analysis grid.

**Reality:** `read_window` filled the part of the grid a source did not cover with the
source's nodata value, or with zero when the source declared none. Copernicus DEM declares
none. Nine one-degree tiles cover the study area, so each read returned real elevation
inside its own tile and 0.0 m everywhere else, and the mosaic step — `where(isfinite(...))`
— treated a finite zero as data already in hand and discarded tiles two through nine.

38,829 of 43,303 spine cells carried an elevation of exactly 0.0 m. Slope, aspect and heat
load are derived from elevation and inherited it. The v0.1 area is small enough to sit
inside one tile, which is why this survived a full ingest without showing.

**Built:** Ground a source does not cover comes back NaN. The reprojecting path clips its
window to the source and warps into a NaN destination, so GDAL writes only where the source
reaches. The same-CRS path keeps its decimated read — that read is what makes a
twelve-month ingest feasible — and masks by geometry, counting a grid pixel as covered only
when the whole block of source pixels it averages lies inside the source. The coverage is
worked out arithmetically rather than taken from rasterio's boundless mask, because that
mask is built by handing the fill value to a VRT as its nodata and would also mask any real
pixel equal to the fill. A canopy height of zero is bare ground.

**Effect on the Component A gate:** elevation now spans 277-2,508 m over 42,819 distinct
values. Every reported number moved. The gate comparison went from +0.0542 to +0.1410 and
the attribution comparison from +0.0535 to +0.1580, both still excluding zero; the verdict
was PASS before and is PASS after. The degraded terrain was making the attribution test
easier than it should have been, and correcting it made the result stronger rather than
weaker.

**Raised:** 2026-08-11 · **Status:** fixed, regression tests in `tests/test_raster.py`

---

## D-013 — The calibration column was measuring the emptiest bin

**Specified:** Report calibration alongside discrimination.

**Reality:** The reported figure was the maximum gap over ten equal-width probability bins,
unweighted by how many cells each bin held. The candidate's headline gap of 0.532 came from
a bin holding 23 of 3,835 cells and `baseline_4`'s 0.588 from a bin holding 8. The baselines
it was being compared against were scored on bins five to thirteen times larger. A model
that never predicts above 0.3 gets a flattering number for refusing to make a confident
prediction at all, and `baseline_2` — the worst model in the table on every other measure —
had the best-looking calibration gap.

**Built:** Three columns rather than one. The worst-bin gap stays, now with the cell count
beside it; ECE weights every bin by its population and is the column models are compared
on. On ECE the candidate is 0.063 against the gate baseline's 0.057, and it holds the best
Brier score of the five models.

The residual finding is real and is reported: the candidate's reliability curve is too
steep at both ends. Bands from 0.60 up promise more than they deliver — 81 cells, 2.1% of
those scored, and the disagreement is larger than a Wilson interval on those bins explains
— while the four bands below 0.40 deliver more than they promise. The ranking is sound;
the levels above 0.6 should not be read as probabilities. The correction is a monotone
recalibration fitted inside each training fold, and it is deliberately not applied inside
the validation run: recalibrating changes the pooled out-of-fold probabilities and so the
gate comparison, and the gate was written before the first model was fitted.

**Raised:** 2026-08-11 · **Status:** measurement corrected; residual documented in the
validation report, recalibration deferred to the served score

---

## D-014 — ERA5-Land carries no precipitation or reference evapotranspiration either

**Specified:** Component B is a rolling water-balance anomaly plus antecedent soil moisture,
from "ERA5-Land via Open-Meteo", with MODIS ET or ERA5-Land P−ET as the evapotranspiration
term.

**Reality:** Open-Meteo's `era5_land` model returns `null` for `precipitation_sum` and
`et0_fao_evapotranspiration` over the study area for every date tested, and `null` for
hourly `precipitation` as well. It returns real values for `soil_moisture_7_to_28cm` and
`soil_moisture_28_to_100cm`. This is the same shape of gap as the 10 m wind that broke FFMC,
ISI and FWI (fixed in `b5e84c9`): ERA5-Land is served as a partial variable set, and asking
it for a variable it does not carry yields nulls rather than an error.

Measured at 49.88 N, 119.50 W for 2023-05-01 to 2023-05-05:

| model | precipitation_sum | et0_fao_evapotranspiration |
|---|---|---|
| `era5_land` | null | null |
| `era5` | 0.0, 0.0, 0.0, 0.3, 4.4 | 3.60, 4.00, 4.11, 4.41, 2.86 |
| `era5_seamless` | 0.0, 0.0, 0.0, 0.3, 4.4 | 3.90, 4.38, 4.30, 4.63, 2.96 |

**To build:** The water balance takes P and ET0 from `era5_seamless`, which is ERA5 with
ERA5-Land's elevation applied to the temperature-derived terms — visible above in the ET0
column, where seamless and plain ERA5 disagree by 8% on the same precipitation. Soil
moisture stays on `era5_land`. The two halves of Component B therefore sit at different
native resolutions, roughly 25 km against 9 km, and the source records have to say so
rather than presenting the component as one measurement.

**Raised:** 2026-08-11 · **Status:** open, to be built this way

---

## D-015 — SPEIbase stops at December 2022, and the demo is a 2023 fire

**Specified:** Component E is a normalised multi-scale SPEI blend, from a SPEIbase download.

**Reality:** SPEIbase v2.11 is anonymously reachable and its server supports byte ranges, so
the study area's cells can be read without pulling 376 MB per timescale — verified by
reading real values for the 49.75 N, 119.75 W cell. But its time axis runs 1901-01 to
**2022-12**, 1,464 months. The study years are 2015–2024, and the case study the whole pitch
rests on is McDougall Creek in **August 2023**.

So the published product covers eight of the ten study years and neither of the two that
matter most. Component E would be missing for 2023 and 2024, and missing is what it would
have to report, because carrying 2022's drought forward into a 2023 fire season would be
fabricating the one variable the demo is about.

**The fork, not yet taken:**

1. **Report Component E missing for 2023–24.** Honest, costs nothing to build, and leaves
   the Kelowna retrodiction without a drought term.
2. **Compute SPEI from the same P and ET0 series Component B already needs**, over the full
   Open-Meteo archive, and validate it against SPEIbase across the 2015–2022 overlap. This
   is the precedent already set by D-010, where the CFFDRS codes were computed from the
   published equations and pinned against CWFIS's own series with the difference quantified.
   It costs a log-logistic fit by L-moments and a long history fetch, and it is a
   methodological claim that has to be stated as one.

**Built:** (2), on the operator's instruction. Component E computes SPEI from the same
precipitation and reference-evapotranspiration series Component B already needs, by
Vicente-Serrano's published method: the climatic water balance aggregated over k months, a
three-parameter log-logistic fitted by probability weighted moments to the same calendar
month across the reference years, and the standard normal quantile of the fitted
probability. `sources/speibase.py` remains, and its only job is to read the published
product over byte ranges so the computed one can be checked against it.

**The agreement, measured rather than asserted.** Four corners of the study area, every
month from 2015-01 to 2022-12, against SPEIbase v2.11:

| timescale | n | correlation | mean difference | mean absolute difference | agrees on SPEI < -1 |
|---|---|---|---|---|---|
| 1 month | 384 | 0.824 | -0.002 | 0.503 | 88.3% |
| 3 month | 305 | 0.872 | -0.020 | 0.458 | 89.2% |
| 12 month | 328 | 0.754 | +0.112 | 0.463 | 91.5% |

Essentially unbiased, and it ranks the same months dry. It is not tight: half a SPEI unit of
mean absolute difference is half a standard deviation of the quantity itself, and this
version flags drought somewhat more often than SPEIbase does — 22% of months against 16% at
the one-month scale. Three reasons, none of them a defect: the reference distribution here is
forty years against SPEIbase's hundred and twenty, so the tails are less stable; the balance
uses FAO-56 reference evapotranspiration where SPEIbase uses Penman-Monteith potential
evapotranspiration; and the reanalysis underneath is ERA5 at 25 km rather than CRU at half a
degree. What survives well is the ordering and the drought classification, which is what the
component is read for. The bounds are pinned in `tests/eii/test_drought.py` so they cannot
loosen quietly.

**Raised:** 2026-08-11 · **Status:** built and quantified; a consumer wanting the published
product's own tails should use SPEIbase directly for years up to 2022

---

## D-016 — Open-Meteo meters by call weight, and a forty-year lattice exhausts a day of it

**Specified:** ERA5-Land through Open-Meteo for Components B, D and E, anonymously.

**Reality:** The free tier does not count requests, it counts *call weight*, roughly
`ceil(days / 14) * ceil(variables / 10) * locations`. Components B and E need a long
reference distribution — a departure is only as meaningful as the record behind it — so the
water balance is thirty-eight years of daily data at eighty-eight lattice nodes. That is
about ninety thousand weighted calls whatever the batching, against a minutely allowance
somewhere near six hundred.

Three refusals, each teaching something different, and each costing a run:

1. Ten locations over thirty-eight years in one GET is refused outright, while the same
   window for one location succeeds every time. The batch is now sized by estimated weight
   rather than by how many coordinates fit in a URL.
2. A 429 whose body says *"Minutely API request limit exceeded"* clears in a minute.
   Exponential backoff from two seconds spends its retries inside the same window and then
   gives up, so a rate-limited call now waits sixty-five seconds.
3. A 429 whose body says *"Daily API request limit exceeded"* clears tomorrow. Retrying it
   eight times over nine minutes only lengthens the log, so it now raises
   `DailyQuotaExhaustedError` immediately.

**Built:** Weight-sized batches, a rate-limit-aware wait, a fast failure on the daily quota,
and — the part that matters most — per-chunk caching. Eight lattice nodes are fetched and
written together, so an interrupted fetch resumes rather than restarts. This is the same
recovery story the per-year label writes tell.

**Current state:** the day's quota was spent on recon and two aborted runs, so the B, D and E
archive is not built. The code is complete and tested against recorded fixtures, and
resuming is a single call that skips every chunk already on disk. Components A and C need no
Open-Meteo and are built: 43,303 cells each, with the composite over them.

**To close:** re-run `build_components(as_of=date(2023, 8, 14))` after the quota resets. If
this becomes routine rather than a one-off backfill, the honest fix is a shorter reference
period or an ERA5 source that is not metered per call — not more aggressive retrying.

**Raised:** 2026-08-11 · **Status:** closed 2026-08-13. The honest fix named above was
taken, and it was neither of the two guessed at: Open-Meteo publishes the archive its own API
serves from as `.om` files on an anonymous S3 bucket, readable by HTTP byte range with no
account, no token and no metering. `sources/om_archive.py` reads it; `sources/climate.py`
sits on top and the retry, pacing, weight-estimation and daily-quota machinery is deleted
rather than tuned. The whole eighty-eight node lattice, one variable, one year is about two
hundred range requests and under a megabyte. Verified against the API it replaces in
`docs/climate-store.md`; the two remaining differences are recorded as D-017 and D-018.

---

## D-017 — The published store is the reanalysis; the API is the reanalysis downscaled

**Specified:** Read the same ERA5 the archive API was serving, out of Open-Meteo's open-data
bucket, so that Components B, D and E stop being blocked on a quota.

**Reality:** It is the same data, and the check says so variable by variable. At 50.00 N,
119.50 W over 2023-08-10 to 2023-08-17, hourly: precipitation, shortwave radiation and both
soil moisture layers agree with the API **exactly**; 10 m wind agrees to 0.05 km/h, which is
the API's own rounding.

Two variables do not. Temperature and dew point come back exactly 3.0 K colder from the
store than from the API, for every hour tested. That is not drift and not a units error, it
is elevation:

| | |
|---|---|
| elevation of the ERA5 grid cell (`static/HSURF.om`) | 959 m |
| elevation the API downscales to | 499 m |
| offset that predicts at the 0.0065 K/m standard lapse rate | 2.99 K |
| offset measured | 2.97 K |

The API applies a lapse-rate correction from the reanalysis cell's elevation to the
requested coordinate's real elevation. The store holds the uncorrected reanalysis.

**Built:** The uncorrected reanalysis, deliberately. Components B, D and E are every one of
them a *departure* — B is a z-score of the water balance against the same node's own
thirty-eight year record, D is a z-score of Drought Code, Buildup Index and vapour pressure
deficit against the same node's own distribution on the same date in season, E is a SPEI
fitted to the same node's own series. A constant offset applied to every year of a node's
record cancels in the departure taken from it. Applying the correction would change no
reported number, and it would put a second elevation model into a chain that already carries
Copernicus DEM GLO-30 for Component A's terrain.

**What that costs, stated rather than hidden:** any *absolute* reading of temperature or
humidity off this pipeline is the 25 km cell's, not the ground's, and at 959 m against 499 m
that is three degrees. Nothing in the index is an absolute reading. If a later component
needs one — an absolute fire-danger class rather than a departure from normal — the
correction has to be applied at that point, and against each cell's own elevation rather
than the node's, which is a different and better calculation than the one being declined
here.

**Raised:** 2026-08-13 · **Status:** measured, deliberate, and stated in the source records;
`LAPSE_RATE_K_PER_M` exists in `sources/climate.py` only to name the offset not applied

---

## D-018 — Two variables the store does not carry, computed rather than fetched

**Specified:** Daily `precipitation_sum` and `et0_fao_evapotranspiration` for Components B
and E; hourly `relative_humidity_2m` at noon for Component D.

**Reality:** Open-Meteo's API derives reference evapotranspiration and relative humidity
rather than storing them, so neither is in the published store. Precipitation is stored, and
its daily sum is arithmetic on stored hours. The other two are not arithmetic on stored
hours; they are published equations evaluated on stored hours, which is a methodological
claim and has to be stated as one — the precedent D-010 set for the CFFDRS codes and D-015
for SPEI.

**Built, and measured rather than asserted.**

*Relative humidity* from dew point by the FAO-56 saturation vapour pressure curve (Allen et
al. 1998, eq. 11) — the same curve `refet` uses inside ET0, so the two derived variables
cannot disagree about what saturated air is. Against the API's own `relative_humidity_2m`
over June to August 2023, evaluated on the API's own temperature and dew point so the D-017
offset is not being measured instead: mean absolute difference **0.27 percentage points**,
worst hour 0.75. That is what rounding a percentage to a whole number costs.

*Reference evapotranspiration* by the FAO-56 Penman-Monteith daily equation through `refet`
(ASCE grass reference). Open-Meteo computes it hourly and sums. The daily form was chosen
because it is the published equation a validator can check against a textbook and it needs
no clear-sky radiation model to close the longwave term. Both run on the same inputs, over
365 days of 2023:

| | |
|---|---|
| correlation | 0.9912 |
| bias, ours minus theirs | -0.131 mm/day |
| mean absolute difference | 0.229 mm/day |
| annual total | 838 mm against 886 mm |
| May-September mean | 4.171 against 4.230 mm/day |

Nearly all of the bias sits in December and January, where both are near zero and the hourly
form's night-time clamping disagrees with the daily form's radiation balance about a
quantity that rounds to nothing. Through the fire season the two agree to within 1.5%.

**Why the offset is tolerable and where it would not be:** Component B is a departure and
Component E fits a log-logistic distribution to the same series, so a systematic offset in
the method shifts the reference and the observation together and largely cancels. A
consumer wanting an *absolute* water balance in millimetres — an irrigation figure, a
reservoir inflow — should not take it from here without reading this entry first.

**Raised:** 2026-08-13 · **Status:** built, quantified in `docs/climate-store.md`, bounds
pinned in `tests/eii/sources/test_climate.py`

---

## D-019 — Component E's three-month term is unavailable at most nodes, and that is the method

**Specified:** Component E is a multi-scale SPEI blend at 1, 3 and 12 months.

**Reality:** At the 2023 case-study date, SPEI-1 fits 88 of 88 lattice nodes and SPEI-12 fits
85, but **SPEI-3 fits 13**. The cause is not missing data — every node has a complete
three-month accumulation and a 38-season reference behind it. It is the distribution.

Vicente-Serrano's SPEI fits a three-parameter log-logistic by probability weighted moments.
That distribution has support above its origin and is right-skewed by construction, and its
shape parameter must exceed one or the fitted distribution has no moments at all —
`Gamma(1 - 1/beta)` diverges. Measured over the study lattice, for the accumulation window
ending in July:

| timescale | median sample skew | median fitted shape | nodes fitted |
|---|---|---|---|
| 1 month | +0.75 | 6.9 | 88 of 88 |
| 3 month | **-0.24** | **-22.7** | 13 of 88 |
| 12 month | +0.28 | 12.1 | 85 of 88 |

A dry interior summer's May-to-July water balance is left-skewed: there is a floor it cannot
go below, because reference evapotranspiration is bounded and precipitation cannot be
negative, and a ceiling that occasional wet years lift. The log-logistic cannot describe
that sample, and the estimator says so by returning a shape below one.

**Built:** the refusal. An unfittable node comes back missing, and the composite scores each
cell on the components and timescales it has rather than substituting zero — zero on a
departure scale is the strongest available claim of ordinariness, and the worst possible
thing to say about something that was never measured. `spei_at` now logs which of the two
refusals occurred, because a data gap and a method limit want different responses from
whoever reads the log. Bounds pinned in `tests/eii/test_drought.py`.

**What would close it, and why it was not done tonight:** the generalized logistic is the
three-parameter family that admits both skews and reduces to the log-logistic where the
sample is right-skewed. Switching to it would fit every node. It would also invalidate the
agreement table in D-015, which was measured against SPEIbase on the log-logistic and is the
only evidence that this implementation of SPEI is the same quantity SPEIbase publishes.
Changing the distribution and keeping the old validation would be worse than a missing
three-month term. The honest sequence is: change the family, re-measure against SPEIbase over
2015-2022, and report the new agreement.

**Raised:** 2026-08-13 · **Status:** open. The one-month and twelve-month terms carry the
component at most nodes; `contributing_variables` on every row says how many it had.

---

## D-020 — Four hundred and sixty NaNs in the archive where NULL was meant

**Specified:** A cell the pipeline reached but could not measure comes back missing, and
missing is `NULL`.

**Reality:** The 2023 Component A partition held 460 rows whose `value` was the float NaN
rather than SQL NULL. Both mean unmeasured. Carrying two spellings of it across the archive
boundary produced three different answers to one question:

- a reader counting `count(value)` or `WHERE value IS NOT NULL` **scored** them;
- a reader ordering by value put them wherever its collation happened to — which is how this
  surfaced, in the portfolio ranking built on top;
- `JSON.stringify` turned them into `null` anyway, so the served payload disagreed with the
  archive it came from.

It survived because every consumer written so far happened to handle it. The portfolio
ranking, which sorts, did not.

**Built:** non-finite floats become NULL at the archive boundary, in `write_component`, which
is the one place that can be certain it *is* the boundary. Only float columns are touched and
only their non-finite entries. Pinned in `tests/eii/test_archive.py`, including that an
already-finite table comes back untouched and that string columns are left alone.

This is the D-012 family from the other direction: there, ground a source did not cover came
back as a finite zero and was treated as data; here, a value that was genuinely absent came
back as a number and was counted as one.

**Raised:** 2026-08-13 · **Status:** fixed; the 2023 partitions were rewritten and now carry
460 nulls and no NaNs

---

## D-021 — h3-js answers an invalid cell id with a hexagon in the Arctic

**Specified:** The portfolio map draws each cell of a book as a polygon.

**Reality:** `cellToBoundary` from `h3-js` does not refuse a bad identifier. Given
`"not-a-cell"`, `"zzz"` or the empty string it returns the same well-formed seven-point
hexagon at about 69 N, 31 E — in Arctic Russia — with no error and nothing in the result
saying it was a guess. `isValidCell` reports all three as invalid; nothing calls it.

A book with one mistyped cell would therefore render a polygon several thousand kilometres
outside the study area. On a map that reads as a rendering glitch rather than a data error,
and if the map is zoomed to the study area it does not read as anything at all: the polygon
is simply off screen, and a layer with one silent stray is indistinguishable from a layer
that drew correctly.

This is the third instance of the same family in this build. D-012: a source that does not
cover the ground returns a finite zero. D-020: an unmeasured cell reaches the archive as NaN
and gets counted. Here: an unparseable identifier comes back as a location.

**Built:** `cellToRing` in `@gaia/core` checks `isValidCell` **before** the call rather than
inspecting what comes back, and raises naming the identifier. It also performs the
`[latitude, longitude]` to `[longitude, latitude]` swap GeoJSON requires and closes the ring,
both in one tested place — a ring built with the pair reversed is a valid polygon in the
wrong hemisphere, which is the same failure by a different route. Pinned in
`core/src/h3-geometry.test.ts`, including a case per invalid input.

**Raised:** 2026-08-13 · **Status:** fixed; the console map calls `cellToRing` and never
`cellToBoundary`
