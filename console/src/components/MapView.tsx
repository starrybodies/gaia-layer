"use client";

/**
 * The map view.
 *
 * MapLibre with a raster basemap from Carto, so nothing here needs a Mapbox token. The
 * indicator layer is the 500 m cell grid the pipeline wrote; clicking a cell opens the
 * full envelope of the area value it was aggregated from, provenance and all.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { Map as MapLibreMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { api, ApiError, type Coverage, type NumericEnvelope } from "@/lib/api";
import {
  Citation,
  ConfidenceBar,
  EmptyState,
  FlagList,
  INDICATOR_CODES,
  label,
  Panel,
  ProvenanceChain,
  StatusDot,
} from "@/components/primitives";

const CELL_SOURCE = "gaia-cells";
const CELL_LAYER = "gaia-cells-fill";

/**
 * Diverging ramp, dry to wet.
 *
 * Ordered so that the fire-prone end is always the warm end regardless of which way the
 * indicator runs — NDMI falling and VPD rising both mean drier, and a reader should not
 * have to remember which is which.
 */
const RAMP = ["#7f3b2b", "#b45f3a", "#c9a227", "#6f8f5f", "#3f6f6f"] as const;

const DRYING_DIRECTION: Record<string, "up" | "down"> = {
  ndvi: "down",
  ndmi: "down",
  nbr: "down",
  vpd_kpa: "up",
  precip_30d_mm: "down",
  temp_max_c: "up",
  days_since_rain: "up",
  soil_moisture_0_7cm: "down",
  soil_moisture_7_28cm: "down",
  twi: "down",
};

const BASEMAP: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    carto: {
      type: "raster",
      tiles: [
        "https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}@2x.png",
        "https://b.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}@2x.png",
      ],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors © CARTO",
    },
  },
  layers: [{ id: "carto", type: "raster", source: "carto" }],
};

interface CellProperties {
  cell_id: string;
  reading: number | null;
  valid_fraction: number;
  confidence: number;
  parent_claim_id: string;
}

export function MapView({ coverage }: { coverage: Coverage }) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);

  const aoi = coverage.aois[0];
  const rasterIndicators = useMemo(
    () =>
      (aoi?.indicators ?? []).filter((i) =>
        ["ndvi", "ndmi", "nbr", "elevation_m", "slope_deg", "twi"].includes(i.indicator),
      ),
    [aoi],
  );

  const [indicator, setIndicator] = useState<string>("ndmi");
  const [periods, setPeriods] = useState<{ start: string; end: string }[]>([]);
  const [period, setPeriod] = useState<string>("");
  const [parent, setParent] = useState<NumericEnvelope | null>(null);
  const [selected, setSelected] = useState<CellProperties | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // --- map lifecycle ---------------------------------------------------------------
  useEffect(() => {
    if (container.current === null || map.current !== null || aoi === undefined) return;

    const instance = new maplibregl.Map({
      container: container.current,
      style: BASEMAP,
      bounds: [
        [aoi.bbox.west, aoi.bbox.south],
        [aoi.bbox.east, aoi.bbox.north],
      ],
      fitBoundsOptions: { padding: 24 },
      attributionControl: { compact: true },
    });
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    instance.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    map.current = instance;

    return () => {
      instance.remove();
      map.current = null;
    };
  }, [aoi]);

  useEffect(() => {
    if (aoi === undefined) return;
    api
      .periods(aoi.aoi_id)
      .then((list) => {
        // Terrain carries a sentinel period covering all time; it is not a timeline entry.
        const monthly = list.filter((p) => p.start >= "2000-02-01");
        setPeriods(monthly);
        setPeriod((current) => current || (monthly[monthly.length - 1]?.start ?? ""));
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [aoi]);

  // --- layer data ------------------------------------------------------------------
  const draw = useCallback(async () => {
    const instance = map.current;
    if (instance === null || aoi === undefined || period === "") return;

    setLoading(true);
    setError(null);
    try {
      const collection = await api.cells(aoi.aoi_id, indicator, period);
      setParent(collection.parent);

      const readings = collection.features
        .map((f) => f.properties.reading)
        .filter((v): v is number => v !== null)
        .sort((a, b) => a - b);
      if (readings.length === 0) throw new Error("no cells with data");

      // Percentile stops rather than min/max: a single outlier cell would otherwise
      // flatten the whole ramp into one colour.
      const at = (q: number): number => readings[Math.floor(q * (readings.length - 1))] ?? 0;
      const stops = [at(0.05), at(0.275), at(0.5), at(0.725), at(0.95)];
      const dry = DRYING_DIRECTION[indicator] ?? "down";
      const colours = dry === "down" ? [...RAMP] : [...RAMP].reverse();

      const paint: (string | number)[] = [];
      stops.forEach((stop, i) => {
        paint.push(stop, colours[i] as string);
      });

      const apply = (): void => {
        const existing = instance.getSource(CELL_SOURCE);
        if (existing !== undefined) {
          (existing as maplibregl.GeoJSONSource).setData(
            collection as unknown as maplibregl.GeoJSONSourceSpecification["data"],
          );
        } else {
          instance.addSource(CELL_SOURCE, {
            type: "geojson",
            data: collection as unknown as maplibregl.GeoJSONSourceSpecification["data"],
          });
          instance.addLayer({
            id: CELL_LAYER,
            type: "fill",
            source: CELL_SOURCE,
            paint: { "fill-color": "#000", "fill-opacity": 0.72, "fill-outline-color": "#0000" },
          });
          instance.on("click", CELL_LAYER, (event) => {
            const feature = event.features?.[0];
            if (feature !== undefined) {
              setSelected(feature.properties as unknown as CellProperties);
            }
          });
          instance.on("mouseenter", CELL_LAYER, () => {
            instance.getCanvas().style.cursor = "pointer";
          });
          instance.on("mouseleave", CELL_LAYER, () => {
            instance.getCanvas().style.cursor = "";
          });
        }
        instance.setPaintProperty(CELL_LAYER, "fill-color", [
          "interpolate",
          ["linear"],
          ["coalesce", ["get", "reading"], stops[0] ?? 0],
          ...paint,
        ] as unknown as maplibregl.ExpressionSpecification);
      };

      if (instance.isStyleLoaded()) apply();
      else instance.once("load", apply);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.message} ${e.detail ?? ""}` : String(e));
    } finally {
      setLoading(false);
    }
  }, [aoi, indicator, period]);

  useEffect(() => {
    void draw();
  }, [draw]);

  if (aoi === undefined) {
    return (
      <div className="p-8">
        <EmptyState
          title="No area of interest is ingested."
          detail="Run `make seed` to build the data lake, then reload."
        />
      </div>
    );
  }

  return (
    <div className="grid h-[calc(100vh-5.75rem)] grid-cols-1 lg:grid-cols-[1fr_26rem]">
      <div className="relative">
        <div ref={container} className="absolute inset-0" />

        <div className="pointer-events-none absolute inset-x-0 top-0 flex flex-wrap gap-2 p-3">
          <div className="pointer-events-auto flex flex-wrap gap-1 border border-base-800 bg-base-950/90 p-1 backdrop-blur">
            {rasterIndicators.map((item) => (
              <button
                key={item.indicator}
                type="button"
                onClick={() => setIndicator(item.indicator)}
                className={`numeric px-2.5 py-1 text-[11px] tracking-wider uppercase transition-colors ${
                  indicator === item.indicator
                    ? "bg-accent-500 text-base-950"
                    : "text-base-400 hover:text-base-100"
                }`}
              >
                {INDICATOR_CODES[item.indicator] ?? item.indicator}
              </button>
            ))}
          </div>

          {periods.length > 0 && (
            <div className="pointer-events-auto flex items-center gap-2 border border-base-800 bg-base-950/90 px-2 py-1 backdrop-blur">
              <label className="numeric text-[10px] tracking-wider text-base-500 uppercase">
                period
              </label>
              <select
                value={period}
                onChange={(e) => setPeriod(e.target.value)}
                className="numeric bg-transparent text-[11px] text-base-100 outline-none"
              >
                {periods.map((p) => (
                  <option key={p.start} value={p.start} className="bg-base-900">
                    {p.start.slice(0, 7)}
                  </option>
                ))}
              </select>
            </div>
          )}

          {loading && (
            <span className="numeric pointer-events-none border border-base-800 bg-base-950/90 px-2 py-1 text-[10px] tracking-wider text-accent-500 uppercase">
              loading
            </span>
          )}
        </div>

        {error !== null && (
          <div className="absolute inset-x-3 bottom-3 border border-status-rejected/50 bg-base-950/95 p-3">
            <p className="numeric text-[10px] tracking-wider text-status-rejected uppercase">
              layer error
            </p>
            <p className="mt-1 text-xs text-base-300">{error}</p>
          </div>
        )}
      </div>

      <aside className="overflow-y-auto border-t border-base-800 lg:border-t-0 lg:border-l">
        <div className="space-y-px">
          <Panel title="Area">
            <p className="text-sm text-base-200">{aoi.name}</p>
            <p className="numeric mt-1 text-[11px] text-base-500">
              {aoi.area_km2.toLocaleString(undefined, { maximumFractionDigits: 0 })} km² ·{" "}
              {aoi.analysis_crs} · {aoi.grid_resolution_m} m grid
            </p>
          </Panel>

          {selected !== null && (
            <Panel title="Selected cell">
              <div className="flex items-baseline justify-between">
                <span className="text-sm text-base-300">{label(indicator)}</span>
                <span className="numeric text-xl text-base-100">
                  {selected.reading === null ? "—" : selected.reading.toFixed(3)}
                </span>
              </div>
              <div className="mt-2 flex items-center gap-4">
                <ConfidenceBar value={selected.confidence} />
                <span className="numeric text-[11px] text-base-500">
                  {(selected.valid_fraction * 100).toFixed(0)}% of cell observed
                </span>
              </div>
              <p className="mt-3 text-[11px] leading-relaxed text-base-500">
                A cell reading is a 500 m aggregate of the area composite below. It is not an
                independent claim; its provenance is the composite&rsquo;s.
              </p>
            </Panel>
          )}

          {parent !== null && (
            <>
              <Panel title="Area value for this period">
                <div className="flex items-baseline justify-between">
                  <span className="text-sm text-base-300">{label(parent.indicator)}</span>
                  <span className="numeric text-2xl text-base-100">{parent.value.toFixed(3)}</span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-4">
                  <StatusDot status={parent.validation_status} />
                  <ConfidenceBar value={parent.confidence} />
                </div>
                <p className="numeric mt-2 text-[10px] break-all text-base-600">
                  {parent.claim_id}
                </p>
                <FlagList flags={parent.flags} />
              </Panel>

              <Panel title="Confidence basis">
                <ul className="space-y-2">
                  {parent.confidence_basis.components.map((component) => (
                    <li key={component.name}>
                      <div className="flex items-baseline justify-between gap-3">
                        <span className="numeric text-[11px] text-base-400">
                          {component.name.replace(/_/g, " ")}
                        </span>
                        <span className="numeric text-[11px] text-base-300">
                          {component.value.toFixed(2)}
                          <span className="text-base-600"> × {component.weight.toFixed(2)}</span>
                        </span>
                      </div>
                      <p className="mt-0.5 text-[10px] leading-relaxed text-base-600">
                        {component.description}
                      </p>
                    </li>
                  ))}
                </ul>
                <p className="numeric mt-3 border-t border-base-800 pt-2 text-[10px] text-base-600">
                  {parent.confidence_basis.aggregation}
                </p>
              </Panel>

              <Panel title="Provenance">
                <ProvenanceChain steps={parent.provenance} />
                <div className="mt-4">
                  <Citation method={parent.method} />
                </div>
              </Panel>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
