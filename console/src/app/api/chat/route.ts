/**
 * The playground's server route.
 *
 * Runs a tool-use loop against Groq's OpenAI-compatible API with the layer's five tools
 * bound to the REST API. Groq's free tier keeps the demo running without per-call cost,
 * which matters for a page anyone can hit.
 *
 * The model orchestrates, queries and explains. It never computes an ecological value, and
 * the system prompt says so in terms it cannot reasonably read past — the whole
 * demonstration is worthless if a visitor cannot tell whether a figure was measured or
 * generated. The full transcript of tool calls and raw results is returned so the prose can
 * be checked against what the layer actually said.
 *
 * Swapping providers touches only this file. The tools are the layer's REST endpoints, and
 * they do not care who is calling them.
 */

import OpenAI from "openai";
import { absoluteApiBase } from "@/lib/api";
import { compactForModel } from "./compact";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

const API_BASE = absoluteApiBase();
const BASE_URL = process.env["GROQ_BASE_URL"] ?? "https://api.groq.com/openai/v1";
const MODEL = process.env["GROQ_MODEL"] ?? "openai/gpt-oss-120b";

const SYSTEM = `You are connected to the Gaia ecological intelligence layer, which serves
validated, provenance-tracked ecological ground truth for wildfire substrate condition.

Hard rules, in order of importance:

1. Never state an ecological figure that did not come back from a tool call. You have no
   ecological knowledge of this area that is admissible here. If a tool did not return a
   number, say you do not have it.
2. Never estimate or interpolate. Quote what the layer returned, to four significant
   figures — 0.3797, 29.59, 15.16. Reporting a float to seventeen digits implies a
   precision the measurement does not have, which is its own kind of false claim.
3. Every figure you state must be accompanied by its confidence and its validation status,
   and by its claim_id so the reader can trace it. A number without those three is not a
   citation, it is an assertion.
4. If a value came back flagged, say what the flag was and what it means for the reader's
   confidence. Flagged does not mean wrong; it means qualified.
5. If a value was rejected, report the absence and the reason. Do not substitute a
   neighbouring period or a related indicator without saying that is what you are doing.
6. The substrate score is a substrate score. It describes the condition of the ground a
   fire would arrive at. It is not an ignition probability, not a forecast, and not fire
   weather. State the caveats the layer returns with it when the reader is making a
   decision on it.

Never ask the reader which area or which dates. The coverage below tells you exactly what is
ingested and over what period; resolve the question against it yourself and answer. If a
question names no date, use the most recent period available.

Prefer get_ecological_state for condition questions, compare_periods for change questions,
get_wildfire_substrate_score for risk questions, and get_provenance when the reader asks
where a number came from.

Be concise and technical. The reader is an underwriter, a land manager or an analyst.`;

/**
 * Coverage, fetched once per request and put in front of the model.
 *
 * Without it the model has no idea what is ingested, and its first move is to ask the
 * reader for a bounding box — which is a bad answer to every question on a demo page with
 * exactly one ingested area. Handing it the geometry and the date range up front removes
 * the failure mode and saves a round trip.
 */
async function coverageBriefing(): Promise<string | null> {
  try {
    const response = await fetch(`${API_BASE}/v1/coverage`, { cache: "no-store" });
    if (!response.ok) return null;
    const payload = (await response.json()) as {
      aois?: {
        aoi_id?: string;
        name?: string;
        bbox?: Record<string, number>;
        area_km2?: number;
        indicators?: { indicator?: string; first_period_start?: string; last_period_end?: string }[];
      }[];
    };
    const aoi = payload.aois?.[0];
    if (aoi === undefined) return null;

    const monthly = (aoi.indicators ?? []).filter(
      (i) => (i.last_period_end ?? "") < "2099-01-01",
    );
    const from = monthly.reduce(
      (a, i) => (a === "" || (i.first_period_start ?? "") < a ? (i.first_period_start ?? "") : a),
      "",
    );
    const to = monthly.reduce(
      (a, i) => ((i.last_period_end ?? "") > a ? (i.last_period_end ?? "") : a),
      "",
    );

    return [
      "Currently ingested — use this as the geometry for every tool call:",
      "",
      `Area: ${aoi.name} (aoi_id ${aoi.aoi_id}), ${Math.round(aoi.area_km2 ?? 0)} km2.`,
      `Geometry to pass: ${JSON.stringify(aoi.bbox)}`,
      `Monthly indicators cover ${from} to ${to}.`,
      `Available indicators: ${(aoi.indicators ?? []).map((i) => i.indicator).join(", ")}.`,
      "",
      "Terrain indicators are static and carry a sentinel period; do not quote their dates.",
    ].join("\n");
  } catch {
    return null;
  }
}

interface ToolSpec {
  // The function variant specifically. `ChatCompletionTool` is a union that also
  // covers custom tools, which have no `function` member.
  definition: OpenAI.Chat.Completions.ChatCompletionFunctionTool;
  call: (input: Record<string, unknown>) => Promise<Response>;
}

const GEOMETRY_PROPERTY = {
  type: "object",
  description:
    "Area to describe. A GeoJSON Polygon/MultiPolygon in WGS84, or a bounding box object " +
    "with west/south/east/north. Must match an ingested area — list_coverage returns one.",
  properties: {
    west: { type: "number" },
    south: { type: "number" },
    east: { type: "number" },
    north: { type: "number" },
  },
} as const;

const DATE_RANGE_PROPERTY = {
  type: "object",
  properties: {
    start: { type: "string", description: "YYYY-MM-DD" },
    end: { type: "string", description: "YYYY-MM-DD" },
  },
  required: ["start", "end"],
} as const;

function jsonPost(path: string) {
  return (input: Record<string, unknown>): Promise<Response> =>
    fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
      cache: "no-store",
    });
}

const TOOLS: ToolSpec[] = [
  {
    definition: {
      type: "function",
      function: {
        name: "list_coverage",
        description:
          "What the layer can answer for: ingested areas with their bounding boxes, the " +
          "indicators available for each, date ranges, and data quality — mean confidence " +
          "and counts of validated, flagged and rejected values. Call this first.",
        parameters: { type: "object", properties: {} },
      },
    },
    call: () => fetch(`${API_BASE}/v1/coverage`, { cache: "no-store" }),
  },
  {
    definition: {
      type: "function",
      function: {
        name: "get_ecological_state",
        description:
          "Validated ecological state for an area over a date range: vegetation greenness " +
          "and moisture, burn ratio, climate, soil moisture, terrain, and the trend in " +
          "each. Every value carries confidence, validation status, a provenance chain and " +
          "a method citation.",
        parameters: {
          type: "object",
          properties: { geometry: GEOMETRY_PROPERTY, date_range: DATE_RANGE_PROPERTY },
          required: ["geometry", "date_range"],
        },
      },
    },
    call: jsonPost("/v1/ecological-state"),
  },
  {
    definition: {
      type: "function",
      function: {
        name: "get_wildfire_substrate_score",
        description:
          "Composite wildfire substrate condition, 0-100, with its full decomposition into " +
          "contributing indicators, weights and points. Condition of the ground, not " +
          "ignition probability.",
        parameters: {
          type: "object",
          properties: {
            geometry: GEOMETRY_PROPERTY,
            date: { type: "string", description: "YYYY-MM-DD" },
          },
          required: ["geometry", "date"],
        },
      },
    },
    call: jsonPost("/v1/wildfire-substrate-score"),
  },
  {
    definition: {
      type: "function",
      function: {
        name: "compare_periods",
        description:
          "Change between two periods, with statistical significance tested on the monthly " +
          "series. A difference that is not significant is reported as not significant " +
          "rather than as change.",
        parameters: {
          type: "object",
          properties: {
            geometry: GEOMETRY_PROPERTY,
            period_a: DATE_RANGE_PROPERTY,
            period_b: DATE_RANGE_PROPERTY,
          },
          required: ["geometry", "period_a", "period_b"],
        },
      },
    },
    call: jsonPost("/v1/compare-periods"),
  },
  {
    definition: {
      type: "function",
      function: {
        name: "get_provenance",
        description:
          "Trace a previously returned number back to the satellite scenes, reanalysis " +
          "cells or elevation tiles behind it. Takes a claim_id from any served value.",
        parameters: {
          type: "object",
          properties: { claim_id: { type: "string" } },
          required: ["claim_id"],
        },
      },
    },
    call: (input) =>
      fetch(`${API_BASE}/v1/provenance/${encodeURIComponent(String(input["claim_id"]))}`, {
        cache: "no-store",
      }),
  },
];

export interface TranscriptEntry {
  type: "tool_call" | "tool_result" | "text";
  name?: string;
  input?: unknown;
  content: unknown;
}

export async function POST(request: Request): Promise<Response> {
  const apiKey = process.env["GROQ_API_KEY"];
  if (apiKey === undefined || apiKey === "") {
    return Response.json(
      {
        error: "no_api_key",
        message:
          "GROQ_API_KEY is not set, so the playground cannot reach a model. The map and " +
          "the report do not need it — they read the layer directly.",
      },
      { status: 503 },
    );
  }

  const body = (await request.json()) as { question?: string };
  const question = (body.question ?? "").trim();
  if (question === "") {
    return Response.json({ error: "empty_question", message: "Ask something." }, { status: 400 });
  }

  const client = new OpenAI({ apiKey, baseURL: BASE_URL });

  const briefing = await coverageBriefing();
  const messages: OpenAI.Chat.Completions.ChatCompletionMessageParam[] = [
    { role: "system", content: SYSTEM },
    ...(briefing === null
      ? []
      : [{ role: "system" as const, content: briefing }]),
    { role: "user", content: question },
  ];
  const transcript: TranscriptEntry[] = [];

  try {
    // Bounded so a confused loop cannot spin on a page anyone can hit.
    for (let turn = 0; turn < 8; turn += 1) {
      const completion = await client.chat.completions.create({
        model: MODEL,
        messages,
        tools: TOOLS.map((t) => t.definition),
        max_tokens: 2048,
        temperature: 0.2,
      });

      const choice = completion.choices[0];
      if (choice === undefined) break;
      const message = choice.message;
      messages.push(message);

      // Groq omits `content` entirely on a tool-call turn where the OpenAI API sends
      // null, so this checks the type rather than comparing against null.
      if (typeof message.content === "string" && message.content.trim() !== "") {
        transcript.push({ type: "text", content: message.content });
      }

      const calls = message.tool_calls ?? [];
      if (calls.length === 0) break;

      for (const call of calls) {
        if (call.type !== "function") continue;
        const spec = TOOLS.find((t) => t.definition.function.name === call.function.name);

        let parsed: Record<string, unknown> = {};
        try {
          parsed = JSON.parse(call.function.arguments || "{}") as Record<string, unknown>;
        } catch {
          // A model that emits malformed arguments should be told so and allowed to retry,
          // rather than having the whole turn fail.
          transcript.push({
            type: "tool_call",
            name: call.function.name,
            content: call.function.arguments,
          });
          messages.push({
            role: "tool",
            tool_call_id: call.id,
            content: "Arguments were not valid JSON. Send the arguments again as JSON.",
          });
          continue;
        }

        transcript.push({ type: "tool_call", name: call.function.name, content: parsed });

        if (spec === undefined) {
          messages.push({
            role: "tool",
            tool_call_id: call.id,
            content: `No such tool: ${call.function.name}`,
          });
          continue;
        }

        try {
          const response = await spec.call(parsed);
          const payload: unknown = await response.json();

          // The visitor sees the raw response; the model sees a summary of it. Full
          // provenance chains run to tens of thousands of tokens, which no context window
          // should be spending on an enumeration the model can fetch on demand.
          transcript.push({ type: "tool_result", name: call.function.name, content: payload });
          messages.push({
            role: "tool",
            tool_call_id: call.id,
            content: JSON.stringify(compactForModel(call.function.name, payload)),
          });
        } catch (error) {
          const detail = error instanceof Error ? error.message : String(error);
          transcript.push({
            type: "tool_result",
            name: call.function.name,
            content: { error: detail },
          });
          messages.push({ role: "tool", tool_call_id: call.id, content: `Tool failed: ${detail}` });
        }
      }
    }
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    return Response.json(
      {
        error: "model_unavailable",
        message: "The model could not be reached.",
        detail,
        transcript,
      },
      { status: 502 },
    );
  }

  const answer = transcript
    .filter((e) => e.type === "text")
    .map((e) => String(e.content))
    .join("\n\n");

  return Response.json({ answer, transcript, model: MODEL });
}
