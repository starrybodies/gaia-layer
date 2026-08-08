import Link from "next/link";
import type { ReactNode } from "react";

const NAV = [
  { href: "/", label: "Map" },
  { href: "/report", label: "Substrate report" },
  { href: "/playground", label: "Agent playground" },
];

export function Shell({ children, active }: { children: ReactNode; active: string }) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex shrink-0 items-center justify-between border-b border-base-800 bg-base-900 px-5 py-2.5">
        <div className="flex items-baseline gap-6">
          <Link href="/" className="numeric text-xs tracking-[0.22em] text-base-100 uppercase">
            Gaia
          </Link>
          <span className="numeric hidden text-[10px] tracking-[0.18em] text-base-500 uppercase sm:inline">
            Ecological Intelligence Layer · v0.1
          </span>
        </div>
        <nav className="flex gap-1">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`numeric px-3 py-1 text-[11px] tracking-wider uppercase transition-colors ${
                active === item.href
                  ? "bg-base-800 text-base-100"
                  : "text-base-400 hover:text-base-200"
              }`}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </header>

      <main className="flex-1">{children}</main>

      <footer className="shrink-0 border-t border-base-800 px-5 py-2">
        <p className="numeric text-[10px] text-base-600">
          Every value served carries a confidence score, a validation status and a provenance
          chain to source observations. No number on this page was produced by a language
          model.
        </p>
      </footer>
    </div>
  );
}
