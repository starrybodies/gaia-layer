/**
 * Display primitives.
 *
 * The design brief is Bloomberg terminal meets field notebook: dense, technical, one
 * accent, no eco-kitsch. The load-bearing decision is that a number is never shown without
 * the things that qualify it — confidence, validation status, and a way to reach its
 * provenance. That is the whitepaper's second lesson expressed in the interface rather
 * than only in the schema.
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
    <section className={`border border-base-800 bg-base-900 ${className}`}>
      {title !== undefined && (
        <header className="flex items-baseline justify-between border-b border-base-800 px-4 py-2">
          <h2 className="numeric text-[11px] uppercase tracking-[0.18em] text-base-400">
            {title}
          </h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

/** Validation status. Colour is reserved for meaning, never for decoration. */
export function StatusDot({ status }: { status: string }) {
  const colour =
    status === "validated"
      ? "bg-status-validated"
      : status === "flagged"
        ? "bg-status-flagged"
        : "bg-status-rejected";
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${colour}`} />
      <span className="numeric text-[10px] uppercase tracking-wider text-base-400">{status}</span>
    </span>
  );
}

/**
 * Confidence as a filled bar plus its number.
 *
 * The bar alone would be an impression; the number alone is easy to skim past. Both,
 * always, because the confidence is as much the measurement as the value is.
 */
export function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <span className="inline-flex items-center gap-2" title={`Confidence ${value.toFixed(3)}`}>
      <span className="relative block h-1 w-14 bg-base-800">
        <span
          className="absolute inset-y-0 left-0 bg-accent-500"
          style={{ width: `${Math.max(2, pct)}%` }}
        />
      </span>
      <span className="numeric text-[11px] text-base-400">{value.toFixed(2)}</span>
    </span>
  );
}

export function FlagList({ flags }: { flags: ValidationFlag[] }) {
  if (flags.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1.5 border-l-2 border-status-flagged pl-3">
      {flags.map((flag) => (
        <li key={flag.code} className="text-xs leading-relaxed text-base-300">
          <span className="numeric text-[10px] uppercase tracking-wider text-status-flagged">
            {flag.code}
          </span>
          <span className="ml-2">{flag.message}</span>
        </li>
      ))}
    </ul>
  );
}

function formatValue(value: number, unit: string): string {
  if (unit === "index") return value.toFixed(3);
  if (unit === "m3/m3") return value.toFixed(3);
  if (Math.abs(value) >= 100) return value.toFixed(0);
  return value.toFixed(2);
}

function unitSuffix(unit: string): string {
  if (unit === "index") return "";
  if (unit === "degC") return " °C";
  if (unit === "m3/m3") return " m³/m³";
  if (unit === "degrees") return "°";
  return ` ${unit}`;
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
    <div className="border-b border-base-800 py-3 last:border-b-0">
      <div className="flex items-baseline justify-between gap-4">
        <div className="min-w-0">
          <span className="numeric text-[10px] uppercase tracking-widest text-base-500">
            {INDICATOR_CODES[envelope.indicator] ?? envelope.indicator}
          </span>
          <p className="truncate text-sm text-base-200">{label(envelope.indicator)}</p>
        </div>
        <div className="shrink-0 text-right">
          <p className="numeric text-xl text-base-100">
            {formatValue(envelope.value, envelope.unit)}
            <span className="text-sm text-base-400">{unitSuffix(envelope.unit)}</span>
          </p>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1">
        <StatusDot status={envelope.validation_status} />
        <ConfidenceBar value={envelope.confidence} />
        {envelope.spatial_stats !== null && (
          <span className="numeric text-[11px] text-base-500">
            p10 {envelope.spatial_stats.p10.toFixed(2)} · p90{" "}
            {envelope.spatial_stats.p90.toFixed(2)} ·{" "}
            {(
              (envelope.spatial_stats.valid_pixels / envelope.spatial_stats.total_pixels) *
              100
            ).toFixed(0)}
            % observed
          </span>
        )}
        {onCite !== undefined && (
          <button
            type="button"
            onClick={() => onCite(envelope.claim_id)}
            className="numeric text-[11px] text-accent-500 underline decoration-dotted underline-offset-4 hover:text-accent-400"
          >
            trace {envelope.claim_id.slice(0, 12)}…
          </button>
        )}
      </div>

      <FlagList flags={envelope.flags} />
    </div>
  );
}

/** The provenance chain, rendered so it reads top to bottom as a sequence of events. */
export function ProvenanceChain({ steps }: { steps: ProvenanceStep[] }) {
  return (
    <ol className="space-y-3">
      {steps.map((step) => (
        <li key={step.index} className="relative border-l border-base-700 pl-4">
          <span
            className={`absolute -left-[3px] top-1.5 h-1.5 w-1.5 rounded-full ${
              step.kind === "observation"
                ? "bg-accent-500"
                : step.kind === "validation"
                  ? "bg-status-validated"
                  : "bg-base-500"
            }`}
          />
          <div className="flex items-baseline gap-2">
            <span className="numeric text-[10px] uppercase tracking-widest text-base-500">
              {step.index.toString().padStart(2, "0")} {step.kind}
            </span>
            {step.acquired_at != null && (
              <span className="numeric text-[10px] text-base-600">
                {step.acquired_at.slice(0, 10)}
              </span>
            )}
          </div>
          <p className="mt-1 text-xs leading-relaxed text-base-300">{step.description}</p>
          <p className="numeric mt-1 text-[10px] text-base-600">
            {[
              step.source,
              step.dataset_id,
              step.access_route,
              step.spatial_ref,
              step.resolution_m != null ? `${step.resolution_m} m` : null,
              `pipeline ${step.pipeline_version}`,
              `algorithm ${step.algorithm_version}`,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {step.asset_ids.length > 0 && (
            <details className="mt-1">
              <summary className="numeric cursor-pointer text-[10px] text-base-600 hover:text-base-400">
                {step.asset_ids.length} source asset{step.asset_ids.length === 1 ? "" : "s"}
              </summary>
              <ul className="numeric mt-1 space-y-0.5 text-[10px] break-all text-base-600">
                {step.asset_ids.map((asset) => (
                  <li key={asset}>{asset}</li>
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
    <div className="border-t border-base-800 pt-3">
      <p className="text-xs text-base-300">{method.name}</p>
      {method.formula != null && (
        <p className="numeric mt-1 text-[11px] text-base-400">{method.formula}</p>
      )}
      <p className="mt-1 text-[11px] leading-relaxed text-base-500">{method.citation}</p>
      {method.notes != null && (
        <p className="mt-1 text-[11px] leading-relaxed text-base-500 italic">{method.notes}</p>
      )}
    </div>
  );
}

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="border border-dashed border-base-800 p-8 text-center">
      <p className="text-sm text-base-300">{title}</p>
      {detail !== undefined && (
        <p className="mx-auto mt-2 max-w-md text-xs leading-relaxed text-base-500">{detail}</p>
      )}
    </div>
  );
}
