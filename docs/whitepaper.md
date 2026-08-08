> ## ⚠️ PLACEHOLDER — NOT THE REAL WHITEPAPER
>
> The build prompt states that the Gaia AI whitepaper lives at this path and is the
> source of truth for product intent. It was not present in this repository or
> anywhere on this machine when the v0.1 build began on 2026-08-07.
>
> This file is a reconstruction of product intent from the three lessons the build
> prompt quotes directly, written so that engineering work had a stated target to
> trace decisions back to. It contains **no data, no figures, and no claims beyond
> what the build prompt itself asserts.** Nothing here should be quoted externally.
>
> **Replace this file with the real whitepaper and re-read `docs/plans/`.** Where the
> real document contradicts anything below, the real document wins, and the
> divergence gets logged in `docs/DIVERGENCES.md`.

---

# Gaia AI — Ecological Intelligence Layer

## What this is

An agent-native ecological ground truth service. It exposes validated,
provenance-tracked ecological state for a defined area of interest, so that a
software agent acting on behalf of an underwriter, a conservation organisation,
or a land manager can obtain a number, know how confident to be in it, and cite
where it came from.

AI agents are the primary consumer. Human interfaces are windows into the layer.
The layer is the product.

## The three lessons

Every architectural decision in this repository traces to one of these.

### 1. Build the verified substrate before the instrument

The instruments people want built on top of ecological data — parametric
triggers, biodiversity credits, resilience-linked premiums — all presuppose a
measurement layer that can be trusted and audited. Where that layer is absent,
the instrument inherits its uncertainty silently, and the failure mode is that
the instrument settles on a number nobody can defend.

The consequence for this codebase is ordering. The ingestion, validation, and
provenance layers are built and hardened first. No financial instrument, trigger
design, or tokenised representation appears in v0.1 at all. The substrate has to
be defensible before anything is priced off it.

### 2. Never let a language model be the system of record for a quantitative claim

Language models are effective at orchestration, retrieval, and explanation. They
are not a measurement apparatus. A model that produces a plausible ecological
figure with no traceable derivation is worse than no figure, because it is
indistinguishable in presentation from one that was measured.

The consequence for this codebase is a hard architectural boundary. All numeric
values originate in the Python pipeline and pass through the validation engine.
The language model reads those values, explains them, and cites them. It never
computes them, and it is never the origin of a figure. This is enforced by the
schema — a value cannot cross the service boundary without a non-empty
provenance chain, a validation status, and a quantified confidence — and by a
test in CI that fails the build if a served response shape can carry a bare
number.

The standard is: an underwriting agent must be able to cite any number this
system returns, and a human reviewing that citation must be able to trace it to
source observations.

### 3. Price the land, not just the sky

Climate risk analytics have concentrated on atmospheric hazard — the fire
weather, the storm track, the temperature anomaly. Hazard is one half of risk.
The other half is the condition of the ground the hazard arrives at: how dry the
vegetation is, how much fuel has accumulated, what the soil is holding, how the
terrain will move water and fire across the landscape.

Two parcels under identical fire weather do not carry identical risk, and the
difference lives in the substrate. That difference is measurable from open Earth
observation data, and it is what a landowner can actually change through
management.

The consequence for this codebase is the choice of indicators. v0.1 measures
ecological substrate condition — vegetation structure and dryness, fuel
condition proxies, soil and surface moisture, terrain, water retention — and
composes them into a wildfire substrate score whose decomposition into
contributing indicators is always returned alongside the score. There are no
black-box composites, because a score a land manager cannot decompose is a score
they cannot act on.

## The stack

**Layer 1 — Ingestion.** Open Earth observation data pulled for a configurable
area of interest and landed in a local data lake. Every record carries source,
dataset identifier, acquisition timestamp, processing timestamp, pipeline
version, and spatial reference. Provenance is a first-class column, not
metadata.

**Layer 2 — Validation.** A constraint engine that checks derived values against
physical and ecological bounds before anything is served: hard physical bounds,
temporal consistency, cross-variable coherence. Every value carries a validation
status and a quantified uncertainty. Rejected values are never served as
answers. Flagged values are served with the flag attached.

**Layer 3 — Agent interface.** An MCP server exposing validated ecological state
as tools, mirrored by a REST API for non-MCP consumers, both over one service
layer.

**Layer 4 — Application.** A console for humans: a map of the area of interest
with indicator layers, a generated ecological condition report, and a playground
where an agent can be watched querying the layer and returning citations.

## v0.1 scope

One bioregion, one peril, one vertical slice through all four layers. The pilot
area of interest is a wildfire-relevant zone in southern British Columbia,
covering the Southern Gulf Islands and the adjacent coastal Douglas-fir zone on
Vancouver Island. The peril focus is wildfire substrate.

Out of scope for v0.1: environmental DNA, biodiversity indices, flood, parametric
trigger design, any token or financial instrument, authentication beyond a single
API key, and multi-tenancy.
