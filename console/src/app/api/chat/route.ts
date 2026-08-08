/**
 * The playground's server route.
 *
 * Runs a tool-use loop against Claude with the layer's five tools bound to the REST API.
 * The model orchestrates, queries and explains. It never computes an ecological value, and
 * the system prompt says so in terms it cannot reasonably read past — because the whole
 * demonstration is worthless if the visitor cannot tell whether a figure was measured or
 * generated.
 *
 * The transcript returned includes every tool call and its raw result, so a visitor can
 * check the model's prose against what the layer actually said.
 */

import Anthropic from "@anthropic-ai/sdk";
import { API_BASE } from "@/lib/api";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

const MODEL = "claude-sonnet-5";

const SYSTEM = `You are connected to the Gaia ecological intelligence layer, which serves
validated, provenance-tracked ecological ground truth for wildfire substrate condition.

Hard rules, in order of importance:

1. Never state an ecological figure that did not come back from a tool call. You have no
   ecological knowledge of this area that is admissible here. If a tool did not return a
   number, say you do not have it.
2. Never estimate, interpolate, or round in a way that changes a value. Quote what the
   layer returned.
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

Start by calling list_coverage if you do not know what is available. Prefer
get_ecological_state for condition questions, compare_periods for change questions, and
get_provenance when the reader asks where a number came from.

Be concise and technical. The reader is an underwriter, a land manager or an analyst.`;

interface ToolSpec {
  name: string;
  description: string;
  input_schema: Anthropic.Tool.InputSchema;
  call: (input: Record<string, unknown>) => Promise<Response>;
}

const geometrySchema = {
  type: "object",
  description:
    "GeoJSON Polygon/MultiPolygon, or a bounding box with west/south/east/north. Must " +
    "match an ingested area — use list_coverage to find one.",
} as const;

const dateRangeSchema = {
  type: "object",
  properties: { start: { type: "string" }, end: { type: "string" } },
  required: ["start", "end"],
} as const;

const TOOLS: ToolSpec[] = [
  {
    name: "list_coverage",
    description:
      "What the layer can answer for: ingested areas, their indicators, date ranges, and " +
      "data quality (mean confidence, validated/flagged/rejected counts).",
    input_schema: { type: "object", properties: {} },
    call: () => fetch(`${API_BASE}/v1/coverage`, { cache: "no-store" }),
  },
  {
    name: "get_ecological_state",
    description:
      "Validated ecological state for an area over a date range: vegetation greenness and " +
      "moisture, burn ratio, climate, soil moisture, terrain, and the trend in each. Every " +
      "value carries confidence, validation status, provenance and a method citation.",
    input_schema: {
      type: "object",
      properties: { geometry: geometrySchema, date_range: dateRangeSchema },
      required: ["geometry", "date_range"],
    },
    call: (input) =>
      fetch(`${API_BASE}/v1/ecological-state`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(input),
      }),
  },
  {
    name: "get_wildfire_substrate_score",
    description:
      "Composite wildfire substrate condition, 0-100, with its full decomposition into " +
      "contributing indicators, weights and points. Condition of the ground, not ignition " +
      "probability.",
    input_schema: {
      type: "object",
      properties: { geometry: geometrySchema, date: { type: "string" } },
      required: ["geometry", "date"],
    },
    call: (input) =>
      fetch(`${API_BASE}/v1/wildfire-substrate-score`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(input),
      }),
  },
  {
    name: "compare_periods",
    description:
      "Change between two periods, with statistical significance. A difference that is not " +
      "significant is reported as not significant rather than as change.",
    input_schema: {
      type: "object",
      properties: {
        geometry: geometrySchema,
        period_a: dateRangeSchema,
        period_b: dateRangeSchema,
      },
      required: ["geometry", "period_a", "period_b"],
    },
    call: (input) =>
      fetch(`${API_BASE}/v1/compare-periods`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(input),
      }),
  },
  {
    name: "get_provenance",
    description:
      "Trace a previously returned number back to the satellite scenes, reanalysis cells " +
      "or elevation tiles behind it. Takes a claim_id.",
    input_schema: {
      type: "object",
      properties: { claim_id: { type: "string" } },
      required: ["claim_id"],
    },
    call: (input) =>
      fetch(`${API_BASE}/v1/provenance/${String(input["claim_id"])}`, { cache: "no-store" }),
  },
];

export interface TranscriptEntry {
  type: "tool_call" | "tool_result" | "text";
  name?: string;
  input?: unknown;
  content: unknown;
}

export async function POST(request: Request): Promise<Response> {
  const apiKey = process.env["ANTHROPIC_API_KEY"];
  if (apiKey === undefined || apiKey === "") {
    return Response.json(
      {
        error: "no_api_key",
        message:
          "ANTHROPIC_API_KEY is not set, so the playground cannot reach a model. The map " +
          "and the report do not need it — they read the layer directly.",
      },
      { status: 503 },
    );
  }

  const body = (await request.json()) as { question?: string };
  const question = (body.question ?? "").trim();
  if (question === "") {
    return Response.json({ error: "empty_question", message: "Ask something." }, { status: 400 });
  }

  const client = new Anthropic({ apiKey });
  const messages: Anthropic.MessageParam[] = [{ role: "user", content: question }];
  const transcript: TranscriptEntry[] = [];

  // Bounded so a confused loop cannot run up a bill on a public demo page.
  for (let turn = 0; turn < 8; turn += 1) {
    const response = await client.messages.create({
      model: MODEL,
      max_tokens: 2048,
      system: SYSTEM,
      tools: TOOLS.map((t) => ({
        name: t.name,
        description: t.description,
        input_schema: t.input_schema,
      })),
      messages,
    });

    messages.push({ role: "assistant", content: response.content });

    const toolUses = response.content.filter(
      (block): block is Anthropic.ToolUseBlock => block.type === "tool_use",
    );

    for (const block of response.content) {
      if (block.type === "text" && block.text.trim() !== "") {
        transcript.push({ type: "text", content: block.text });
      }
    }

    if (toolUses.length === 0) break;

    const results: Anthropic.ToolResultBlockParam[] = [];
    for (const use of toolUses) {
      const spec = TOOLS.find((t) => t.name === use.name);
      transcript.push({ type: "tool_call", name: use.name, input: use.input, content: use.input });

      if (spec === undefined) {
        results.push({
          type: "tool_result",
          tool_use_id: use.id,
          content: `No such tool: ${use.name}`,
          is_error: true,
        });
        continue;
      }

      try {
        const apiResponse = await spec.call(use.input as Record<string, unknown>);
        const payload: unknown = await apiResponse.json();
        transcript.push({ type: "tool_result", name: use.name, content: payload });
        results.push({
          type: "tool_result",
          tool_use_id: use.id,
          content: JSON.stringify(payload),
          is_error: !apiResponse.ok,
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        transcript.push({ type: "tool_result", name: use.name, content: { error: message } });
        results.push({
          type: "tool_result",
          tool_use_id: use.id,
          content: message,
          is_error: true,
        });
      }
    }

    messages.push({ role: "user", content: results });
  }

  const answer = transcript
    .filter((e) => e.type === "text")
    .map((e) => String(e.content))
    .join("\n\n");

  return Response.json({ answer, transcript });
}
