/**
 * Node listener for the REST API.
 *
 * The app itself lives in `app.ts`; this only binds it to a port. Splitting them lets the
 * same app be mounted inside a serverless runtime, where there is no port to bind.
 */

import { serve } from "@hono/node-server";
import { API_KEY_HEADER } from "@gaia/core";
import { lakePath } from "@gaia/service";
import { app } from "./app.js";

const PORT = Number(process.env["API_PORT"] ?? 8811);

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
