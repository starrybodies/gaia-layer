import { describe, expect, it } from "vitest";
import { assertProvenanced, findProvenanceViolations } from "./guards.js";

const provenance = [
  {
    index: 0,
    kind: "observation",
    description: "Sentinel-2 L2A scene read",
    processed_at: "2026-08-07T00:00:00Z",
    pipeline_version: "0.1.0",
    algorithm_version: "2026.08.07",
    spatial_ref: "EPSG:32610",
  },
];

const goodClaim = {
  claim_id: "clm_01J0000000000000000000000A",
  value: 0.41,
  unit: "index",
  confidence: 0.87,
  validation_status: "validated",
  provenance,
  method: { name: "NDMI", citation: "Gao 1996" },
};

describe("findProvenanceViolations", () => {
  it("passes a well-formed claim", () => {
    expect(findProvenanceViolations(goodClaim)).toEqual([]);
  });

  it("catches a value with no provenance key", () => {
    const { provenance: _dropped, ...bare } = goodClaim;
    const violations = findProvenanceViolations(bare);
    expect(violations).toHaveLength(1);
    expect(violations[0]?.reason).toContain("provenance");
  });

  it("catches an empty provenance chain", () => {
    const violations = findProvenanceViolations({ ...goodClaim, provenance: [] });
    expect(violations.map((v) => v.reason)).toContain(
      'carries "value" with an empty provenance chain',
    );
  });

  it("catches a value missing confidence, status or method", () => {
    const { confidence: _c, validation_status: _s, method: _m, ...stripped } = goodClaim;
    expect(findProvenanceViolations(stripped)).toHaveLength(3);
  });

  it("catches confidence outside [0, 1]", () => {
    const violations = findProvenanceViolations({ ...goodClaim, confidence: 1.4 });
    expect(violations[0]?.reason).toContain("outside [0, 1]");
  });

  it("descends into arrays and nested responses", () => {
    const { provenance: _dropped, ...bare } = goodClaim;
    const response = { indicators: [goodClaim, bare], summary: "…" };
    const violations = findProvenanceViolations(response);
    expect(violations).toHaveLength(1);
    expect(violations[0]?.path).toBe("$.indicators[1]");
  });

  it("does not treat opaque parameter bags as claims", () => {
    const step = {
      ...provenance[0],
      parameters: { value: 3, threshold: { value: 0.2 } },
    };
    const claim = { ...goodClaim, provenance: [step] };
    expect(findProvenanceViolations(claim)).toEqual([]);
  });

  it("does not treat confidence components as claims", () => {
    const claim = {
      ...goodClaim,
      confidence_basis: {
        observation_count: 4,
        spatial_coverage: 0.98,
        components: [{ name: "cloud", value: 0.9, weight: 1.0, description: "…" }],
      },
    };
    expect(findProvenanceViolations(claim)).toEqual([]);
  });

  it("finds violations nested inside a substrate score decomposition", () => {
    const { confidence: _c, ...componentRaw } = goodClaim;
    const payload = {
      score: {
        ...goodClaim,
        value: {
          score: 61.2,
          components: [{ indicator: "ndmi", raw: componentRaw }],
        },
      },
    };
    const violations = findProvenanceViolations(payload);
    expect(violations.some((v) => v.path.includes("raw"))).toBe(true);
  });
});

describe("assertProvenanced", () => {
  it("throws with every violation listed", () => {
    const { provenance: _dropped, method: _m, ...bare } = goodClaim;
    expect(() => assertProvenanced(bare)).toThrow(/provenance/);
    expect(() => assertProvenanced(bare)).toThrow(/method/);
  });

  it("stays quiet on clean payloads", () => {
    expect(() => assertProvenanced(goodClaim)).not.toThrow();
  });
});

describe("the archived-figure shape", () => {
  // The v0.2 archive and the diligence dossier store provenance by reference rather than
  // inlining a chain onto every row. A number carrying the three references is citable —
  // a reader can take the run id and reproduce it — so it satisfies the guard. A number
  // carrying neither shape still does not.
  const archived = {
    label: "Gate delta, AUC-PR",
    value: 0.141,
    display: "+0.1410",
    source: "validation.json#gate_delta",
    run_id: "run_01KZX4NMMJARJXV7GS71TPF2ZF",
    method_id: "eii.diligence_dossier",
    source_set_id: "set_78387a5dd5f0341a",
  };

  it("accepts a value that carries run, method and source set", () => {
    expect(findProvenanceViolations(archived)).toEqual([]);
  });

  it("still refuses a value carrying neither shape", () => {
    const { run_id, ...bare } = archived;
    void run_id;
    expect(findProvenanceViolations(bare).length).toBeGreaterThan(0);
  });

  it("does not accept the references when they are empty strings", () => {
    expect(
      findProvenanceViolations({ ...archived, source_set_id: "" }).length,
    ).toBeGreaterThan(0);
  });

  it("says both shapes were missing rather than only naming the claim shape", () => {
    const { method_id, ...bare } = archived;
    void method_id;
    expect(findProvenanceViolations(bare)[0]?.reason).toContain("archived-figure");
  });
});
