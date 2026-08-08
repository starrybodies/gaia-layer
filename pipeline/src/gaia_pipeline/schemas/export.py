"""Export the Pydantic schemas to JSON Schema.

Run via ``make schema``. Each exported model becomes one self-contained JSON Schema
document with its dependencies inlined under ``$defs``. Self-contained matters: the Zod
compiler on the other side resolves ``#/$defs`` references but has no notion of a document
that references a sibling file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from . import (
    AoiCoverage,
    ComparePeriodsRequest,
    ComparePeriodsResponse,
    ConfidenceBasis,
    CoverageRequest,
    CoverageResponse,
    EcologicalStateRequest,
    EcologicalStateResponse,
    ErrorResponse,
    IndicatorComparison,
    IndicatorCoverage,
    Method,
    NumericEnvelope,
    ProvenanceRequest,
    ProvenanceResponse,
    ProvenanceStep,
    RejectedValue,
    ResolvedGeometry,
    SourceRecord,
    SpatialStats,
    SubstrateComponent,
    SubstrateEnvelope,
    SubstrateScore,
    SubstrateScoreRequest,
    SubstrateScoreResponse,
    Trend,
    TrendEnvelope,
    ValidationFlag,
    ValidationReport,
)

# Every model that gets its own generated Zod schema. Ordering is alphabetical so the
# generated index is stable across runs.
EXPORTED: tuple[type[BaseModel], ...] = (
    AoiCoverage,
    ComparePeriodsRequest,
    ComparePeriodsResponse,
    ConfidenceBasis,
    CoverageRequest,
    CoverageResponse,
    EcologicalStateRequest,
    EcologicalStateResponse,
    ErrorResponse,
    IndicatorComparison,
    IndicatorCoverage,
    Method,
    NumericEnvelope,
    ProvenanceRequest,
    ProvenanceResponse,
    ProvenanceStep,
    RejectedValue,
    ResolvedGeometry,
    SourceRecord,
    SpatialStats,
    SubstrateComponent,
    SubstrateEnvelope,
    SubstrateScore,
    SubstrateScoreRequest,
    SubstrateScoreResponse,
    Trend,
    TrendEnvelope,
    ValidationFlag,
    ValidationReport,
)


def _to_kebab(name: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and not name[i - 1].isupper():
            out.append("-")
        out.append(ch.lower())
    return "".join(out)


def schema_for(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = model.__name__
    return schema


def export(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    index: dict[str, str] = {}
    for model in EXPORTED:
        path = out_dir / f"{_to_kebab(model.__name__)}.json"
        path.write_text(json.dumps(schema_for(model), indent=2, sort_keys=True) + "\n")
        index[model.__name__] = path.name
        written.append(path)

    index_path = out_dir / "index.json"
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    written.append(index_path)

    # Stale generated files are worse than missing ones — they typecheck.
    expected = {p.name for p in written}
    for existing in out_dir.glob("*.json"):
        if existing.name not in expected:
            existing.unlink()

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Pydantic schemas to JSON Schema.")
    parser.add_argument("--out", type=Path, required=True, help="Output directory.")
    args = parser.parse_args()
    written = export(args.out)
    print(f"wrote {len(written)} schema files to {args.out}")


if __name__ == "__main__":
    main()
