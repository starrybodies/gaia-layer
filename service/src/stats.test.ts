import { describe, expect, it } from "vitest";
import { effectiveSampleSize, logGamma, ols, tTestPValue, welchTTest } from "./stats.js";

describe("logGamma", () => {
  it("matches known factorials", () => {
    expect(Math.exp(logGamma(5))).toBeCloseTo(24, 6); // 4!
    expect(Math.exp(logGamma(1))).toBeCloseTo(1, 9);
    expect(Math.exp(logGamma(0.5))).toBeCloseTo(Math.sqrt(Math.PI), 9);
  });
});

describe("tTestPValue", () => {
  // Reference values from the two-sided Student t distribution.
  it("returns 1 for a zero statistic", () => {
    expect(tTestPValue(0, 10)).toBeCloseTo(1, 9);
  });

  it("matches published critical values", () => {
    expect(tTestPValue(2.228, 10)).toBeCloseTo(0.05, 3);
    expect(tTestPValue(3.169, 10)).toBeCloseTo(0.01, 3);
    expect(tTestPValue(1.96, 100000)).toBeCloseTo(0.05, 3);
  });

  it("is symmetric in the sign of t", () => {
    expect(tTestPValue(-2.5, 8)).toBeCloseTo(tTestPValue(2.5, 8), 12);
  });
});

describe("ols", () => {
  it("recovers an exact line", () => {
    const xs = [0, 1, 2, 3, 4, 5];
    const ys = xs.map((x) => 3 * x + 7);
    const fit = ols(xs, ys);
    expect(fit.slope).toBeCloseTo(3, 9);
    expect(fit.intercept).toBeCloseTo(7, 9);
    expect(fit.rSquared).toBeCloseTo(1, 9);
    expect(fit.pValue).toBeLessThan(0.001);
  });

  it("finds no significant slope in flat data", () => {
    const fit = ols([0, 1, 2, 3, 4], [5, 5, 5, 5, 5]);
    expect(fit.slope).toBeCloseTo(0, 9);
    expect(fit.pValue).toBe(1);
  });

  it("refuses to fit fewer than three points", () => {
    const fit = ols([0, 1], [0, 10]);
    expect(fit.slope).toBe(0);
    expect(fit.pValue).toBe(1);
    expect(fit.n).toBe(2);
  });

  it("reports a weak fit as weak", () => {
    const xs = [0, 1, 2, 3, 4, 5, 6, 7];
    const ys = [3, -1, 4, -2, 5, 0, 3, -1];
    const fit = ols(xs, ys);
    expect(fit.rSquared).toBeLessThan(0.3);
    expect(fit.pValue).toBeGreaterThan(0.05);
  });

  it("keeps r squared inside [0, 1]", () => {
    const fit = ols([1, 2, 3, 4], [2.1, 1.9, 2.4, 2.2]);
    expect(fit.rSquared).toBeGreaterThanOrEqual(0);
    expect(fit.rSquared).toBeLessThanOrEqual(1);
  });
});

describe("welchTTest", () => {
  it("finds no difference between identical summaries", () => {
    const result = welchTTest(0.5, 0.1, 100, 0.5, 0.1, 100);
    expect(result.pValue).toBeCloseTo(1, 6);
    expect(result.significant).toBe(false);
  });

  it("detects a large separation", () => {
    const result = welchTTest(0.2, 0.05, 200, 0.6, 0.05, 200);
    expect(result.significant).toBe(true);
    expect(result.pValue).toBeLessThan(1e-6);
  });

  it("does not call a small shift significant under high variance", () => {
    const result = welchTTest(0.5, 0.4, 10, 0.55, 0.4, 10);
    expect(result.significant).toBe(false);
  });

  it("declines to test samples of one", () => {
    expect(welchTTest(1, 0, 1, 5, 0, 1).significant).toBe(false);
  });
});

describe("effectiveSampleSize", () => {
  it("discounts autocorrelated pixels", () => {
    // 20 m pixels, 200 m decorrelation length: 100 pixels per independent sample.
    expect(effectiveSampleSize(10000, 20, 200)).toBe(100);
  });

  it("never returns fewer than two for a non-empty raster", () => {
    expect(effectiveSampleSize(5, 20, 200)).toBe(2);
  });

  it("returns zero for an empty raster", () => {
    expect(effectiveSampleSize(0, 20)).toBe(0);
  });
});
