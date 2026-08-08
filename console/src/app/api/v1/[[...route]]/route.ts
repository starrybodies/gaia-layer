/**
 * The layer's REST API, mounted inside the console.
 *
 * One deployment serves both the console and the interface an agent uses, and neither
 * reimplements the other — this is the same configured Hono app that `@gaia/api` binds to a
 * port when run standalone. Running them together is a deployment choice, not an
 * architectural one; the service layer underneath is unchanged.
 */

import { app } from "@gaia/api";
import { Hono } from "hono";
import { handle } from "hono/vercel";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
// Opening a 47 MB DuckDB lake and walking a provenance chain is not a ten-second operation
// on a cold start.
export const maxDuration = 60;

// The app declares its routes as `/v1/...`; here they are reached at `/api/v1/...`, so it
// is mounted under `/api` rather than having its own paths rewritten.
const mounted = new Hono().route("/api", app);
const handler = handle(mounted);

export const GET = handler;
export const POST = handler;
export const OPTIONS = handler;
