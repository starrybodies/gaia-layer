import { beforeEach, describe, expect, it } from "vitest";
import { Hono } from "hono";
import { rateLimit, resetRateLimit } from "./rate-limit.js";

function app(limit: number): Hono {
  const instance = new Hono();
  instance.use("*", rateLimit(limit));
  instance.get("/health", (c) => c.json({ status: "ok" }));
  instance.get("/v1/coverage", (c) => c.json({ aois: [] }));
  return instance;
}

describe("rateLimit", () => {
  beforeEach(() => resetRateLimit());

  it("passes requests under the limit", async () => {
    const instance = app(3);
    for (let i = 0; i < 3; i += 1) {
      const response = await instance.request("/v1/coverage");
      expect(response.status).toBe(200);
    }
  });

  it("rejects the request that crosses the limit", async () => {
    const instance = app(2);
    await instance.request("/v1/coverage");
    await instance.request("/v1/coverage");
    const response = await instance.request("/v1/coverage");

    expect(response.status).toBe(429);
    const body = (await response.json()) as { error: string; retryable: boolean };
    expect(body.error).toBe("rate_limited");
    expect(body.retryable).toBe(true);
  });

  it("reports remaining budget in headers", async () => {
    const instance = app(10);
    const response = await instance.request("/v1/coverage");
    expect(response.headers.get("x-ratelimit-limit")).toBe("10");
    expect(response.headers.get("x-ratelimit-remaining")).toBe("9");
    expect(response.headers.get("x-ratelimit-reset")).not.toBeNull();
  });

  it("sets retry-after when limiting", async () => {
    const instance = app(1);
    await instance.request("/v1/coverage");
    const response = await instance.request("/v1/coverage");
    expect(Number(response.headers.get("retry-after"))).toBeGreaterThan(0);
  });

  it("never limits the health endpoint", async () => {
    const instance = app(1);
    await instance.request("/v1/coverage");
    await instance.request("/v1/coverage");
    // Health has to stay reachable while limited, or a load balancer would pull a
    // node out of rotation for being busy rather than for being broken.
    const response = await instance.request("/health");
    expect(response.status).toBe(200);
  });

  it("counts separate client addresses separately", async () => {
    const instance = app(1);
    await instance.request("/v1/coverage", { headers: { "x-forwarded-for": "10.0.0.1" } });
    const other = await instance.request("/v1/coverage", {
      headers: { "x-forwarded-for": "10.0.0.2" },
    });
    expect(other.status).toBe(200);
  });

  it("reads the first address from a forwarded chain", async () => {
    const instance = app(1);
    await instance.request("/v1/coverage", {
      headers: { "x-forwarded-for": "10.0.0.1, 10.0.0.9" },
    });
    const same = await instance.request("/v1/coverage", {
      headers: { "x-forwarded-for": "10.0.0.1, 10.0.0.8" },
    });
    expect(same.status).toBe(429);
  });
});
