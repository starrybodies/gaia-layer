/**
 * Display primitives.
 *
 * The load-bearing rule: a number is never shown without the things that qualify it —
 * confidence, validation status, and a route to its provenance. That is the whitepaper's
 * second lesson expressed in the interface rather than only in the schema, and it is why
 * `Reading` has no variant that renders a bare value.
 *
 * Colour carries meaning and nothing else. Green is validated, amber is flagged, rust is
 * rejected. Chrome is greyscale.
 */
import type { ReactNode } from "react";
import type { Method, NumericEnvelope, ProvenanceStep, ValidationFlag } from "@/lib/api";

export const INDICATOR_LABELS: Record<string, string> = {
  ndvi: "Vegetation greenness",
  ndmi: "Canopy moisture",
  nbr: "Burn ratio",
  vpd_kpa: "Vapour pressure deficit",
  precip_30d_mm: "Precipitation, monthly",
  temp_max_c: "Maximum temperature",
  days_since_rain: "Days since rain",
  soil_moisture_0_7cm: "Soil moisture, 0–7 cm",
  soil_moisture_7_28cm: "Soil moisture, 7–28 cm",
  elevation_m: "Elevation",
  slope_deg: "Slope",
  aspect_deg: "Aspect",
  twi: "Topographic wetness",
};

export const INDICATOR_CODES: Record<string, string> = {
  ndvi: "NDVI",
  ndmi: "NDMI",
  nbr: "NBR",
  vpd_kpa: "VPD",
  precip_30d_mm: "PRCP",
  temp_max_c: "TMAX",
  days_since_rain: "DSR",
  soil_moisture_0_7cm: "SM07",
  soil_moisture_7_28cm: "SM728",
  elevation_m: "ELEV",
  slope_deg: "SLOPE",
  aspect_deg: "ASP",
  twi: "TWI",
};

export function label(indicator: string): string {
  return INDICATOR_LABELS[indicator] ?? indicator;
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return <p className="eyebrow text-muted">{children}</p>;
}

export function Panel({
  title,
  right,
  children,
  className = "",
}: {
  title?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`border-line bg-surface border ${className}`}>
      {title !== undefined && (
        <header className="border-line flex items-baseline justify-between gap-3 border-b px-4 py-2.5">
          <h2 className="eyebrow text-dim">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const STATUS_STYLE: Record<string, { dot: string; text: string }> = {
  validated: { dot: "bg-signal", text: "text-signal" },
  flagged: { dot: "bg-amber", text: "text-amber" },
  rejected: { dot: "bg-rust", text: "text-rust" },
};

export function StatusDot({ status }: { status: string }) {
  const style = STATUS_STYLE[status] ?? STATUS_STYLE["validated"]!;
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${style.dot}`} />
      <span className={`numeric text-[10px] tracking-[0.14em] uppercase ${style.text}`}>
        {status}
      </span>
    </span>
  );
}

/**
 * Confidence as a bar and a number, always both.
 *
 * The bar alone is an impression; the number alone is easy to skim past. The confidence is
 * as much the measurement as the value is.
 */
export function ConfidenceBar({ value, wide = false }: { value: number; wide?: boolean }) {
  const pct = Math.max(2, Math.round(value * 100));
  const tone = value >= 0.75 ? "bg-signal" : value >= 0.45 ? "bg-amber" : "bg-rust";
  return (
    <span
      className="inline-flex items-center gap-2"
      title={`Confidence ${value.toFixed(3)} of 1.000`}
    >
      <span className={`bg-raised relative block h-1 ${wide ? "w-24" : "w-14"}`}>
        <span className={`absolute inset-y-0 left-0 ${tone}`} style={{ width: `${pct}%` }} />
      </span>
      <span className="numeric text-dim text-[11px]">{value.toFixed(2)}</span>
    </span>
  );
}

export function FlagList({ flags }: { flags: ValidationFlag[] }) {
  if (flags.length === 0) return null;
  return (
    <ul className="border-amber/60 mt-3 space-y-2 border-l-2 pl-3">
      {flags.map((flag, i) => (
        <li key={`${flag.code}-${i}`} className="text-[11px] leading-relaxed">
          <span className="numeric text-amber text-[10px] tracking-wider uppercase">
            {flag.code}
          </span>
          <span className="text-dim ml-2">{flag.message}</span>
        </li>
      ))}
    </ul>
  );
}

export function formatValue(value: number, unit: string): string {
  if (unit === "index" || unit === "m3/m3") return value.toFixed(3);
  if (Math.abs(value) >= 100) return value.toFixed(0);
  return value.toFixed(2);
}

export function unitSuffix(unit: string): string {
  if (unit === "index") return "";
  if (unit === "degC") return "°C";
  if (unit === "m3/m3") return "m³/m³";
  if (unit === "degrees") return "°";
  return unit;
}

/** A single measurement, with everything needed to judge it and cite it. */
export function Reading({
  envelope,
  onCite,
}: {
  envelope: NumericEnvelope;
  onCite?: (claimId: string) => void;
}) {
  return (
    <div className="border-line/70 border-b py-3.5 last:border-b-0">
      <div className="flex items-baseline justify-between gap-4">
        <div className="min-w-0">
          <span className="numeric text-faint text-[10px] tracking-[0.18em] uppercase">
            {INDICATOR_CODES[envelope.indicator] ?? envelope.indicator}
          </span>
          <p className="text-text truncate text-sm">{label(envelope.indicator)}</p>
        </div>
        <div className="shrink-0 text-right">
          <span className="numeric text-text text-xl">
            {formatValue(envelope.value, envelope.unit)}
          </span>
          <span className="numeric text-muted ml-1 text-[11px]">
            {unitSuffix(envelope.unit)}
          </span>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1.5">
        <StatusDot status={envelope.validation_status} />
        <ConfidenceBar value={envelope.confidence} />
        {envelope.spatial_stats !== null && (
          <span className="numeric text-faint text-[10px]">
            p10 {envelope.spatial_stats.p10.toFixed(2)} · p90{" "}
            {envelope.spatial_stats.p90.toFixed(2)} ·{" "}
            {(
              (envelope.spatial_stats.valid_pixels / envelope.spatial_stats.total_pixels) *
              100
            ).toFixed(0)}
            % observed
          </span>
        )}
        {onCite !== undefined ? (
          <button
            type="button"
            onClick={() => onCite(envelope.claim_id)}
            className="numeric text-signal-dim hover:text-signal text-[10px] underline decoration-dotted underline-offset-4 transition-colors"
          >
            {envelope.claim_id}
          </button>
        ) : (
          <span className="numeric text-faint text-[10px] break-all">{envelope.claim_id}</span>
        )}
      </div>

      <FlagList flags={envelope.flags} />
    </div>
  );
}

const STEP_TONE: Record<string, string> = {
  observation: "bg-signal",
  processing: "bg-muted",
  validation: "bg-cyan",
};

/** The provenance chain, read top to bottom as a sequence of events. */
export function ProvenanceChain({ steps }: { steps: ProvenanceStep[] }) {
  return (
    <ol className="space-y-3.5">
      {steps.map((step) => (
        <li key={step.index} className="border-line relative border-l pl-4">
          <span
            className={`absolute top-1.5 -left-[3.5px] h-1.5 w-1.5 rounded-full ${
              STEP_TONE[step.kind] ?? "bg-muted"
            }`}
          />
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="numeric text-faint text-[10px] tracking-[0.16em] uppercase">
              {step.index.toString().padStart(2, "0")} {step.kind}
            </span>
            {step.acquired_at != null && (
              <span className="numeric text-muted text-[10px]">
                {step.acquired_at.slice(0, 10)}
              </span>
            )}
          </div>
          <p className="text-dim mt-1 text-xs leading-relaxed">{step.description}</p>
          <p className="numeric text-faint mt-1 text-[10px]">
            {[
              step.source,
              step.dataset_id,
              step.access_route,
              step.spatial_ref,
              step.resolution_m != null ? `${step.resolution_m} m` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {step.asset_ids.length > 0 && (
            <details className="mt-1.5">
              <summary className="numeric text-faint hover:text-muted cursor-pointer text-[10px]">
                {step.asset_ids.length} source asset{step.asset_ids.length === 1 ? "" : "s"}
              </summary>
              <ul className="numeric text-faint mt-1.5 space-y-0.5 text-[10px] break-all">
                {step.asset_ids.map((asset, i) => (
                  <li key={`${asset}-${i}`}>{asset}</li>
                ))}
              </ul>
            </details>
          )}
        </li>
      ))}
    </ol>
  );
}

export function Citation({ method }: { method: Method }) {
  return (
    <div className="border-line border-t pt-3">
      <p className="text-dim text-xs">{method.name}</p>
      {method.formula != null && (
        <p className="numeric text-signal-dim mt-1 text-[11px]">{method.formula}</p>
      )}
      <p className="text-muted mt-1.5 text-[11px] leading-relaxed">{method.citation}</p>
      {method.notes != null && (
        <p className="text-faint mt-1.5 text-[11px] leading-relaxed italic">{method.notes}</p>
      )}
    </div>
  );
}

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="border-line border border-dashed p-10 text-center">
      <p className="text-dim text-sm">{title}</p>
      {detail !== undefined && (
        <p className="text-faint mx-auto mt-2 max-w-lg text-xs leading-relaxed">{detail}</p>
      )}
    </div>
  );
}

export function Stat({
  value,
  label: statLabel,
  hint,
  tone = "text",
}: {
  value: string;
  label: string;
  hint?: string;
  tone?: "text" | "signal";
}) {
  return (
    <div className="border-line border-t pt-3">
      <p
        className={`numeric text-2xl md:text-3xl ${tone === "signal" ? "text-signal" : "text-text"}`}
      >
        {value}
      </p>
      <p className="eyebrow text-muted mt-1.5">{statLabel}</p>
      {hint !== undefined && (
        <p className="text-faint mt-1 text-[11px] leading-relaxed">{hint}</p>
      )}
    </div>
  );
}
