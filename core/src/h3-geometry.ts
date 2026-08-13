/**
 * A cell id turned into a ring, which is the one place a map can silently lie.
 *
 * `cellToBoundary` returns `[latitude, longitude]` pairs. GeoJSON wants `[longitude,
 * latitude]`. Swapping them produces a perfectly valid polygon somewhere else entirely — the
 * Okanagan lands off the coast of China — and nothing in the rendered map says so, because a
 * map with no features drawn and a map with features drawn out of view look identical.
 *
 * It lives in core rather than in the console because it is not a rendering decision. It is
 * the geometric identity of an identifier, the same for whoever asks, and putting it beside
 * the provenance guard keeps it somewhere with a test suite.
 */

import { cellToBoundary, isValidCell } from "h3-js";

/** A closed GeoJSON linear ring, `[longitude, latitude]`, for one H3 cell. */
export function cellToRing(h3: string): [number, number][] {
  // Checked with `isValidCell`, not by inspecting what came back. `cellToBoundary` does not
  // refuse a bad id: given "not-a-cell", "zzz" or the empty string it returns the same
  // perfectly well-formed hexagon at 69 N, 31 E, in Arctic Russia. A caller that trusted the
  // output would render a portfolio somewhere it has no exposure and see nothing wrong.
  if (!isValidCell(h3)) {
    throw new Error(
      `${JSON.stringify(h3)} is not an H3 cell id. h3-js answers a bad id with a ring in ` +
        "the Arctic rather than an error, so this is checked before the call, not after.",
    );
  }
  const boundary = cellToBoundary(h3);
  const ring = boundary.map(([lat, lng]) => [lng, lat] as [number, number]);
  // GeoJSON requires the first and last position to be identical. MapLibre tolerates an open
  // ring and other readers do not, and a demo that renders here and not in QGIS is worse
  // than one that renders nowhere.
  ring.push(ring[0] as [number, number]);
  return ring;
}

/** The bounding box of a set of cells, as `[west, south, east, north]`. */
export function ringsBounds(rings: [number, number][][]): [number, number, number, number] {
  if (rings.length === 0) throw new Error("no rings to bound");
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  for (const ring of rings) {
    for (const [lng, lat] of ring) {
      if (lng < west) west = lng;
      if (lng > east) east = lng;
      if (lat < south) south = lat;
      if (lat > north) north = lat;
    }
  }
  return [west, south, east, north];
}
