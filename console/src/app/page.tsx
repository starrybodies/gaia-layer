import Link from "next/link";
import { Shell } from "@/components/Shell";
import { Eyebrow, Stat } from "@/components/primitives";
import { api, type Coverage } from "@/lib/api";

export const dynamic = "force-dynamic";

/** Live figures for the hero. The page describes a measurement layer, so it measures itself. */
async function loadCoverage(): Promise<Coverage | null> {
  try {
    return await api.coverage();
  } catch {
    return null;
  }
}

const LESSONS = [
  {
    n: "01",
    title: "Build the verified substrate before the instrument",
    body: "Parametric triggers, biodiversity credits, resilience-linked premiums — every instrument people want built on ecological data presupposes a measurement layer that can be trusted and audited. Where that layer is missing, the instrument inherits its uncertainty silently, and settles on a number nobody can defend. So the substrate comes first. There is no token, no trigger and no financial instrument anywhere in this system.",
  },
  {
    n: "02",
    title: "Never let a language model be the system of record",
    body: "Language models are good at orchestration, retrieval and explanation. They are not a measurement apparatus. A model that produces a plausible ecological figure with no traceable derivation is worse than no figure, because it is indistinguishable in presentation from one that was measured. Every number here originates in the pipeline and passes the validation engine. The model reads, explains and cites. It never computes.",
  },
  {
    n: "03",
    title: "Price the land, not just the sky",
    body: "Climate risk analytics have concentrated on atmospheric hazard: the fire weather, the storm track, the temperature anomaly. Hazard is one half of risk. The other half is the condition of the ground the hazard arrives at — how dry the vegetation is, how much fuel has accumulated, what the soil is holding. Two parcels under identical fire weather do not carry identical risk, and that difference is measurable, and it is the half a landowner can actually change.",
  },
];

const LAYERS = [
  {
    n: "L1",
    name: "Ingestion",
    body: "Open Earth observation pulled for a configurable area and landed in a local data lake. Sentinel-2 read by windowed request — only the bytes covering the area, never the whole scene — plus ERA5-Land reanalysis and the Copernicus 30 m elevation model.",
    detail:
      "Every record carries source, dataset id, acquisition time, processing time, pipeline version and spatial reference. Provenance is a column, not metadata.",
  },
  {
    n: "L2",
    name: "Validation",
    body: "A constraint engine checks every derived value against physical bounds, its own history, and the other indicators measured beside it, before anything is served.",
    detail:
      "Three outcomes. Validated. Flagged — served, with what is odd about it attached. Rejected — never served as an answer, and reported as an absence with a reason.",
  },
  {
    n: "L3",
    name: "Agent interface",
    body: "Five tools over MCP, mirrored by a REST API, both over one service layer so the two can never drift. Agents are the primary consumer; this is the primary interface.",
    detail:
      "get_ecological_state · get_wildfire_substrate_score · get_provenance · compare_periods · list_coverage",
  },
  {
    n: "L4",
    name: "Application",
    body: "This console. A map of the area, a generated condition report, and a playground where you can watch an agent query the layer and check its prose against the raw tool output.",
    detail:
      "It reads the same public API an agent does, with no privileged path to the data.",
  },
];

const NOT = [
  "An ignition probability or a fire forecast. The substrate score describes the condition of the ground a fire would arrive at.",
  "A wind model. Wind dominates fire behaviour once burning, and it is weather rather than substrate.",
  "A fuel-load inventory. Identical spectral moisture can sit over very different tonnes per hectare.",
  "A globally calibrated index. The weights are anchored to one biogeoclimatic zone and say so.",
  "A daily product. Values are monthly composites and cannot resolve a drying event inside a month.",
];

export default async function LandingPage() {
  const coverage = await loadCoverage();
  const aoi = coverage?.aois[0];

  const monthly = (aoi?.indicators ?? []).filter((i) => i.last_period_end < "2099-01-01");
  const periods = monthly.reduce((max, i) => Math.max(max, i.period_count), 0);
  const meanConfidence =
    monthly.length > 0
      ? monthly.reduce((a, i) => a + i.mean_confidence, 0) / monthly.length
      : 0;
  const rejected = (aoi?.indicators ?? []).reduce((a, i) => a + i.rejected_count, 0);
  const flagged = (aoi?.indicators ?? []).reduce((a, i) => a + i.flagged_count, 0);

  return (
    <Shell active="/">
      {/* ───────────────────────────────────────────────────────────── hero */}
      <section className="atmosphere relative overflow-hidden">
        <div className="grid-field pointer-events-none absolute inset-0" />
        <div className="relative mx-auto max-w-[100rem] px-5 pt-20 pb-16 md:pt-28 md:pb-24">
          <div className="flex items-center gap-2.5">
            <span className="bg-signal pulse inline-block h-1.5 w-1.5 rounded-full" />
            <Eyebrow>
              {aoi === undefined ? "Layer offline" : "Live · pilot area ingested"}
            </Eyebrow>
          </div>

          <h1 className="display text-text mt-7 text-[2.6rem] leading-[1.04] sm:text-6xl lg:text-[5.2rem]">
            Ecological
            <br />
            ground truth
            <br />
            <span className="text-signal glow-signal">agents can cite</span>
          </h1>

          <p className="text-dim mt-8 max-w-2xl text-base leading-relaxed md:text-lg">
            Gaia is a measurement layer for the condition of land. It serves validated,
            provenance-tracked ecological state to software agents — and it will not return a
            number it cannot defend.
          </p>

          <p className="text-muted mt-4 max-w-2xl text-sm leading-relaxed">
            Every value comes back with a confidence score, a validation status, the citation
            for the method that produced it, and a chain you can follow to the individual
            satellite scenes it was computed from. Values that fail validation are reported as
            rejected rather than quietly omitted.
          </p>

          <div className="mt-10 flex flex-wrap gap-3">
            <Link
              href="/playground"
              className="numeric border-signal bg-signal text-void hover:bg-signal-dim border px-5 py-2.5 text-[11px] tracking-[0.16em] uppercase transition-colors"
            >
              Watch an agent query it
            </Link>
            <Link
              href="/map"
              className="numeric border-line-bright text-text hover:border-signal hover:text-signal border px-5 py-2.5 text-[11px] tracking-[0.16em] uppercase transition-colors"
            >
              Open the map
            </Link>
            <Link
              href="/report"
              className="numeric border-line-bright text-text hover:border-signal hover:text-signal border px-5 py-2.5 text-[11px] tracking-[0.16em] uppercase transition-colors"
            >
              Read a substrate report
            </Link>
          </div>

          {/* Live stats — the page describes a measurement layer, so it measures itself. */}
          {aoi !== undefined && (
            <div className="mt-16 grid grid-cols-2 gap-x-8 gap-y-6 md:grid-cols-4 lg:max-w-5xl">
              <Stat
                value={aoi.area_km2.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                label="km² under measurement"
                hint={`${aoi.grid_resolution_m} m analysis grid, ${aoi.analysis_crs}`}
              />
              <Stat
                value={String(aoi.indicators.length)}
                label="indicators served"
                hint="Spectral, climate, soil and terrain"
              />
              <Stat
                value={String(periods)}
                label="monthly periods"
                hint="Composited from Sentinel-2 and ERA5-Land"
              />
              <Stat
                value={meanConfidence.toFixed(2)}
                label="mean confidence"
                tone="signal"
                hint={`${flagged} flagged · ${rejected} rejected and withheld`}
              />
            </div>
          )}
        </div>
      </section>

      <div className="rule" />

      {/* ─────────────────────────────────────────────────────── the problem */}
      <section className="mx-auto max-w-[100rem] px-5 py-20 md:py-28">
        <div className="grid gap-12 lg:grid-cols-[22rem_1fr] lg:gap-20">
          <div>
            <Eyebrow>The problem</Eyebrow>
            <h2 className="display text-text mt-4 text-2xl md:text-3xl">
              Nobody can
              <br />
              check the number
            </h2>
          </div>
          <div className="max-w-3xl space-y-5 text-[15px] leading-relaxed">
            <p className="text-dim">
              An underwriter pricing wildfire exposure on a parcel of coastal British Columbia
              can obtain a great many numbers. What they cannot usually obtain is the answer to
              the next question: where did this come from, how confident should I be, and what
              would make it wrong.
            </p>
            <p className="text-muted">
              That gap is not a presentation problem. It is what stops ecological data being
              used in decisions with money attached. A figure that cannot be traced cannot be
              defended to a regulator, a reinsurer, or a landowner who disagrees with it — so it
              gets discounted to nothing, and the decision is made on the atmospheric hazard
              alone, which is the half that nobody on the ground can change.
            </p>
            <p className="text-muted">
              Language models made this worse before they made it better. A model will produce a
              plausible NDMI value for a polygon on request. It looks exactly like a measured
              one. The layer below is the part that has to be right.
            </p>
          </div>
        </div>
      </section>

      <div className="rule" />

      {/* ────────────────────────────────────────────────────── three lessons */}
      <section className="mx-auto max-w-[100rem] px-5 py-20 md:py-28">
        <Eyebrow>What shapes every decision here</Eyebrow>
        <h2 className="display text-text mt-4 max-w-3xl text-2xl md:text-4xl">
          Three lessons, and everything traces to one of them
        </h2>

        <div className="mt-14 grid gap-px md:grid-cols-3">
          {LESSONS.map((lesson) => (
            <article key={lesson.n} className="border-line bg-surface border p-6 lg:p-8">
              <span className="numeric text-signal text-xs tracking-[0.2em]">{lesson.n}</span>
              <h3 className="text-text mt-4 text-lg leading-snug">{lesson.title}</h3>
              <p className="text-muted mt-4 text-[13px] leading-relaxed">{lesson.body}</p>
            </article>
          ))}
        </div>
      </section>

      <div className="rule" />

      {/* ────────────────────────────────────────────────────────── the stack */}
      <section className="mx-auto max-w-[100rem] px-5 py-20 md:py-28">
        <div className="grid gap-12 lg:grid-cols-[22rem_1fr] lg:gap-20">
          <div className="lg:sticky lg:top-24 lg:self-start">
            <Eyebrow>The stack</Eyebrow>
            <h2 className="display text-text mt-4 text-2xl md:text-3xl">
              Four layers,
              <br />
              one vertical slice
            </h2>
            <p className="text-muted mt-5 text-sm leading-relaxed">
              v0.1 is deliberately narrow: one bioregion, one peril, cut all the way through.
              Southern British Columbia, wildfire substrate. A shallow layer that works end to
              end is worth more than four deep ones that do not meet.
            </p>
          </div>

          <div className="space-y-px">
            {LAYERS.map((layer) => (
              <article
                key={layer.n}
                className="border-line bg-surface hover:border-line-bright border p-6 transition-colors lg:p-8"
              >
                <div className="flex flex-wrap items-baseline gap-4">
                  <span className="numeric text-signal text-xs tracking-[0.2em]">{layer.n}</span>
                  <h3 className="display text-text text-base">{layer.name}</h3>
                </div>
                <p className="text-dim mt-4 max-w-3xl text-sm leading-relaxed">{layer.body}</p>
                <p className="numeric text-muted border-line mt-4 border-l-2 pl-4 text-[11px] leading-relaxed">
                  {layer.detail}
                </p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <div className="rule" />

      {/* ───────────────────────────────────────────────────────── the envelope */}
      <section className="mx-auto max-w-[100rem] px-5 py-20 md:py-28">
        <div className="grid gap-12 lg:grid-cols-2 lg:gap-20">
          <div>
            <Eyebrow>The guarantee</Eyebrow>
            <h2 className="display text-text mt-4 text-2xl md:text-3xl">
              A bare number is
              <br />
              <span className="text-signal">not representable</span>
            </h2>
            <div className="mt-6 space-y-4 text-sm leading-relaxed">
              <p className="text-dim">
                &ldquo;No number without provenance&rdquo; is not a policy anyone has to
                remember here. It is a property of the type system.
              </p>
              <p className="text-muted">
                The envelope every value travels in has no representation for a value without a
                provenance chain, a validation status, a confidence and a method. A rejected
                measurement is a different type entirely — one with no{" "}
                <span className="numeric text-dim">value</span> field at all, so it cannot be
                served as an answer by accident.
              </p>
              <p className="text-muted">
                Two more guards sit behind that: the service layer walks every response before
                returning it, and the API walks it again as JSON leaves the process. A test in
                CI asserts the whole thing is unconstructible rather than merely absent.
              </p>
            </div>
          </div>

          <div className="border-line bg-surface border">
            <div className="border-line flex items-center justify-between border-b px-4 py-2.5">
              <span className="eyebrow text-dim">Every value, every time</span>
              <span className="numeric text-faint text-[10px]">JSON</span>
            </div>
            <pre className="numeric text-dim overflow-x-auto p-4 text-[11px] leading-relaxed">
              {`{
  "indicator": "ndmi",
  "value": `}
              <span className="text-text">0.373</span>
              {`,
  "unit": "index",
  "confidence": `}
              <span className="text-signal">0.94</span>
              {`,
  "validation_status": `}
              <span className="text-signal">&quot;validated&quot;</span>
              {`,
  "flags": [],
  "method": {
    "name": "Normalised Difference Moisture Index",
    "citation": "Gao, B.-C. (1996). NDWI — A normalized
      difference water index... doi:10.1016/S0034-4257(96)00067-3"
  },
  "provenance": [ `}
              <span className="text-cyan">45 steps to 39 satellite scenes</span>
              {` ],
  "claim_id": `}
              <span className="text-signal-dim">&quot;clm_34H91YE51ZR24X0GV66X1472PG&quot;</span>
              {`
}`}
            </pre>
            <p className="text-faint border-line border-t px-4 py-3 text-[11px] leading-relaxed">
              Hand that <span className="numeric text-muted">claim_id</span> to{" "}
              <span className="numeric text-muted">get_provenance</span> and you get the
              processing chain and every contributing scene with its acquisition date.
            </p>
          </div>
        </div>
      </section>

      <div className="rule" />

      {/* ───────────────────────────────────────────────────── substrate score */}
      <section className="mx-auto max-w-[100rem] px-5 py-20 md:py-28">
        <div className="grid gap-12 lg:grid-cols-[22rem_1fr] lg:gap-20">
          <div>
            <Eyebrow>The composite</Eyebrow>
            <h2 className="display text-text mt-4 text-2xl md:text-3xl">
              A score you can
              <br />
              take apart
            </h2>
            <p className="text-muted mt-5 text-sm leading-relaxed">
              The wildfire substrate score is 0–100 and always arrives with its full
              decomposition: each indicator&rsquo;s measured value, how it was normalised, its
              weight, the points it contributed, and why it is in the scheme at all.
            </p>
            <p className="text-muted mt-4 text-sm leading-relaxed">
              The weights are a stated judgement, not an empirical fit, and the response says
              so. They are anchored to the Coastal Douglas-fir zone and versioned by scheme
              name, so a score computed today stays interpretable when they change.
            </p>
          </div>

          <div>
            <p className="text-dim text-sm leading-relaxed">
              A score a land manager cannot decompose is a score they cannot act on, and a score
              an underwriter cannot decompose is one they cannot defend. So the black box is not
              an option — which also means the layer has to be honest about what it leaves out.
            </p>

            <div className="border-line bg-surface mt-8 border p-6 lg:p-8">
              <Eyebrow>What this score does not model</Eyebrow>
              <ul className="mt-4 space-y-3">
                {NOT.map((item) => (
                  <li key={item} className="flex gap-3 text-[13px] leading-relaxed">
                    <span className="text-rust mt-1.5 inline-block h-px w-4 shrink-0 bg-current" />
                    <span className="text-muted">{item}</span>
                  </li>
                ))}
              </ul>
              <p className="text-faint mt-6 text-[11px] leading-relaxed">
                These travel with every score in the API response, not only on this page. A
                report that hid its caveats would be more persuasive and less defensible.
              </p>
            </div>
          </div>
        </div>
      </section>

      <div className="rule" />

      {/* ────────────────────────────────────────────────────────── for agents */}
      <section className="mx-auto max-w-[100rem] px-5 py-20 md:py-28">
        <Eyebrow>For agents</Eyebrow>
        <h2 className="display text-text mt-4 max-w-3xl text-2xl md:text-4xl">
          The interface is the product. The console is a window onto it.
        </h2>

        <div className="mt-12 grid gap-8 lg:grid-cols-2 lg:gap-16">
          <div className="space-y-5 text-sm leading-relaxed">
            <p className="text-dim">
              An underwriting agent does not want a dashboard. It wants a tool call that returns
              a number it can put in a memo with a citation attached.
            </p>
            <p className="text-muted">
              The layer speaks MCP over stdio and REST over HTTP, both bound to the same service
              layer, so the two interfaces cannot disagree about what the layer says. Point a
              client at it and the five tools appear.
            </p>
            <p className="text-muted">
              The playground on this site runs exactly that loop against a live model, and shows
              you every tool call and its raw response underneath the answer — so you can check
              the prose against what the layer actually returned rather than taking the
              model&rsquo;s word for it.
            </p>
            <Link
              href="/playground"
              className="numeric text-signal hover:text-text inline-block pt-2 text-[11px] tracking-[0.16em] uppercase underline decoration-dotted underline-offset-4 transition-colors"
            >
              Try it →
            </Link>
          </div>

          <div className="border-line bg-surface border">
            <div className="border-line border-b px-4 py-2.5">
              <span className="eyebrow text-dim">Connect an agent</span>
            </div>
            <pre className="numeric text-dim overflow-x-auto p-4 text-[11px] leading-relaxed">
              <span className="text-faint"># register the MCP server</span>
              {`
claude mcp add gaia -- pnpm --dir ./mcp-server start

`}
              <span className="text-faint"># or call the REST mirror directly</span>
              {`
curl -X POST `}
              <span className="text-signal-dim">
                https://gaia-layer.vercel.app/api/v1/ecological-state
              </span>
              {`
  -H 'content-type: application/json'
  -d '{"geometry":{"west":-123.9,"south":48.4,
       "east":-123.1,"north":49.0},
       "date_range":{"start":"2026-01-01",
                     "end":"2026-07-31"}}'`}
            </pre>
            <ul className="border-line divide-line divide-y border-t">
              {[
                ["get_ecological_state", "Condition and trend, per indicator, with envelopes"],
                ["get_wildfire_substrate_score", "Composite score with full decomposition"],
                ["get_provenance", "Any number, traced to its source scenes"],
                ["compare_periods", "Change between periods, with significance"],
                ["list_coverage", "What the layer can currently answer for"],
              ].map(([name, desc]) => (
                <li key={name} className="flex flex-col gap-0.5 px-4 py-2.5">
                  <span className="numeric text-signal-dim text-[11px]">{name}</span>
                  <span className="text-muted text-[11px]">{desc}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      <div className="rule" />

      {/* ────────────────────────────────────────────────────────────── pilot */}
      <section className="mx-auto max-w-[100rem] px-5 py-20 md:py-28">
        <div className="grid gap-12 lg:grid-cols-[22rem_1fr] lg:gap-20">
          <div>
            <Eyebrow>The pilot</Eyebrow>
            <h2 className="display text-text mt-4 text-2xl md:text-3xl">
              Southern
              <br />
              Gulf Islands
            </h2>
          </div>
          <div className="max-w-3xl space-y-5 text-sm leading-relaxed">
            <p className="text-dim">
              The pilot area covers the Southern Gulf Islands and the adjacent Coastal
              Douglas-fir zone on southeastern Vancouver Island — the driest biogeoclimatic zone
              on the British Columbia coast, and one where fire season is no longer an
              abstraction.
            </p>
            <p className="text-muted">
              Sentinel-2 delivers it in two MGRS tiles at 20 m, which is the native resolution
              of the shortwave-infrared bands that canopy moisture depends on. Twelve months of
              monthly composites, cloud-masked from the scene classification layer and reduced
              by median so one bad observation cannot carry a month.
            </p>
            <p className="text-muted">
              The area is a default, not a constant. Any GeoJSON polygon can be registered and
              the whole pipeline follows it — which is what makes this a layer rather than one
              bespoke study.
            </p>
            {aoi !== undefined && (
              <div className="border-line bg-surface mt-8 grid gap-px border sm:grid-cols-2">
                {[
                  ["Bounding box", `${aoi.bbox.west}, ${aoi.bbox.south} → ${aoi.bbox.east}, ${aoi.bbox.north}`],
                  ["Analysis grid", `${aoi.grid_resolution_m} m · ${aoi.analysis_crs}`],
                  ["Area", `${aoi.area_km2.toLocaleString(undefined, { maximumFractionDigits: 0 })} km²`],
                  [
                    "Last ingested",
                    aoi.last_ingested_at?.slice(0, 10) ?? "unknown",
                  ],
                ].map(([k, v]) => (
                  <div key={k} className="p-4">
                    <p className="eyebrow text-faint">{k}</p>
                    <p className="numeric text-dim mt-1.5 text-[11px]">{v}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ──────────────────────────────────────────────────────────────── cta */}
      <section className="atmosphere border-line relative overflow-hidden border-t">
        <div className="relative mx-auto max-w-[100rem] px-5 py-20 text-center md:py-28">
          <h2 className="display text-text mx-auto max-w-3xl text-2xl md:text-4xl">
            Ask it something an
            <br />
            <span className="text-signal glow-signal">underwriter would ask</span>
          </h2>
          <p className="text-muted mx-auto mt-6 max-w-xl text-sm leading-relaxed">
            Then follow any figure in the answer back to the satellite scenes it came from.
          </p>
          <div className="mt-9 flex flex-wrap justify-center gap-3">
            <Link
              href="/playground"
              className="numeric border-signal bg-signal text-void hover:bg-signal-dim border px-5 py-2.5 text-[11px] tracking-[0.16em] uppercase transition-colors"
            >
              Open the agent playground
            </Link>
            <Link
              href="/map"
              className="numeric border-line-bright text-text hover:border-signal hover:text-signal border px-5 py-2.5 text-[11px] tracking-[0.16em] uppercase transition-colors"
            >
              Explore the map
            </Link>
          </div>
        </div>
      </section>
    </Shell>
  );
}
