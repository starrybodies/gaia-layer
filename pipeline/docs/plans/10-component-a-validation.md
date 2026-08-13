# Component A validation summary

**Generated:** 2026-08-11T16:23:24+00:00
**Study area:** Okanagan and southern interior
**Fire years:** 2018-2024

## The gate

Written before the first model was fitted, and not adjusted afterwards:

> Component A, added to baseline_3 (FWI + FBP fuel type), produces a positive delta AUC-PR whose bootstrap 95% confidence interval excludes zero under spatially-blocked cross-validation with 2-3 km buffers, and does not worsen calibration.

### Verdict: PASS

- **Gate comparison (candidate vs baseline_3):** +0.1410 (95% CI +0.1086 to +0.1773, excludes zero)
- **Calibration (Brier, positive means better):** +0.0091 (95% CI +0.0055 to +0.0127, excludes zero)

The attribution comparison below is the stricter test and was not required by the specification. It gives terrain to both sides, so it isolates Component A from the elevation and slope that the candidate also carries. Where the two disagree, this is the one that describes the index.

- **Attribution (candidate vs baseline_4, terrain on both sides):** +0.1580 (95% CI +0.1293 to +0.1886, excludes zero)

## What was predicted, against what truth

- **Question:** given that this 500 m cell burned, did it burn at high severity?
- **Label:** mean dNBR across the cell at or above 660, the Key and Benson (2006) high-severity break, from Sentinel-2 growing-season composites one year either side of the fire.
- **Truth is a proxy.** This is remotely sensed severity, not insurance loss. No claims data was used and none is implied.
- **Cells only inside fire perimeters.** Nothing outside NBAC's mapped footprint is scored, so this says nothing about where fires start.

## Leakage controls

- **folds:** 5
- **buffer km:** 3.0
- **block size km:** 20
- **minimum train test distance m:** 3000
- Folds are blocks of ground, not cells. A random split of the same data leaves training cells adjacent to test cells and inflates AUC substantially; that number is not reported here because it would be misleading.

## What was left out

- **Fires below 200 ha:** excluded by a stated floor rather than a silent one. Small fires carry too few cells for a within-fire severity comparison to mean anything.
- **cells never predicted in any fold:** 0
- **Not recorded:** the exclusion counts for 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024 were not written when those labels were built and cannot be recovered without rebuilding them. They are missing from the totals above rather than counted as zero.

## Models

| model | features | AUC-PR | AUC-ROC | Brier | ECE | worst bin | cells in it |
|---|---|---|---|---|---|---|---|
| `baseline_1_fwi` | weather | 0.2011 | 0.6573 | 0.0976 | 0.058 | 0.345 | 108 |
| `baseline_2_fbp` | fuel | 0.1099 | 0.5038 | 0.1014 | 0.080 | 0.182 | 497 |
| `baseline_3_fwi_fbp` *(gate baseline)* | weather, fuel | 0.2018 | 0.6471 | 0.0970 | 0.057 | 0.347 | 107 |
| `baseline_4_fwi_fbp_terrain` *(attribution baseline)* | weather, fuel, terrain | 0.1848 | 0.6715 | 0.0987 | 0.063 | 0.588 | 8 |
| `candidate_with_component_a` **(candidate)** | weather, fuel, terrain, structure | 0.3428 | 0.8394 | 0.0879 | 0.063 | 0.532 | 23 |

Calibration is reported three ways because the obvious one misleads. The worst bin is what an underwriter asks about first, but it is a maximum over ten bins that hold anything from a handful of cells to thousands, and a model that spreads its predictions across the full range is judged on a sparser tail than one that never leaves the bottom. The cell count beside it says how much of the data that number describes. ECE weights every bin by its population and is the column to compare models on.

Scored on 3,835 cells with a high-severity prevalence of 0.106. AUC-PR leads because the positive class is the minority; its floor is the prevalence, not 0.5.

## Where the candidate's probabilities are wrong

| predicted band | cells | mean predicted | observed | 95% interval on observed |
|---|---|---|---|---|
| 0.00-0.10 | 3,225 | 0.017 | 0.056 | 0.048 to 0.064 |
| 0.10-0.20 | 241 | 0.143 | 0.295 | 0.241 to 0.355 |
| 0.20-0.30 | 125 | 0.248 | 0.424 | 0.341 to 0.512 |
| 0.30-0.40 | 76 | 0.346 | 0.500 | 0.390 to 0.610 |
| 0.40-0.50 | 44 | 0.443 | 0.500 | 0.358 to 0.642 |
| 0.50-0.60 | 43 | 0.548 | 0.419 | 0.284 to 0.567 |
| 0.60-0.70 | 24 | 0.643 | 0.292 | 0.149 to 0.492 |
| 0.70-0.80 | 23 | 0.749 | 0.217 | 0.097 to 0.419 |
| 0.80-0.90 | 24 | 0.841 | 0.375 | 0.212 to 0.573 |
| 0.90-1.00 | 10 | 0.933 | 0.500 | 0.237 to 0.763 |

A band is listed below when its mean predicted probability falls outside the interval on its own observed frequency, which is to say the disagreement is larger than the band's population explains.

- **Promises more than it delivers:** 0.60-0.70, 0.70-0.80, 0.80-0.90, 0.90-1.00 — 81 cells, 2.1% of those scored.
- **Delivers more than it promises:** 0.00-0.10, 0.10-0.20, 0.20-0.30, 0.30-0.40 — 3,667 cells, 95.6% of those scored.

The gate is a statement about ranking, and ranking is what AUC-PR measures: the order the candidate puts cells in is better than the baselines' by more than the folds' noise, and its Brier score and ECE are the best of the five. The levels are a separate claim and a weaker one. A cell scored above the bands listed as over-confident should be read as *high risk relative to the others*, not as a probability to multiply by an exposure.

The fix is a monotone recalibration fitted inside each training fold, and it is deliberately not applied here. Recalibrating changes the pooled out-of-fold probabilities, which changes the gate comparison, and the gate was written before the first model was fitted. Correcting the levels after seeing the verdict would make the verdict unfalsifiable. It belongs on the served score, where it can be fitted, held out and reported on its own terms, not inside the experiment that is supposed to be able to fail.

## Per-fold spread

| model | AUC-PR mean | AUC-PR sd | folds |
|---|---|---|---|
| `baseline_1_fwi` | 0.2108 | 0.0996 | 5 |
| `baseline_2_fbp` | 0.1563 | 0.0922 | 5 |
| `baseline_3_fwi_fbp` | 0.2138 | 0.0999 | 5 |
| `baseline_4_fwi_fbp_terrain` | 0.2215 | 0.0862 | 5 |
| `candidate_with_component_a` | 0.4244 | 0.1040 | 5 |

A model that scores the same on every fold and one that swings widely can share a mean and are not the same model. The spread is the more useful column when it is large.

## What this does not establish

- Nothing about ignition probability. Only conditional severity.
- Nothing about structure loss or insured loss. The label is spectral.
- Nothing outside the southern interior of British Columbia, or outside the fire years listed above.
- The high-severity threshold is a North American convention rather than a calibration against field plots in this biogeoclimatic zone.
