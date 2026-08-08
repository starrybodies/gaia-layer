"use client";

import { useState } from "react";
import { Shell } from "@/components/Shell";
import { Panel } from "@/components/primitives";
import type { TranscriptEntry } from "@/app/api/chat/route";

const SUGGESTIONS = [
  "What is the vegetation dryness trend for the pilot area over the last six months, and how confident are you in it?",
  "What is the wildfire substrate score, and which indicator is driving it?",
  "Compare the last three months against the same period earlier in the record. Is the difference significant?",
  "Where did the canopy moisture number come from? Trace it to the satellite scenes.",
];

export default function PlaygroundPage() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function ask(text: string): Promise<void> {
    if (text.trim() === "" || busy) return;
    setBusy(true);
    setError(null);
    setAnswer(null);
    setTranscript([]);
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: text }),
      });
      const body = (await response.json()) as {
        answer?: string;
        transcript?: TranscriptEntry[];
        message?: string;
      };
      if (!response.ok) {
        setError(body.message ?? "The playground could not reach the model.");
        return;
      }
      setAnswer(body.answer ?? "");
      setTranscript(body.transcript ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell active="/playground">
      <div className="mx-auto max-w-5xl px-6 py-10">
        <header>
          <p className="numeric text-[10px] tracking-[0.22em] text-base-500 uppercase">
            Agent playground
          </p>
          <h1 className="mt-2 text-2xl text-base-100">Watch an agent query the layer</h1>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-base-400">
            The model below has the same five tools an agent gets over MCP, bound to the same
            REST API. It orchestrates and explains; it never computes an ecological value.
            Every tool call and its raw response is shown, so you can check the prose against
            what the layer actually returned.
          </p>
        </header>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void ask(question);
          }}
          className="mt-8"
        >
          <div className="flex gap-2">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask about the pilot area…"
              className="flex-1 border border-base-800 bg-base-900 px-3 py-2.5 text-sm text-base-100 outline-none placeholder:text-base-600 focus:border-base-600"
            />
            <button
              type="submit"
              disabled={busy}
              className="numeric border border-accent-600 bg-accent-500 px-5 text-[11px] tracking-widest text-base-950 uppercase transition-opacity disabled:opacity-40"
            >
              {busy ? "…" : "Ask"}
            </button>
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => {
                  setQuestion(s);
                  void ask(s);
                }}
                disabled={busy}
                className="max-w-full truncate border border-base-800 px-2.5 py-1 text-left text-[11px] text-base-400 transition-colors hover:border-base-600 hover:text-base-200 disabled:opacity-40"
              >
                {s}
              </button>
            ))}
          </div>
        </form>

        {error !== null && (
          <div className="mt-6 border border-status-rejected/50 p-4">
            <p className="numeric text-[10px] tracking-wider text-status-rejected uppercase">
              playground unavailable
            </p>
            <p className="mt-1 text-xs leading-relaxed text-base-300">{error}</p>
          </div>
        )}

        {answer !== null && answer !== "" && (
          <div className="mt-8">
            <Panel title="Answer">
              <div className="space-y-3 text-sm leading-relaxed whitespace-pre-wrap text-base-200">
                {answer}
              </div>
            </Panel>
          </div>
        )}

        {transcript.length > 0 && (
          <div className="mt-px">
            <Panel title="Transcript — every call and its raw result">
              <ol className="space-y-3">
                {transcript.map((entry, i) => (
                  <li key={i} className="border-l border-base-700 pl-3">
                    <p className="numeric text-[10px] tracking-widest text-base-500 uppercase">
                      {entry.type === "tool_call"
                        ? `call · ${entry.name}`
                        : entry.type === "tool_result"
                          ? `result · ${entry.name}`
                          : "model"}
                    </p>
                    {entry.type === "text" ? (
                      <p className="mt-1 text-xs leading-relaxed whitespace-pre-wrap text-base-300">
                        {String(entry.content)}
                      </p>
                    ) : (
                      <details className="mt-1">
                        <summary className="numeric cursor-pointer text-[10px] text-base-600 hover:text-base-400">
                          {entry.type === "tool_call" ? "arguments" : "raw response"}
                        </summary>
                        <pre className="numeric mt-1 max-h-72 overflow-auto bg-base-950 p-2 text-[10px] leading-relaxed text-base-400">
                          {JSON.stringify(entry.content, null, 2)}
                        </pre>
                      </details>
                    )}
                  </li>
                ))}
              </ol>
            </Panel>
          </div>
        )}
      </div>
    </Shell>
  );
}
