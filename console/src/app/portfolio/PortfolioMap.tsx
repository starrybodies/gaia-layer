"use client";

/**
 * The book on a map.
 *
 * A ranked table answers "which cells". It does not answer "where", and where is the
 * question an underwriter looking at an accumulation actually has: four hundred cells spread
 * across a valley and four hundred cells stacked on one interface are the same table and a
 * very different exposure.
 *
 * Two rules carry over from the rest of the surface.
 *
 * **Nothing here computes a statistic.** The colour breaks are quantiles of values the
 * pipeline persisted, which is a rendering decision about the same numbers, and the cell
 * outlines are the geometric identity of a cell id rather than a measurement. No value on
 * this map was worked out in the browser.
 *
 * **Unmeasured cells are drawn, not omitted.** A map that simply leaves them out shows a
 * portfolio with no holes in it, which is the most misleading picture available. They render
 * in flat grey with a dashed edge, so a gap in coverage looks like a gap.
 *
 * MapLibre with a Carto raster basemap, so nothing here needs a token — the same choice the
 * v0.1 map view made and for the same reason.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { type Map as MapLibreMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { cellToRing, ringsBounds } from "@gaia/core";
import type { CellChange, RankedCell } from "@/lib/api";

const SOURCE = "book-cells";
const FILL = "book-cells-fill";
const LINE = "book-cells-line";

/**
 * Warm at the fire-prone end, in both modes.
 *
 * The index runs so that higher is the direction associated with more severe fire, and a
 * change runs so that positive is a move in that direction. One ramp reads correctly for
 * both, which matters because the mode toggle is one click away and a reader should not have
 * to re-learn the legend.
 */
const RAMP = ["#00c8e0", "#00e87b", "#c8e6c9", "#f0a830", "#e0623c"] as const;

/** Measured nothing. Not a low value — a different kind of thing, and drawn like one. */
const UNMEASURED_COLOUR = "#4b5563";

interface Feature {
  type: "Feature";
  id: number;
  geometry: { type: "Polygon"; coordinates: number[][][] };
  properties: {
    h3: string;
    reading: number | null;
    rank: number | null;
    label: string;
    detail: string;
  };
}

/** Quantile breaks over the values present, so a skewed book still shows structure. */
function breaks(values: number[]): number[] {
  if (values.length === 0) return [];
  const sorted = [...values].sort((a, b) => a - b);
  const at = (fraction: number) =>
    sorted[Math.min(sorted.length - 1, Math.floor(fraction * sorted.length))] as number;
  const candidates = [at(0.2), at(0.4), at(0.6), at(0.8)];
  // Strictly increasing, or MapLibre's step expression refuses the layer outright.
  return candidates.filter((value, index) => index === 0 || value > (candidates[index - 1] as number));
}

function toFeatures(
  cells: RankedCell[] | CellChange[],
  mode: "index" | "change",
): Feature[] {
  return cells.map((cell, index) => {
    // `cellToRing` lives in core: it does the [lat, lng] to [lng, lat] swap, closes the
    // ring, and refuses an id h3-js would otherwise answer with a hexagon in the Arctic.
    const ring = cellToRing(cell.h3);

    const reading =
      mode === "index" ? ((cell as RankedCell).value ?? null) : ((cell as CellChange).change ?? null);
    const rank = mode === "index" ? ((cell as RankedCell).rank ?? null) : null;

    const detail =
      mode === "index"
        ? reading === null
          ? "no measurement in the archive for this cell"
          : `index ${reading.toFixed(3)}${rank === null ? "" : ` · rank ${rank}`}`
        : reading === null
          ? "scored in only one of the two periods, so there is no change to report"
          : `${reading >= 0 ? "+" : ""}${reading.toFixed(3)} · ` +
            `${(cell as CellChange).before?.toFixed(2) ?? "—"} → ` +
            `${(cell as CellChange).after?.toFixed(2) ?? "—"}`;

    return {
      type: "Feature" as const,
      id: index,
      geometry: { type: "Polygon" as const, coordinates: [ring] },
      properties: { h3: cell.h3, reading, rank, label: cell.h3, detail },
    };
  });
}

export function PortfolioMap({
  cells,
  mode,
}: {
  cells: RankedCell[] | CellChange[];
  mode: "index" | "change";
}) {
  const container = useRef<HTMLDivElement | null>(null);
  const map = useRef<MapLibreMap | null>(null);
  const [selected, setSelected] = useState<Feature["properties"] | null>(null);

  const features = useMemo(() => toFeatures(cells, mode), [cells, mode]);
  const stops = useMemo(
    () =>
      breaks(
        features
          .map((feature) => feature.properties.reading)
          .filter((value): value is number => value !== null),
      ),
    [features],
  );

  useEffect(() => {
    if (container.current === null || map.current !== null) return;
    const instance = new maplibregl.Map({
      container: container.current,
      style: {
        version: 8,
        sources: {
          carto: {
            type: "raster",
            tiles: [
              "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png",
              "https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png",
            ],
            tileSize: 256,
            attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
          },
        },
        layers: [{ id: "carto", type: "raster", source: "carto" }],
      },
      center: [-119.5, 49.85],
      zoom: 8,
      attributionControl: { compact: true },
    });
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.current = instance;
    return () => {
      instance.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = map.current;
    if (instance === null || features.length === 0) return;

    const paint = () => {
      const data = { type: "FeatureCollection" as const, features };
      const existing = instance.getSource(SOURCE);
      if (existing !== undefined) {
        (existing as maplibregl.GeoJSONSource).setData(data);
      } else {
        instance.addSource(SOURCE, { type: "geojson", data });
      }

      const colour: unknown =
        stops.length === 0
          ? RAMP[2]
          : ["step", ["get", "reading"], RAMP[0], ...stops.flatMap((stop, i) => [stop, RAMP[i + 1]])];

      if (instance.getLayer(FILL) === undefined) {
        instance.addLayer({
          id: FILL,
          type: "fill",
          source: SOURCE,
          paint: {
            "fill-color": [
              "case",
              ["==", ["get", "reading"], null],
              UNMEASURED_COLOUR,
              colour,
            ] as never,
            "fill-opacity": ["case", ["==", ["get", "reading"], null], 0.35, 0.72] as never,
          },
        });
        instance.addLayer({
          id: LINE,
          type: "line",
          source: SOURCE,
          paint: { "line-color": "#0b0f14", "line-width": 0.4 },
        });
        instance.on("click", FILL, (event) => {
          const feature = event.features?.[0];
          if (feature !== undefined) {
            setSelected(feature.properties as unknown as Feature["properties"]);
          }
        });
        instance.on("mouseenter", FILL, () => {
          instance.getCanvas().style.cursor = "pointer";
        });
        instance.on("mouseleave", FILL, () => {
          instance.getCanvas().style.cursor = "";
        });
      } else {
        instance.setPaintProperty(FILL, "fill-color", [
          "case",
          ["==", ["get", "reading"], null],
          UNMEASURED_COLOUR,
          colour,
        ] as never);
      }

      const [west, south, east, north] = ringsBounds(
        features.map((feature) => feature.geometry.coordinates[0] as [number, number][]),
      );
      instance.fitBounds([west, south, east, north], { padding: 40, duration: 0 });
    };

    if (instance.isStyleLoaded()) paint();
    else instance.once("load", paint);
  }, [features, stops]);

  const unmeasured = features.filter((feature) => feature.properties.reading === null).length;

  return (
    <div className="border-line bg-surface border">
      <div ref={container} className="h-[26rem] w-full" />
      <div className="border-line flex flex-wrap items-center justify-between gap-4 border-t px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="eyebrow text-faint">
            {mode === "index" ? "index" : "change"} · lower
          </span>
          <span className="flex">
            {RAMP.map((colour) => (
              <span key={colour} className="block h-2 w-8" style={{ background: colour }} />
            ))}
          </span>
          <span className="eyebrow text-faint">higher</span>
          {unmeasured > 0 && (
            <span className="ml-4 flex items-center gap-2">
              <span
                className="block h-2 w-8 opacity-40"
                style={{ background: UNMEASURED_COLOUR }}
              />
              <span className="eyebrow text-faint">{unmeasured} unmeasured</span>
            </span>
          )}
        </div>
        <p className="numeric text-faint text-[10px]">
          {selected === null
            ? "click a cell"
            : `${selected.h3} · ${selected.detail}`}
        </p>
      </div>
      <p className="text-faint border-line border-t px-4 py-2 text-[11px] leading-relaxed">
        Colour breaks are quantiles of the values the archive holds for this book, so they
        describe this book and not the region. Cells with no measurement are drawn in grey
        rather than left out: a map that omits them shows a portfolio with no holes in it.
      </p>
    </div>
  );
}
