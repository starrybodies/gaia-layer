import { describe, expect, it } from "vitest";
import { latLngToCell } from "h3-js";
import { cellToRing, ringsBounds } from "./h3-geometry.js";

/**
 * The swap is the whole test.
 *
 * A ring built with latitude and longitude the wrong way round is a valid polygon, renders
 * without an error, and puts the Okanagan somewhere off the coast of China. On a map that is
 * indistinguishable from a layer that failed to draw, because both show an empty study area.
 * So this pins the coordinates against the place the cell is actually in.
 */

// The ERA5 cell McDougall Creek burned in: 49.88 N, 119.50 W.
const KELOWNA = latLngToCell(49.88, -119.5, 8);

describe("cellToRing", () => {
  it("returns longitude first, which is the half of this that goes wrong", () => {
    for (const [lng, lat] of cellToRing(KELOWNA)) {
      expect(lng).toBeGreaterThan(-120.5);
      expect(lng).toBeLessThan(-118.5);
      expect(lat).toBeGreaterThan(49.0);
      expect(lat).toBeLessThan(50.5);
    }
  });

  it("closes the ring, because an open one renders here and not in QGIS", () => {
    const ring = cellToRing(KELOWNA);
    expect(ring[0]).toEqual(ring[ring.length - 1]);
    expect(ring.length).toBeGreaterThanOrEqual(7);
  });

  it("draws a cell about the size a resolution-8 cell is", () => {
    const [west, south, east, north] = ringsBounds([cellToRing(KELOWNA)]);
    // 0.74 km2 is roughly a kilometre across: a hundredth of a degree of latitude, and a
    // little more of longitude at this latitude. Orders of magnitude are what matter here.
    expect(north - south).toBeGreaterThan(0.002);
    expect(north - south).toBeLessThan(0.02);
    expect(east - west).toBeGreaterThan(0.002);
    expect(east - west).toBeLessThan(0.03);
  });

  it("puts a cell's ring around the coordinate the cell was made from", () => {
    const [west, south, east, north] = ringsBounds([cellToRing(KELOWNA)]);
    expect(49.88).toBeGreaterThan(south);
    expect(49.88).toBeLessThan(north);
    expect(-119.5).toBeGreaterThan(west);
    expect(-119.5).toBeLessThan(east);
  });

  it.each(["not-a-cell", "", "zzz", "1 Example Road"])(
    "refuses %o rather than drawing the Arctic hexagon h3-js hands back",
    (bad) => {
      // h3-js does not throw on a bad id. It returns a well-formed ring at 69 N, 31 E — the
      // same one for every invalid input. A book with one mistyped cell would render a
      // polygon in Russia and look like a rendering glitch rather than a data error.
      expect(() => cellToRing(bad)).toThrow(/not an H3 cell id/);
    },
  );
});

describe("ringsBounds", () => {
  it("covers every ring it is given", () => {
    const cells = [
      latLngToCell(49.1, -120.5, 8),
      latLngToCell(50.5, -118.6, 8),
      latLngToCell(49.9, -119.5, 8),
    ];
    const [west, south, east, north] = ringsBounds(cells.map(cellToRing));

    expect(west).toBeLessThan(-120.4);
    expect(east).toBeGreaterThan(-118.7);
    expect(south).toBeLessThan(49.2);
    expect(north).toBeGreaterThan(50.4);
  });

  it("refuses an empty set rather than returning an infinite box", () => {
    expect(() => ringsBounds([])).toThrow(/no rings/);
  });
});
