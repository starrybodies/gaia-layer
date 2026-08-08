/**
 * Rate limiting.
 *
 * A fixed-window counter held in memory. That is the right size for v0.1: one process, one
 * shared key, no multi-tenancy. A distributed limiter would need a store this deployment
 * does not have, and adding one now would be speculative infrastructure.
 *
 * The limit is per client address. With a single shared API key there is no identity to
 * key on, and address is the honest approximation.
 */

import type { Context, Next } from "hono";

interface Window {
  count: number;
  resetAt: number;
}

const WINDOW_MS = 60_000;
const DEFAULT_LIMIT = 120;

const windows = new Map<string, Window>();

function clientKey(c: Context): string {
  return (
    c.req.header("x-forwarded-for")?.split(",")[0]?.trim() ??
    c.req.header("x-real-ip") ??
    "local"
  );
}

/** Drop windows that have expired, so the map cannot grow without bound. */
function sweep(now: number): void {
  for (const [key, window] of windows) {
    if (window.resetAt <= now) windows.delete(key);
  }
}

export function rateLimit(limit = Number(process.env["GAIA_RATE_LIMIT"] ?? DEFAULT_LIMIT)) {
  return async (c: Context, next: Next): Promise<Response | void> => {
    if (c.req.path === "/health") return next();

    const now = Date.now();
    if (windows.size > 1024) sweep(now);

    const key = clientKey(c);
    const existing = windows.get(key);
    const window: Window =
      existing === undefined || existing.resetAt <= now
        ? { count: 0, resetAt: now + WINDOW_MS }
        : existing;

    window.count += 1;
    windows.set(key, window);

    const remaining = Math.max(0, limit - window.count);
    c.header("x-ratelimit-limit", String(limit));
    c.header("x-ratelimit-remaining", String(remaining));
    c.header("x-ratelimit-reset", String(Math.ceil(window.resetAt / 1000)));

    if (window.count > limit) {
      const retryAfter = Math.ceil((window.resetAt - now) / 1000);
      c.header("retry-after", String(retryAfter));
      return c.json(
        {
          error: "rate_limited",
          message: `More than ${limit} requests in a minute from this address.`,
          detail: `Retry in ${retryAfter} seconds. Raise the ceiling with GAIA_RATE_LIMIT.`,
          retryable: true,
          generated_at: new Date().toISOString(),
        },
        429,
      );
    }

    return next();
  };
}

/** Test hook. Windows are process-local, so clearing them is enough to reset state. */
export function resetRateLimit(): void {
  windows.clear();
}
