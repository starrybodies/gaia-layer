/**
 * REST mirror of the MCP surface.
 *
 * Exists so a consumer without an MCP client can still reach the layer. Every route is a
 * thin adapter over `@gaia/service` — request in, service call, envelope out. No route may
 * contain query logic, because then two transports would disagree about what the layer
 * says.
 */

import { serve } from "@hono/node-server";
import { API_KEY_HEADER, assertProvenanced } from "@gaia/core";
import { ServiceError, isPopulated, lakePath } from "@gaia/service";
import { Hono } from "hono";
import { cors } from "hono/cors";
import type { Context, Next } from "hono";
import { rateLimit } from "./rate-limit.js";
import { routes } from "./routes.js";

const PORT = Number(process.env["API_PORT"] ?? 8811);

export const app = new Hono();

/**
 * Single shared key, read from the environment. v0.1 has no user model by design; this is
 * a deployment boundary, not an identity system.
 *
 * With no key configured the API is open, and says so on startup rather than pretending
 * to be protected.
 */
async function apiKeyAuth(c: Context, next: Next): Promise<Response | void> {
  const expected = process.env["GAIA_API_KEY"];
  if (expected === undefined || expected === "") return next();
  if (c.req.path === "/health") return next();

  const presented = c.req.header(API_KEY_HEADER) ?? "";
  if (presented !== expected) {
    return c.json(
      {
        error: "unauthorized",
        message: `Missing or invalid ${API_KEY_HEADER} header.`,
        retryable: false,
        generated_at: new Date().toISOString(),
      },
      401,
    );
  }
  return next();
}

// The console runs on a different port, so it is a cross-origin caller by construction.
app.use("*", cors({ origin: (origin) => origin, allowHeaders: [API_KEY_HEADER, "content-type"] }));
app.use("*", rateLimit());
app.use("*", apiKeyAuth);

app.onError((err, c) => {
  if (err instanceof ServiceError) {
    const status = err.code === "claim_not_found" || err.code === "aoi_not_ingested" ? 404 : 400;
    return c.json(err.toResponse(), status);
  }
  console.error("[api] unhandled error", err);
  return c.json(
    {
      error: "internal",
      message: "The layer failed to answer this request.",
      retryable: true,
      generated_at: new Date().toISOString(),
    },
    500,
  );
});

app.get("/health", async (c) => {
  const populated = await isPopulated();
  return c.json({
    status: populated ? "ok" : "empty",
    lake: lakePath(),
    populated,
    generated_at: new Date().toISOString(),
  });
});

/**
 * Every JSON response the API sends passes the provenance guard first.
 *
 * This is the whitepaper's second lesson enforced at the last possible moment: even if a
 * bug upstream produced a bare number, it does not leave the process.
 */
app.use("*", async (c, next) => {
  await next();
  if (process.env["GAIA_STRICT_GUARD"] === "0") return;
  const contentType = c.res.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return;
  const clone = c.res.clone();
  const body: unknown = await clone.json();
  assertProvenanced(body, c.req.path);
});

app.route("/", routes);

app.notFound((c) =>
  c.json(
    {
      error: "invalid_request",
      message: `No route for ${c.req.method} ${c.req.path}.`,
      retryable: false,
      generated_at: new Date().toISOString(),
    },
    404,
  ),
);

if (process.env["NODE_ENV"] !== "test") {
  const keyConfigured = (process.env["GAIA_API_KEY"] ?? "") !== "";
  console.log(`[api] listening on http://127.0.0.1:${PORT}`);
  console.log(`[api] lake: ${lakePath()}`);
  console.log(
    keyConfigured
      ? `[api] api key required via ${API_KEY_HEADER}`
      : "[api] no GAIA_API_KEY set — the API is unauthenticated",
  );
  console.log(`[api] rate limit ${process.env["GAIA_RATE_LIMIT"] ?? 120} requests/minute/address`);
  serve({ fetch: app.fetch, port: PORT, hostname: "127.0.0.1" });
}
