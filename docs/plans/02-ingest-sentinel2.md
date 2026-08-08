# Milestone 2 — Sentinel-2 ingestion

**Goal:** Twelve months of cloud-masked monthly NDVI, NDMI and NBR composites for the pilot
area, landed in DuckDB and as COGs, every row carrying full provenance.

## Data source

**Element 84 Earth Search v1** (`https://earth-search.aws.element84.com/v1`), collection
`sentinel-2-l2a`. Chosen over Microsoft Planetary Computer because it is fully anonymous —
Planetary Computer requires SAS token signing through the `planetary-computer` package,
which is one more moving part between a fresh machine and a working demo. Probed before
committing: 16 scenes under 40% cloud over the pilot area in August 2025, delivered in
EPSG:32610, which is the analysis CRS, so the spectral path involves no reprojection at all.

Alternative if Earth Search degrades: Planetary Computer's `sentinel-2-l2a` collection, same
band structure, add `planetary_computer.sign_inplace` as the STAC modifier. The source
adapter is isolated in `sources/stac.py` for exactly this reason.

## Band choices

| Index | Formula | Bands | Native res |
|---|---|---|---|
| NDVI | (NIR − Red) / (NIR + Red) | B08 `nir`, B04 `red` | 10 m |
| NDMI | (NIR − SWIR1) / (NIR + SWIR1) | B8A `nir08`, B11 `swir16` | 20 m |
| NBR | (NIR − SWIR2) / (NIR + SWIR2) | B8A `nir08`, B12 `swir22` | 20 m |

NDMI and NBR use B8A rather than B08. B8A is the narrow near-infrared band at the same 20 m
resolution as the SWIR bands they are paired with, so the ratio is formed between bands the
sensor recorded at the same scale. Pairing 10 m B08 with 20 m B11 would require upsampling
the SWIR band, which fabricates detail. This follows the standard Sentinel-2 formulation of
both indices.

Reflectance is resampled to the common 20 m grid **before** the index is computed, by
area-weighted averaging. Computing at native resolution and averaging the index afterwards
would give a slightly different answer; averaging reflectance first is the convention and
keeps all three indices on one footing.

## Cloud masking

From the scene classification layer (SCL). Pixels retained: 4 (vegetation), 5 (not
vegetated), 7 (unclassified). Pixels discarded: 0 no data, 1 saturated, 2 dark/topographic
shadow, 3 cloud shadow, 6 water, 8 cloud medium probability, 9 cloud high probability,
10 thin cirrus, 11 snow and ice.

## The land mask problem

The pilot bounding box is mostly ocean — it covers the Southern Gulf Islands and the water
between them. If spatial coverage were computed against every pixel in the box, a perfect
cloud-free scene would report about 30% coverage, and the confidence score would be
measuring the Strait of Georgia rather than the data.

So the ingest derives a land mask once per area, from SCL water-class frequency across every
scene in the window: a pixel classified as water in 80% or more of observations is water.
Coverage, statistics and indices are all computed over land pixels only. The mask is written
as a COG and its derivation is a step in the provenance chain of every value that uses it.

This gets superseded in milestone 5, where the Copernicus DEM gives a cleaner land mask, but
the S2-derived one is self-contained and keeps this milestone independent.

## Compositing

Monthly. For each month, the least-cloudy **three** scenes are read (configurable via
`GAIA_MAX_SCENES_PER_MONTH`), masked, and reduced per pixel by median. Median rather than
mean because residual cloud that survives SCL masking is bright and skews a mean; the median
of three is robust to one bad observation.

Months with no usable scene produce no row rather than an interpolated one.

## Volume, honestly

The area is roughly 59 × 66 km. At 20 m that is a 2950 × 3300 grid. Three scenes a month for
twelve months across five assets is 180 windowed reads of roughly 10 MB each, and the area
spans more than one MGRS tile, so call it 3–4 GB of transfer and 15–25 minutes on a good
connection. Reads run eight at a time.

Windowed reads are what makes this feasible: rasterio requests only the byte ranges of the
COG covering the area, so a full Sentinel-2 tile never crosses the network.

## Determinism

Each run writes a `run_manifest` row with an `inputs_digest` (sorted source asset ids) and an
`outputs_digest` (hash of every value produced). Re-running the same command over the same
scenes must reproduce both. Re-ingesting an existing period is a no-op unless `--force` is
passed, which makes `make seed` resumable after an interruption.

## Assumptions

1. Cloud cover filter at 60% scene-level before selection; the SCL mask does the real work.
2. Scenes are deduplicated by MGRS tile and date — Earth Search lists reprocessed versions
   of the same acquisition, and the highest processing baseline wins.
3. Nodata is written as NaN in float32 COGs, deflate-compressed, tiled 512.

## Checkpoint

`gaia coverage` shows 12 months of NDVI, NDMI and NBR for the pilot area, with confidence
and validation status on every row, queryable in DuckDB.
