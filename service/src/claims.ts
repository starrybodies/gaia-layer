/**
 * Claim identity and recording.
 *
 * A claim id is derived from the content of the claim, never minted at random. Two
 * consequences follow, both wanted:
 *
 * - Asking the same question twice returns the same id, so the claim table converges
 *   rather than growing without bound.
 * - Re-ingesting a period and getting a different number produces a *different* id, and
 *   the old claim row survives with its original provenance. A figure someone cited last
 *   month still resolves to what they were actually shown.
 *
 * MUST stay identical to `claim_id_for` in
 * `pipeline/src/gaia_pipeline/schemas/envelope.py`.
 */

import { createHash } from "node:crypto";
import { execute } from "./db.js";

/** Crockford base32: no I, L, O or U, so a transcribed id cannot become a different id. */
const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

export function claimIdFor(...parts: (string | number)[]): string {
  // Unit separator between parts, matching the Python side. Without it,
  // ("ab", "c") and ("a", "bc") would hash to the same id.
  const canonical = parts.map(String).join("\u001f");
  const digest = createHash("sha256").update(canonical).digest().subarray(0, 16);

  let value = 0n;
  for (const byte of digest) value = (value << 8n) | BigInt(byte);

  const chars = new Array<string>(26);
  for (let i = 25; i >= 0; i -= 1) {
    chars[i] = CROCKFORD[Number(value & 0x1fn)] as string;
    value >>= 5n;
  }
  return `clm_${chars.join("")}`;
}

export interface ClaimRecord {
  claim_id: string;
  claim_kind: "numeric" | "trend" | "substrate_score";
  indicator: string | null;
  aoi_id: string | null;
  geometry_hash: string;
  period_start: string;
  period_end: string;
  value_repr: string;
  unit: string;
  confidence: number;
  validation: unknown;
  method: unknown;
  provenance: unknown;
  source_ids: string[];
  payload: unknown;
}

/**
 * Persist a claim so `get_provenance` can answer for it later.
 *
 * `served_at` is set once and never moved; `last_served_at` tracks the most recent time
 * the same claim was handed out. The distinction matters when auditing when a figure first
 * entered circulation.
 */
export async function recordClaim(claim: ClaimRecord): Promise<void> {
  const now = new Date().toISOString();
  await execute(
    `INSERT INTO claim (claim_id, claim_kind, indicator, aoi_id, geometry_hash,
                        period_start, period_end, value_repr, unit, confidence,
                        validation_json, method_json, provenance_json, source_ids,
                        payload_json, served_at, last_served_at)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $16)
     ON CONFLICT (claim_id) DO UPDATE SET last_served_at = excluded.last_served_at`,
    [
      claim.claim_id,
      claim.claim_kind,
      claim.indicator,
      claim.aoi_id,
      claim.geometry_hash,
      claim.period_start,
      claim.period_end,
      claim.value_repr,
      claim.unit,
      claim.confidence,
      JSON.stringify(claim.validation),
      JSON.stringify(claim.method),
      JSON.stringify(claim.provenance),
      JSON.stringify(claim.source_ids),
      JSON.stringify(claim.payload),
      now,
    ],
  );
}

/** Record a batch of claims. Failures here must not fail the response. */
export async function recordClaims(claims: ClaimRecord[]): Promise<void> {
  for (const claim of claims) {
    try {
      await recordClaim(claim);
    } catch (error) {
      // A claim that fails to persist means `get_provenance` cannot answer for it later.
      // That is worth a loud log, but not worth withholding a validated measurement from
      // the caller who asked for it.
      console.error(`[service] failed to record claim ${claim.claim_id}:`, error);
    }
  }
}
