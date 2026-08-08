import Link from "next/link";
import type { ReactNode } from "react";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/map", label: "Map" },
  { href: "/report", label: "Report" },
  { href: "/playground", label: "Agent" },
];

export function Mark({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <circle cx="16" cy="16" r="14" fill="none" stroke="currentColor" strokeWidth="1.25" />
      {/* Two rising strokes meeting a horizon: the substrate and what stands on it. */}
      <path
        d="M7 22 L13 11 L19 22"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path d="M17 22 L22 13 L26 20" fill="none" stroke="currentColor" strokeWidth="1.25" />
      <path d="M5 25.5 H27" stroke="currentColor" strokeWidth="1.25" opacity="0.55" />
    </svg>
  );
}

export function TopBar({ active }: { active: string }) {
  return (
    <header className="border-line bg-void/85 sticky top-0 z-50 border-b backdrop-blur-md">
      <div className="mx-auto flex max-w-[100rem] items-center justify-between px-5 py-3">
        <Link href="/" className="group flex items-center gap-2.5">
          <Mark className="text-signal h-6 w-6" />
          <span className="display text-text text-sm tracking-[0.2em]">Gaia</span>
          <span className="eyebrow text-faint hidden sm:inline">Ecological Intelligence</span>
        </Link>

        <nav className="flex items-center gap-0.5">
          {NAV.map((item) => {
            const on = active === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`eyebrow px-3 py-2 transition-colors ${
                  on ? "text-signal" : "text-muted hover:text-text"
                }`}
              >
                {item.label}
                {on && <span className="bg-signal mt-1.5 block h-px w-full" />}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="border-line mt-px border-t">
      <div className="mx-auto max-w-[100rem] px-5 py-5">
        <p className="text-faint max-w-4xl text-[11px] leading-relaxed">
          Every value served carries a confidence score, a validation status, and a provenance
          chain back to source observations. No number anywhere on this site was produced by a
          language model.
        </p>
        <p className="numeric text-faint mt-2 text-[10px]">
          v0.1 · pilot area: Southern Gulf Islands and Coastal Douglas-fir, British Columbia
        </p>
      </div>
    </footer>
  );
}

export function Shell({
  children,
  active,
  flush = false,
}: {
  children: ReactNode;
  active: string;
  flush?: boolean;
}) {
  return (
    <div className="flex min-h-screen flex-col">
      <TopBar active={active} />
      <main className="flex-1">{children}</main>
      {!flush && <Footer />}
    </div>
  );
}
