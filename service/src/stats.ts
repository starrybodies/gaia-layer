/**
 * The statistics the service layer needs to decide whether a difference is real.
 *
 * A slope without a significance test, or a period-over-period delta without one, invites
 * the reader to see change in noise. Both tools that report change go through here.
 *
 * This is general-purpose statistics, not ecological logic — ecological logic stays in the
 * pipeline. The functions here are standard and covered by tests against known values.
 */

/** Regularised incomplete beta function, by the continued-fraction method (Lentz). */
function incompleteBeta(a: number, b: number, x: number): number {
  if (x <= 0) return 0;
  if (x >= 1) return 1;

  const lbeta =
    logGamma(a + b) - logGamma(a) - logGamma(b) + a * Math.log(x) + b * Math.log(1 - x);

  if (x < (a + 1) / (a + b + 2)) {
    return (Math.exp(lbeta) * betaContinuedFraction(a, b, x)) / a;
  }
  return 1 - (Math.exp(lbeta) * betaContinuedFraction(b, a, 1 - x)) / b;
}

function betaContinuedFraction(a: number, b: number, x: number): number {
  const TINY = 1e-30;
  const EPS = 3e-12;
  const qab = a + b;
  const qap = a + 1;
  const qam = a - 1;

  let c = 1;
  let d = 1 - (qab * x) / qap;
  if (Math.abs(d) < TINY) d = TINY;
  d = 1 / d;
  let h = d;

  for (let m = 1; m <= 300; m += 1) {
    const m2 = 2 * m;
    let aa = (m * (b - m) * x) / ((qam + m2) * (a + m2));
    d = 1 + aa * d;
    if (Math.abs(d) < TINY) d = TINY;
    c = 1 + aa / c;
    if (Math.abs(c) < TINY) c = TINY;
    d = 1 / d;
    h *= d * c;

    aa = (-(a + m) * (qab + m) * x) / ((a + m2) * (qap + m2));
    d = 1 + aa * d;
    if (Math.abs(d) < TINY) d = TINY;
    c = 1 + aa / c;
    if (Math.abs(c) < TINY) c = TINY;
    d = 1 / d;
    const del = d * c;
    h *= del;
    if (Math.abs(del - 1) < EPS) break;
  }
  return h;
}

const LANCZOS = [
  676.5203681218851, -1259.1392167224028, 771.32342877765313, -176.61502916214059,
  12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7,
];

/** Log-gamma, Lanczos approximation. */
export function logGamma(z: number): number {
  if (z < 0.5) {
    return Math.log(Math.PI / Math.sin(Math.PI * z)) - logGamma(1 - z);
  }
  const x = z - 1;
  let a = 0.99999999999980993;
  const t = x + 7.5;
  LANCZOS.forEach((coefficient, i) => {
    a += coefficient / (x + i + 1);
  });
  return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(a);
}

/** Two-sided p-value for a t statistic with `df` degrees of freedom. */
export function tTestPValue(t: number, df: number): number {
  if (!Number.isFinite(t) || df <= 0) return 1;
  const x = df / (df + t * t);
  return Math.min(1, incompleteBeta(df / 2, 0.5, x));
}

export interface OlsFit {
  slope: number;
  intercept: number;
  rSquared: number;
  pValue: number;
  n: number;
  standardError: number;
}

/**
 * Ordinary least squares of y on x, with the significance of the slope.
 *
 * Returns a fit with `pValue` 1 and zero slope when there are fewer than three points or
 * no variation in x — the honest answer being "this tells you nothing".
 */
export function ols(xs: number[], ys: number[]): OlsFit {
  const n = Math.min(xs.length, ys.length);
  const empty: OlsFit = {
    slope: 0,
    intercept: ys[0] ?? 0,
    rSquared: 0,
    pValue: 1,
    n,
    standardError: Number.POSITIVE_INFINITY,
  };
  if (n < 3) return empty;

  const meanX = xs.slice(0, n).reduce((a, b) => a + b, 0) / n;
  const meanY = ys.slice(0, n).reduce((a, b) => a + b, 0) / n;

  let sxx = 0;
  let sxy = 0;
  let syy = 0;
  for (let i = 0; i < n; i += 1) {
    const dx = (xs[i] ?? 0) - meanX;
    const dy = (ys[i] ?? 0) - meanY;
    sxx += dx * dx;
    sxy += dx * dy;
    syy += dy * dy;
  }
  if (sxx === 0) return empty;

  const slope = sxy / sxx;
  const intercept = meanY - slope * meanX;
  const ssResidual = Math.max(0, syy - slope * sxy);
  const rSquared = syy === 0 ? 0 : Math.max(0, Math.min(1, 1 - ssResidual / syy));

  const df = n - 2;
  const standardError = df > 0 ? Math.sqrt(ssResidual / df / sxx) : Number.POSITIVE_INFINITY;
  const pValue =
    standardError === 0 || !Number.isFinite(standardError)
      ? ssResidual === 0 && slope !== 0
        ? 0
        : 1
      : tTestPValue(slope / standardError, df);

  return { slope, intercept, rSquared, pValue, n, standardError };
}

export interface WelchResult {
  t: number;
  df: number;
  pValue: number;
  significant: boolean;
}

/**
 * Welch's unequal-variance t-test between two summarised samples.
 *
 * Used by `compare_periods`, where each period is already reduced to a mean, a standard
 * deviation and a pixel count, so the raw samples are not available.
 */
export function welchTTest(
  meanA: number,
  sdA: number,
  nA: number,
  meanB: number,
  sdB: number,
  nB: number,
  alpha = 0.05,
): WelchResult {
  const varA = sdA * sdA;
  const varB = sdB * sdB;
  if (nA < 2 || nB < 2 || (varA === 0 && varB === 0)) {
    return { t: 0, df: 0, pValue: 1, significant: false };
  }
  const seSquared = varA / nA + varB / nB;
  const se = Math.sqrt(seSquared);
  if (se === 0) return { t: 0, df: 0, pValue: 1, significant: false };

  const t = (meanB - meanA) / se;
  const df =
    (seSquared * seSquared) /
    ((varA * varA) / (nA * nA * (nA - 1)) + (varB * varB) / (nB * nB * (nB - 1)));
  const pValue = tTestPValue(t, df);
  return { t, df, pValue, significant: pValue < alpha };
}

/**
 * Effective sample size for a spatially autocorrelated raster.
 *
 * Treating every 20 m pixel as an independent observation would make trivial differences
 * look overwhelmingly significant, because neighbouring pixels are not independent. This
 * discounts the pixel count to an effective count at a stated decorrelation length. The
 * assumed length is coarse and is reported alongside any result that uses it.
 */
export function effectiveSampleSize(
  pixels: number,
  pixelSizeM: number,
  decorrelationLengthM = 200,
): number {
  if (pixels <= 0) return 0;
  const pixelsPerCorrelationLength = Math.max(1, decorrelationLengthM / pixelSizeM);
  return Math.max(2, Math.floor(pixels / (pixelsPerCorrelationLength * pixelsPerCorrelationLength)));
}
