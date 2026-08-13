/**
 * REST routes.
 *
 * Every handler is three lines: parse, call the service, return. Any logic that appears
 * here is logic the MCP server does not have, and the two interfaces would start
 * disagreeing about what the layer says.
 */

import {
  ServiceError,
  comparePeriods,
  compareBaseline,
  explainScore,
  getComponent,
  getEii,
  portfolioChange,
  portfolioRanking,
  portfolioScan,
  readDemoBook,
  readDossier,
  getCells,
  getEcologicalState,
  getProvenance,
  getWildfireSubstrateScore,
  interpretLayer,
  listCoverage,
  listLayers,
  listPeriods,
  type DateRange,
  type GeometryInput,
} from "@gaia/service";
import { Hono } from "hono";
import { z } from "zod";

const dateSchema = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "expected YYYY-MM-DD");

const dateRangeSchema = z
  .object({ start: dateSchema, end: dateSchema })
  .refine((r) => r.start <= r.end, { message: "start must not follow end" });

const geometrySchema = z.union([
  z.object({ type: z.literal("Polygon"), coordinates: z.array(z.unknown()) }),
  z.object({ type: z.literal("MultiPolygon"), coordinates: z.array(z.unknown()) }),
  z.object({
    west: z.number().min(-180).max(180),
    south: z.number().min(-90).max(90),
    east: z.number().min(-180).max(180),
    north: z.number().min(-90).max(90),
  }),
]);

const indicatorsSchema = z.array(z.string()).optional();

/** Turn a Zod failure into the same structured error shape everything else uses. */
async function parse<T>(schema: z.ZodType<T>, body: unknown): Promise<T> {
  const result = await schema.safeParseAsync(body);
  if (!result.success) {
    throw new ServiceError(
      "invalid_request",
      "The request body did not match the expected shape.",
      result.error.issues.map((i) => `${i.path.join(".") || "body"}: ${i.message}`).join("; "),
    );
  }
  return result.data;
}

export const routes = new Hono();

routes.post("/v1/ecological-state", async (c) => {
  const body = await parse(
    z.object({
      geometry: geometrySchema,
      date_range: dateRangeSchema,
      indicators: indicatorsSchema,
    }),
    await c.req.json(),
  );
  return c.json(
    await getEcologicalState(
      body.geometry as GeometryInput,
      body.date_range as DateRange,
      body.indicators,
    ),
  );
});

routes.post("/v1/wildfire-substrate-score", async (c) => {
  const body = await parse(
    z.object({ geometry: geometrySchema, date: dateSchema }),
    await c.req.json(),
  );
  return c.json(await getWildfireSubstrateScore(body.geometry as GeometryInput, body.date));
});

routes.get("/v1/provenance/:claimId", async (c) => {
  return c.json(await getProvenance(c.req.param("claimId")));
});

routes.post("/v1/compare-periods", async (c) => {
  const body = await parse(
    z.object({
      geometry: geometrySchema,
      period_a: dateRangeSchema,
      period_b: dateRangeSchema,
      indicators: indicatorsSchema,
    }),
    await c.req.json(),
  );
  return c.json(
    await comparePeriods(
      body.geometry as GeometryInput,
      body.period_a as DateRange,
      body.period_b as DateRange,
      body.indicators,
    ),
  );
});

routes.get("/v1/coverage", async (c) => {
  const aoiId = c.req.query("aoi_id");
  return c.json(await listCoverage(aoiId));
});

// --- console support -------------------------------------------------------------
// Not part of the agent surface. The map needs cell geometry and a list of periods, and
// both are pure reads over the same lake.

routes.get("/v1/cells/:aoiId/:indicator/:periodStart", async (c) => {
  return c.json(
    await getCells(
      c.req.param("aoiId"),
      c.req.param("indicator"),
      c.req.param("periodStart"),
    ),
  );
});

routes.get("/v1/periods/:aoiId", async (c) => {
  return c.json(await listPeriods(c.req.param("aoiId")));
});

routes.get("/v1/layers/:aoiId", async (c) => {
  return c.json(await listLayers(c.req.param("aoiId")));
});

// Deterministic landscape analysis of one layer: distribution, zonal breakdowns by
// terrain, correlations, extremes, and a written read assembled from those numbers.
routes.get("/v1/interpretation/:aoiId/:indicator/:periodStart", async (c) => {
  return c.json(
    await interpretLayer(
      c.req.param("aoiId"),
      c.req.param("indicator"),
      c.req.param("periodStart"),
    ),
  );
});

// --- the wildfire Ecosystem Integrity Index ---------------------------------------
// The v0.2 archive. Every handler here is parse, call, return, for the same reason as
// above: an agent reaching these through MCP and an analyst reaching them through the
// console must be told the same thing.

const h3Schema = z
  .string()
  .regex(
    /^[0-9a-fA-F]{15,16}$/,
    "expected an H3 cell id. This surface takes cell identifiers only — never an address " +
      "or a coordinate.",
  );

// `weight` is the caller's own exposure measure. The layer weights a mean by it and does
// not store it; there is deliberately no field here for anything that locates a risk.
const bookSchema = z.object({
  cells: z
    .array(z.object({ h3: h3Schema, weight: z.number().finite().nonnegative().optional() }))
    .min(1)
    .max(20_000),
  component: z.string().optional(),
});

routes.get("/v1/eii/dossier", async (c) => {
  return c.json(readDossier());
});

routes.get("/v1/eii/demo-book", async (c) => {
  return c.json(readDemoBook());
});

routes.get("/v1/eii/baseline", async (c) => {
  return c.json(await compareBaseline());
});

routes.get("/v1/eii/cell/:h3", async (c) => {
  const h3 = await parse(h3Schema, c.req.param("h3"));
  const year = c.req.query("year");
  return c.json(await getEii({ h3, year: year === undefined ? undefined : Number(year) }));
});

routes.get("/v1/eii/cell/:h3/explain", async (c) => {
  const h3 = await parse(h3Schema, c.req.param("h3"));
  const year = c.req.query("year");
  return c.json(await explainScore({ h3, year: year === undefined ? undefined : Number(year) }));
});

routes.get("/v1/eii/cell/:h3/component/:component", async (c) => {
  const h3 = await parse(h3Schema, c.req.param("h3"));
  const year = c.req.query("year");
  return c.json(
    await getComponent({
      h3,
      component: c.req.param("component"),
      year: year === undefined ? undefined : Number(year),
    }),
  );
});

routes.post("/v1/eii/portfolio-scan", async (c) => {
  const body = await parse(
    z.object({
      cells: z.array(h3Schema).min(1).max(20_000),
      year: z.number().int().optional(),
      threshold: z.number().finite().optional(),
    }),
    await c.req.json(),
  );
  return c.json(await portfolioScan(body));
});

routes.post("/v1/eii/portfolio-ranking", async (c) => {
  const body = await parse(
    bookSchema.extend({ year: z.number().int().optional() }),
    await c.req.json(),
  );
  return c.json(await portfolioRanking(body));
});

routes.post("/v1/eii/portfolio-change", async (c) => {
  const body = await parse(
    bookSchema.extend({ before: z.number().int(), after: z.number().int() }),
    await c.req.json(),
  );
  return c.json(await portfolioChange(body));
});
