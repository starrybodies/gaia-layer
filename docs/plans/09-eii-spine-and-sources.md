# Milestone 9 — The v0.2 spine, its sources, and the validation harness

**Status:** built and tested; the gate result is milestone 10

v0.2 asks a different question from v0.1. v0.1 measures ecological condition and serves it
with provenance. v0.2 tries to earn a claim: that landscape condition explains burn severity
beyond what fire weather and a static fuel map explain. That is falsifiable, so most of the
engineering here exists to make the falsification honest.

## The spine

H3 resolution 8 over 28,000 km² of the Okanagan and the southern interior — 43,303 cells,
6,305 resolution-7 parents — on a BC Albers grid at 30 m.

The load-bearing decision is how a raster becomes cells. The obvious way asks, for every
pixel, which cell contains it: thirty million calls into a Python H3 binding, half an hour,
repeated for every layer. Instead the cells are rasterised once — they are polygons, and
`rasterio.features.rasterize` burns polygons in C — producing an index array holding the
owning cell's row number per pixel. Cached, every later aggregation is a `np.bincount`. The
whole spine builds in four seconds.

Three aggregators sit on top, sharing one convention: a cell with no evidence behind it
returns NaN with a valid fraction of zero, never a zero pretending to be a measurement.
`mean` carries the fraction because a cell assembled from three pixels and one assembled
from eight hundred are different claims. `majority` exists so a categorical layer is never
averaged into a class that is not present.

## Provenance by reference

v0.1 writes a full provenance chain as JSON on every value. At this scale that is twenty-
five million copies of the same paragraph. v0.2 stores `method_id`, `run_id` and
`source_set_id` on the fact row and assembles the chain from dimension tables on read. What
a caller receives is unchanged — sources, native resolutions, citations, retrieval times,
PROV-O fields — so this is a storage change, and the tests are written to prove it is not an
honesty regression. Facts live in Parquet partitioned by component and year, replaced rather
than appended, because a rebuild of 2023 should leave 2023 as the rebuild found it.

## Sources, and what recon changed

Every source is open and anonymous; no account, token or key appears anywhere in the path.
Three planned sources did not survive contact.

**Canopy height.** ETH's 10 m product was the plan. Its documented host now returns 403 and
only a personal file-share link survives, so canopy height comes from the GLAD/Potapov 2019
North America mosaic at 30 m — native to our grid, so the read is like-for-like. The mosaic
is strip-organised rather than tiled, so a window read pulls full continental scanlines;
affordable exactly once, for a single epoch.

That module also carries the most interesting bug that did not happen. GLAD encodes water as
101 and snow as 102. An averaged output pixel drawing on one water pixel and three 20 m
stands produces a plausible 40 m canopy that an upper bound would pass. So the read is done
twice — once averaged for the value, once with max resampling to find the largest source
value behind each output pixel — and a pixel survives only if nothing flagged contributed.
The shoreline goes missing rather than wrong.

**Fire weather.** CWFIS publishes FWI grids for the current day only, so the ten-year archive
had to be computed. Nothing on PyPI does it: `PyFWI` is seismic full-waveform inversion,
`fwi` is an empty placeholder, the reference `cffdrs` is R, and NRCan's own Python implements
the next-generation hourly system. So the 1985 equations are implemented here — and checked
against the agency that wrote them, because CWFIS distributes station observations alongside
the codes it computed from them. Over ten station-seasons at five Okanagan stations, the
Drought Code agrees within 0.7 units across a whole season, FFMC within 4.5, ISI within 0.8,
FWI within 3.

The Duff Moisture Code runs up to 23% high, and that is understood rather than tolerated.
Fitting the day-length factor back out of CWFIS's own increments reproduces the published
table from July onward — 12.49 against 12.40, 11.10 against 10.90, 9.53 against 9.40 — and
falls short only in April and May, when CWFIS suspends code advance for snow on the ground.
Their series encodes an operational policy; ours encodes the specification.

**Burn severity.** The plan said Landsat Collection 2 Level 2 through Earth Search, and recon
confirmed the collection is catalogued there anonymously. Its assets are not: every href
points at `usgs-landsat.s3`, which is Requester Pays. Cataloguing and access are different
promises and only the first was checked. The first real run returned zero labels for 2023
and this was why.

Severity now comes from Sentinel-2 L2A on Earth Search — genuinely anonymous, the route v0.1
already uses — from B8A and B12 at 20 m, finer than the 30 m planned. The cost is the first
two years: Sentinel-2B launched in March 2017, so a pre-fire season for a 2017 fire rests on
one satellite at a ten-day repeat.

One trap is handled explicitly. Since processing baseline 04.00 the products carry an
additive offset of −1000, and NBR is a normalised difference, so a common offset does not
cancel. Reading a 2023 scene with the pre-2022 convention shifts every severity value rather
than failing loudly.

**Fuel type.** The national FBP grid has no colour table or raster attribute table, so the
code-to-class mapping was established from two independent NRCan documents that agree,
cross-checked against the file's own statistics. What the study window contains is worth
recording: C-7, the type that actually names the dry Okanagan's ponderosa and Douglas-fir,
covers 0.5%, while C-5 — red and white pine, species that do not grow in British Columbia —
covers 9.8%. That is Baron et al. 2024's 58% mismatch finding visible in our own window, and
it is why this layer is a lossy prior rather than ground truth.

**Inventory.** BCGW's WFS turns out to be unpageable on four of its five layers: any request
carrying `startIndex` returns 504 after sixty seconds, while the identical request without it
returns in 0.3 s. Paging is therefore spatial — request a box, and if more features match
than were returned, quarter it and recurse. Feature ids are also per-request, so deduplication
is by content hash rather than id.

## The label

`target.py` is the only module whose errors cannot be caught downstream, and it is built
around three refusals. A cell only partly inside a perimeter is dropped: severity averaged
across burned and unburned ground describes nothing. A cell without enough clear imagery
either side of the fire is dropped: missing severity is not low severity. A fire with fewer
than two usable scenes in either season produces no labels. Every exclusion is counted and
returned alongside the labels.

A fire-size floor of 200 ha exists for compute reasons, and is reported in both fires and
hectares so a reader can see what it cost.

## The validation harness

Folds are blocks of ground, not cells, with a 3 km buffer cut around every test block.
Published wildfire-susceptibility work shows random splits producing AUC near 0.99 and the
same models collapsing to 0.55–0.66 once folds are spatially disjoint; the 0.99 is the same
afternoon appearing on both sides. `leakage_report` measures the guarantee rather than
assuming it, and the test suite asserts that a random split of the same cells leaves training
data 500 m from test data while the blocked split holds 3 km.

Deltas are a paired bootstrap — both models scored on the same resampled cells each
iteration — because the question is whether this model beats that one on this ground, not
whether two independent intervals overlap. AUC-PR leads because high-severity cells are the
minority and ROC pays a model for true negatives it gets for free.

**A fourth baseline was added that the specification did not ask for.** The spec's gate
compares the candidate against weather plus fuel type, but the candidate also carries
terrain, so elevation and slope alone could win that comparison while Component A
contributed nothing. `baseline_4` gives terrain to both sides, and the difference between it
and the candidate isolates the component. A test proves it earns its place: on synthetic
ground where the signal lives entirely in terrain, the candidate beats the spec's baseline
and correctly shows nothing against the attribution baseline.

## Component A

Structure z-scores against each cell's own BEC subzone-variant crossed with cover class, so
"dense" means dense for an Interior Douglas-fir Very Dry Hot Okanagan stand rather than dense
for British Columbia. Sparse, unstratified and degenerate reference strata are flagged
separately, and uncertainty is widened rather than narrowed when the reference is weak —
without that, the fallback to a global reference would have reported a *tighter* interval
than the stratum that failed.

The sign is a documented hypothesis, not a finding. Parks et al. 2018 makes live fuel the
dominant variable group; Whitman et al. 2018 found open high-basal-area stands burning less
severely. If validation contradicts the direction, that is a result, and the fix is three
entries in one constant.

## Counts

- 43,303 cells, 6,305 parents, 37 million grid pixels
- 27 fires over the study area in 2017, 53 in 2018, 56 in 2021, 30 in 2023
- 265 tests across the v0.2 modules, all passing, none touching the network
