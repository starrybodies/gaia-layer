"use client";

/**
 * The agent's answer, rendered.
 *
 * The model replies in markdown — tables of indicators with claim ids and p-values are its
 * natural shape for a comparison — so rendering it as plain text left asterisks and pipe
 * characters on screen. This is presentation only: it changes how the answer looks and
 * nothing about what it says, and the raw text stays available in the transcript below it.
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function Answer({ markdown }: { markdown: string }) {
  return (
    <div className="text-dim text-sm leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
          strong: ({ children }) => <strong className="text-text font-semibold">{children}</strong>,
          em: ({ children }) => <em className="text-muted">{children}</em>,
          ul: ({ children }) => <ul className="mb-3 space-y-1.5 last:mb-0">{children}</ul>,
          ol: ({ children }) => (
            <ol className="mb-3 list-inside list-decimal space-y-1.5 last:mb-0">{children}</ol>
          ),
          li: ({ children }) => (
            <li className="marker:text-signal-dim pl-1 [&>p]:mb-0 [&>p]:inline">{children}</li>
          ),
          h1: ({ children }) => (
            <h3 className="display text-text mt-5 mb-2 text-sm first:mt-0">{children}</h3>
          ),
          h2: ({ children }) => (
            <h3 className="display text-text mt-5 mb-2 text-sm first:mt-0">{children}</h3>
          ),
          h3: ({ children }) => (
            <h3 className="display text-text mt-5 mb-2 text-sm first:mt-0">{children}</h3>
          ),
          // Claim ids and figures arrive as inline code. Monospace and the signal colour
          // mark them as things you can carry to get_provenance.
          code: ({ children }) => (
            <code className="numeric text-signal-dim bg-void border-line border px-1 py-0.5 text-[11px] break-all">
              {children}
            </code>
          ),
          a: ({ children, href }) => (
            <a
              href={href}
              className="text-signal underline decoration-dotted underline-offset-4"
              target="_blank"
              rel="noreferrer"
            >
              {children}
            </a>
          ),
          hr: () => <hr className="border-line my-4" />,
          blockquote: ({ children }) => (
            <blockquote className="border-line text-muted my-3 border-l-2 pl-3">
              {children}
            </blockquote>
          ),
          table: ({ children }) => (
            <div className="border-line my-4 overflow-x-auto border">
              <table className="w-full border-collapse text-left">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-raised">{children}</thead>,
          th: ({ children }) => (
            <th className="numeric text-muted border-line border-b px-3 py-2 text-[10px] tracking-wider whitespace-nowrap uppercase">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border-line/60 text-dim border-b px-3 py-2 align-top text-[12px]">
              {children}
            </td>
          ),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
