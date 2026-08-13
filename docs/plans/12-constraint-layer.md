# 12 — The mechanistic constraint layer

**Status:** built and tested.

## The problem it exists for

A fitted model can be right about the data and wrong about the world, and neither AUC nor a
calibration curve will say so. It can learn that severity falls as the Drought Code climbs,
because in this sample the worst fires happened to land in a year the codes lagged. It can
rank ponderosa pine above standing grass, because the grass cells in the training set sat
near roads. Both are statements a fire scientist would reject on sight and neither shows up
in any metric the validation report carries.

## Three rules, in two kinds

**Statements about the model.** These either hold for a fitted model or they do not, and no
per-cell adjustment repairs them.

1. **Monotonicity.** Partial dependence must not run against mechanism in the Drought Code,
   the Buildup Index, vapour pressure deficit, or either soil-moisture depth. The sweep is
   over the real rows at each feature's own deciles, not over a synthetic grid, so it asks
   the model about combinations of weather and fuel that occur on this ground rather than
   about ones that do not. A feature the model ignores produces a flat curve and *holds*:
   being uninformative is not the same as being backwards, and failing a model for not using
   a variable would be a rule about feature selection wearing a rule about physics.

2. **CFFDRS consistency.** Mean predicted severity per fuel type must rank the way the FBP
   System's own head fire rate of spread ranks them. The reference is not an ordering
   somebody here felt was about right: `sources/fbp.py` now carries the published
   `ROS = a(1 - e^{-b·ISI})^c` coefficients from Forestry Canada Fire Danger Group (1992),
   ST-X-3 Table 6, evaluated at ISI 10. Mixedwood spread is interpolated between C-2 and D-1
   by conifer content, which is what the system does for M-1 and M-2. Non-fuel, water and
   unclassified have no rate of spread at all and are dropped from the ordering rather than
   ranked at zero, which would place a lake below a leafless aspen stand instead of outside
   the comparison.

**A statement about a cell.**

3. **Water-balance sanity.** A cell in intact riparian context, predicted in the top decile,
   with no overriding weather signal, is being asked to burn the way wet ground does not
   burn. It is clamped to the top-decile envelope, marked low confidence, and the rule that
   fired is recorded. All three conditions are necessary: intact ground can be severe, the
   top decile can contain wet ground, and under extreme weather both happen at once — 2021 in
   this same valley — which is not something to constrain away.

## The move this adds to v0.1's vocabulary

v0.1 draws one line: rejection says the number is wrong, flagging says the number may be
right and you should know what is odd about it. Clamping is the third move — the number is
outside what mechanism allows, so it is pulled to the boundary and marked as having been
pulled. It is not deletion: a clamped cell stays among the more severe ground, because the
model had a reason and mechanism only bounds it.

Nothing implausible is emitted silently and nothing is silently removed.

## One bug the tests caught, worth keeping

The override test compares each cell's weather against the run's 90th percentile. Written as
`signal < extreme`, a run where the weather does not vary at all makes every cell look
overridden, which switches the rule off entirely and without a trace. It is now
`~(signal > extreme)`, strictly: where the weather is flat, no cell's weather overrides any
other's. A cell with no weather behind it does not override either — absence is not an
extreme.

## Verification

`tests/eii/test_eii_constraints.py`, 18 tests. Both directions of the monotonicity check are
pinned, a model that inverts the FBP ordering must fail, and the clamped cells must come back
flagged rather than quietly adjusted.
