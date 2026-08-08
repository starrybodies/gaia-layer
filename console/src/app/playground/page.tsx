"use client";

import { useState } from "react";
import { Shell } from "@/components/Shell";
import { Answer } from "@/components/Answer";
import { Eyebrow } from "@/components/primitives";
import type { TranscriptEntry } from "@/app/api/chat/route";

const SUGGESTIONS = [
  {
    short: "Substrate score",
    full: "What is the wildfire substrate score, and which indicator is driving it?",
  },
  {
    short: "Dryness trend",
    full: "What is the vegetation dryness trend over the last six months, and how confident are you in it?",
  },
  {
    short: "Seasonal change",
    full: "Compare winter against summer. Which changes are statistically significant?",
  },
  {
    short: "Trace a number",
    full: "Where did the canopy moisture number come from? Trace it to the satellite scenes.",
  },
];

function ToolBadge({ name }: { name: string }) {
  return (
    <span className="numeric border-line-bright text-signal-dim border px-2 py-0.5 text-[10px]">
      {name}
    </span>
  );
}

export default function PlaygroundPage() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [model, setModel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState<number | null>(null);

  async function ask(text: string): Promise<void> {
    if (text.trim() === "" || busy) return;
    setBusy(true);
    setError(null);
    setAnswer(null);
    setTranscript([]);
    setElapsed(null);
    const started = Date.now();
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: text }),
      });
      const body = (await response.json()) as {
        answer?: string;
        transcript?: TranscriptEntry[];
        model?: string;
        message?: string;
        detail?: string;
      };
      if (!response.ok) {
        setError(
          [body.message, body.detail].filter(Boolean).join(" ") ||
            "The playground could not reach the model.",
        );
        setTranscript(body.transcript ?? []);
        return;
      }
      setAnswer(body.answer ?? "");
      setTranscript(body.transcript ?? []);
      setModel(body.model ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setElapsed(Date.now() - started);
      setBusy(false);
    }
  }

  const calls = transcript.filter((e) => e.type === "tool_call");

  return (
    <Shell active="/playground">
      <div className="mx-auto max-w-6xl px-5 py-14 md:py-20">
        <header className="max-w-3xl">
          <Eyebrow>Agent playground</Eyebrow>
          <h1 className="display text-text mt-4 text-3xl md:text-4xl">
            Watch an agent
            <br />
            <span className="text-signal glow-signal">query the layer</span>
          </h1>
          <p className="text-dim mt-6 text-sm leading-relaxed">
            The model below has the same five tools an agent gets over MCP, bound to the same
            REST API this site runs on. It orchestrates and explains. It never computes an
            ecological value.
          </p>
          <p className="text-muted mt-3 text-sm leading-relaxed">
            Every tool call and its raw response is shown underneath the answer, so you can
            check the prose against what the layer actually returned rather than taking the
            model&rsquo;s word for it.
          </p>
        </header>

        {/* ─────────────────────────────────────────────────────────── ask */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void ask(question);
          }}
          className="mt-10"
        >
          <div className="border-line bg-surface focus-within:border-signal flex items-stretch border transition-colors">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask about the pilot area…"
              disabled={busy}
              className="text-text placeholder:text-faint flex-1 bg-transparent px-4 py-3.5 text-sm outline-none disabled:opacity-60"
            />
            <button
              type="submit"
              disabled={busy || question.trim() === ""}
              className="numeric bg-signal text-void hover:bg-signal-dim shrink-0 px-6 text-[11px] tracking-[0.16em] uppercase transition-colors disabled:opacity-30"
            >
              {busy ? "Working" : "Ask"}
            </button>
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s.short}
                type="button"
                onClick={() => {
                  setQuestion(s.full);
                  void ask(s.full);
                }}
                disabled={busy}
                title={s.full}
                className="numeric border-line text-muted hover:border-signal hover:text-signal border px-3 py-1.5 text-[10px] tracking-wider uppercase transition-colors disabled:opacity-30"
              >
                {s.short}
              </button>
            ))}
          </div>
        </form>

        {/* ──────────────────────────────────────────────────────── status */}
        {busy && (
          <div className="border-line bg-surface mt-8 border p-5">
            <div className="flex items-center gap-2.5">
              <span className="bg-signal pulse inline-block h-1.5 w-1.5 rounded-full" />
              <span className="numeric text-dim text-[11px]">
                Querying the layer. Tool calls and their raw results will appear below.
              </span>
            </div>
          </div>
        )}

        {error !== null && (
          <div className="border-rust/50 bg-surface mt-8 border p-5">
            <p className="numeric text-rust text-[10px] tracking-wider uppercase">
              Playground unavailable
            </p>
            <p className="text-dim mt-2 text-xs leading-relaxed">{error}</p>
            <p className="text-faint mt-3 text-[11px] leading-relaxed">
              The map and the report do not need a model — they read the layer directly.
            </p>
          </div>
        )}

        {/* ──────────────────────────────────────────────────────── answer */}
        {answer !== null && answer !== "" && (
          <article className="border-line bg-surface mt-8 border">
            <header className="border-line flex flex-wrap items-center justify-between gap-3 border-b px-5 py-3">
              <Eyebrow>Answer</Eyebrow>
              <div className="flex flex-wrap items-center gap-2">
                {calls.map((c, i) => (
                  <ToolBadge key={`${c.name}-${i}`} name={String(c.name)} />
                ))}
                {elapsed !== null && (
                  <span className="numeric text-faint text-[10px]">
                    {(elapsed / 1000).toFixed(1)}s
                  </span>
                )}
              </div>
            </header>
            <div className="px-5 py-5">
              <Answer markdown={answer} />
            </div>
            {model !== null && (
              <footer className="border-line border-t px-5 py-2.5">
                <p className="numeric text-faint text-[10px]">
                  {model} · every figure above came from a tool call, not from the model
                </p>
              </footer>
            )}
          </article>
        )}

        {/* ───────────────────────────────────────────────────── transcript */}
        {transcript.length > 0 && (
          <section className="border-line bg-surface mt-px border">
            <header className="border-line border-b px-5 py-3">
              <Eyebrow>Transcript — every call and its raw result</Eyebrow>
            </header>
            <ol className="divide-line divide-y">
              {transcript.map((entry, i) => (
                <li key={i} className="px-5 py-3.5">
                  <div className="flex flex-wrap items-baseline gap-2.5">
                    <span
                      className={`numeric text-[10px] tracking-[0.16em] uppercase ${
                        entry.type === "tool_call"
                          ? "text-signal"
                          : entry.type === "tool_result"
                            ? "text-cyan"
                            : "text-muted"
                      }`}
                    >
                      {entry.type === "tool_call"
                        ? "call"
                        : entry.type === "tool_result"
                          ? "result"
                          : "model"}
                    </span>
                    {entry.name !== undefined && (
                      <span className="numeric text-dim text-[11px]">{entry.name}</span>
                    )}
                  </div>

                  {entry.type === "text" ? (
                    <p className="text-muted mt-2 text-xs leading-relaxed whitespace-pre-wrap">
                      {String(entry.content)}
                    </p>
                  ) : (
                    <details className="mt-2">
                      <summary className="numeric text-faint hover:text-muted cursor-pointer text-[10px]">
                        {entry.type === "tool_call" ? "arguments" : "raw response"}
                      </summary>
                      <pre className="numeric bg-void border-line text-muted mt-2 max-h-80 overflow-auto border p-3 text-[10px] leading-relaxed">
                        {JSON.stringify(entry.content, null, 2)}
                      </pre>
                    </details>
                  )}
                </li>
              ))}
            </ol>
          </section>
        )}
      </div>
    </Shell>
  );
}
