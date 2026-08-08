/**
 * Geometry canonicalisation and hashing.
 *
 * MUST stay byte-identical to `canonical_geometry` in
 * `pipeline/src/gaia_pipeline/config.py`. The hash is how a request finds its ingested
 * values, so a divergence here presents as "no coverage" for data that exists.
 *
 * The format is a fixed-precision string rather than JSON precisely because JSON float
 * rendering does not agree across the two languages.
 */

import { createHash } from "node:crypto";

export interface PolygonGeometry {
  type: "Polygon";
  coordinates: number[][][];
}

export interface MultiPolygonGeometry {
  type: "MultiPolygon";
  coordinates: number[][][][];
}

export interface BBoxGeometry {
  west: number;
  south: number;
  east: number;
  north: number;
}

export type GeometryInput = PolygonGeometry | MultiPolygonGeometry | BBoxGeometry;

export function isBBox(g: GeometryInput): g is BBoxGeometry {
  return "west" in g && "south" in g && "east" in g && "north" in g;
}

export function bboxToPolygon(b: BBoxGeometry): PolygonGeometry {
  return {
    type: "Polygon",
    coordinates: [
      [
        [b.west, b.south],
        [b.east, b.south],
        [b.east, b.north],
        [b.west, b.north],
        [b.west, b.south],
      ],
    ],
  };
}

function ring(points: number[][]): string {
  return points
    .map((pt) => {
      const x = pt[0];
      const y = pt[1];
      if (x === undefined || y === undefined) {
        throw new Error("geometry position must have at least two ordinates");
      }
      return `${x.toFixed(6)},${y.toFixed(6)}`;
    })
    .join(";");
}

export function canonicalGeometry(geometry: GeometryInput): string {
  const g: PolygonGeometry | MultiPolygonGeometry = isBBox(geometry)
    ? bboxToPolygon(geometry)
    : geometry;

  if (g.type === "Polygon") {
    return `Polygon:${g.coordinates.map(ring).join("|")}`;
  }
  return `MultiPolygon:${g.coordinates.map((poly) => poly.map(ring).join("|")).join("#")}`;
}

export function geometryHash(geometry: GeometryInput): string {
  return createHash("sha256").update(canonicalGeometry(geometry)).digest("hex").slice(0, 16);
}

/** Bounding box of any supported geometry, in WGS84 degrees. */
export function boundsOf(geometry: GeometryInput): BBoxGeometry {
  if (isBBox(geometry)) return geometry;
  const rings: number[][][] =
    geometry.type === "Polygon" ? geometry.coordinates : geometry.coordinates.flat();
  const xs: number[] = [];
  const ys: number[] = [];
  for (const r of rings) {
    for (const pt of r) {
      const x = pt[0];
      const y = pt[1];
      if (x !== undefined) xs.push(x);
      if (y !== undefined) ys.push(y);
    }
  }
  if (xs.length === 0) throw new Error("geometry has no coordinates");
  return {
    west: Math.min(...xs),
    south: Math.min(...ys),
    east: Math.max(...xs),
    north: Math.max(...ys),
  };
}

const EARTH_RADIUS_KM = 6371.0088;

/**
 * Spherical-excess area of a polygon ring, in square kilometres.
 *
 * Adequate at the scale of an area of interest; this is a display figure, not a survey.
 */
export function areaKm2(geometry: GeometryInput): number {
  const g: PolygonGeometry | MultiPolygonGeometry = isBBox(geometry)
    ? bboxToPolygon(geometry)
    : geometry;
  const polygons: number[][][][] = g.type === "Polygon" ? [g.coordinates] : g.coordinates;

  let total = 0;
  for (const poly of polygons) {
    poly.forEach((r, index) => {
      const a = Math.abs(ringAreaKm2(r));
      total += index === 0 ? a : -a; // interior rings are holes
    });
  }
  return total;
}

function ringAreaKm2(points: number[][]): number {
  if (points.length < 4) return 0;
  const rad = (deg: number): number => (deg * Math.PI) / 180;
  let sum = 0;
  for (let i = 0; i < points.length - 1; i += 1) {
    const p1 = points[i];
    const p2 = points[i + 1];
    if (p1 === undefined || p2 === undefined) continue;
    const [lon1, lat1] = [p1[0] ?? 0, p1[1] ?? 0];
    const [lon2, lat2] = [p2[0] ?? 0, p2[1] ?? 0];
    sum += (rad(lon2) - rad(lon1)) * (2 + Math.sin(rad(lat1)) + Math.sin(rad(lat2)));
  }
  return (sum * EARTH_RADIUS_KM * EARTH_RADIUS_KM) / 2;
}
