/**
 * The provenance guard.
 *
 * The build prompt asks for a test that scans served responses for any value lacking a
 * provenance chain and fails CI if it finds one. A text grep would be fooled by
 * formatting, so this walks the actual response object instead: anywhere a `value` key
 * appears, the object carrying it must also carry a non-empty `provenance` array, a
 * `validation_status`, a numeric `confidence`, and a `method`.
 *
 * This runs in two places: as a vitest suite over recorded service output, and as a
 * runtime assertion in the service layer's emit path when `GAIA_STRICT_GUARD` is set.
 */

export interface GuardViolation {
  /** JSON-pointer-ish path to the offending object. */
  path: string;
  reason: string;
}

const REQUIRED_ALONGSIDE_VALUE = [
  "provenance",
  "validation_status",
  "confidence",
  "method",
] as const;

/**
 * Keys whose contents are opaque payloads rather than served claims. A `parameters` bag
 * recording that a processing step ran with `{ value: 3 }` is not a claim, and must not be
 * treated as one. Opacity propagates to descendants, which is why `confidence_basis`
 * covers the confidence components nested inside it — while a substrate score's
 * `components` stay in scope, because the envelopes they carry are real claims.
 */
const OPAQUE_KEYS = new Set(["parameters", "confidence_basis"]);

function isRecord(node: unknown): node is Record<string, unknown> {
  return typeof node === "object" && node !== null && !Array.isArray(node);
}

/**
 * Walk a served response and collect every place a number escapes without its context.
 * Returns an empty array when the payload is clean.
 */
export function findProvenanceViolations(payload: unknown, rootPath = "$"): GuardViolation[] {
  const violations: GuardViolation[] = [];

  const walk = (node: unknown, path: string, opaque: boolean): void => {
    if (Array.isArray(node)) {
      node.forEach((item, i) => walk(item, `${path}[${i}]`, opaque));
      return;
    }
    if (!isRecord(node)) return;

    if (!opaque && "value" in node) {
      for (const key of REQUIRED_ALONGSIDE_VALUE) {
        if (!(key in node)) {
          violations.push({ path, reason: `carries "value" but no "${key}"` });
        }
      }
      const provenance = node["provenance"];
      if (Array.isArray(provenance) && provenance.length === 0) {
        violations.push({ path, reason: 'carries "value" with an empty provenance chain' });
      }
      const confidence = node["confidence"];
      if (typeof confidence === "number" && (confidence < 0 || confidence > 1)) {
        violations.push({ path, reason: `confidence ${confidence} outside [0, 1]` });
      }
    }

    for (const [key, child] of Object.entries(node)) {
      walk(child, `${path}.${key}`, opaque || OPAQUE_KEYS.has(key));
    }
  };

  walk(payload, rootPath, false);
  return violations;
}

export class ProvenanceGuardError extends Error {
  constructor(public readonly violations: GuardViolation[]) {
    super(
      `served payload contains ${violations.length} value(s) without a provenance chain:\n` +
        violations.map((v) => `  ${v.path}: ${v.reason}`).join("\n"),
    );
    this.name = "ProvenanceGuardError";
  }
}

/** Throw if the payload would serve a number the caller could not cite. */
export function assertProvenanced(payload: unknown, context = "$"): void {
  const violations = findProvenanceViolations(payload, context);
  if (violations.length > 0) throw new ProvenanceGuardError(violations);
}
