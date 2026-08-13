"""The dossier's job is to be unwelcome, and these tests are what stop it drifting friendly.

A diligence surface degrades in one direction. Nobody deletes a bad finding; the finding
gets moved below the good one, or its wording softens, or the artifact it depends on stops
carrying the field and the section quietly renders blank. All three are regressions and none
of them fails a type check.

So: the disclosures are ordered first and marked, the unflattering wording is *derived from
the comparison* rather than typed in — flip the inputs and the sentence has to flip with
them — a missing figure raises instead of blanking, and coverage is counted inside each
stratum dimension rather than pooled across them, which is the specific arithmetic that once
produced "covers 9,836 of 3,835 cells".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gaia_pipeline.validate import dossier
from gaia_pipeline.validate.dossier import MissingEvidenceError, build_dossier

ARCHIVE = Path(__file__).resolve().parents[3] / "data" / "eii"


def _delta(point: float) -> dict[str, Any]:
    return {"point": point, "low": point - 0.03, "high": point + 0.03, "excludes_zero": True}


def _model(name: str) -> dict[str, Any]:
    return {
        "features": ["weather", "fuel"],
        "auc_pr_overall": 0.2,
        "auc_roc_overall": 0.65,
        "brier_overall": 0.09,
        "calibration_expected_gap": 0.05,
        "calibration_max_gap": 0.5,
        "calibration_max_gap_count": 8.0,
    }


@pytest.fixture()
def validation() -> dict[str, Any]:
    return {
        "verdict": "PASS",
        "gate_statement": "Component A, added to baseline_3, produces a positive delta.",
        "gate_delta": _delta(0.1410),
        "attribution_delta": _delta(0.1580),
        "calibration_delta": _delta(0.0091),
        "folds": 5,
        "leakage": {"buffer_m": 3000.0, "holds": 1.0, "minimum_train_test_distance_m": 3000.1},
        "models": {"baseline_3_fwi_fbp": _model("b3"), "candidate_with_component_a": _model("c")},
        "excluded": {},
        "exclusions_unrecorded_for": [2015, 2016],
    }


@pytest.fixture()
def diagnostics() -> dict[str, Any]:
    return {
        "n_cells": 3835,
        "prevalence": 0.1064,
        "leave_one_fire_out": {
            "candidate_with_component_a": 0.2677,
            "baseline_3_fwi_fbp": 0.1039,
            "delta": 0.1638,
        },
        "groups": [
            {"name": "structure", "auc_pr_drop": 0.1580, "auc_pr_drop_sd": 0.0},
            {"name": "terrain", "auc_pr_drop": 0.0839, "auc_pr_drop_sd": 0.0},
            {"name": "fuel", "auc_pr_drop": -0.0040, "auc_pr_drop_sd": 0.0},
            {"name": "weather", "auc_pr_drop": -0.0046, "auc_pr_drop_sd": 0.0},
        ],
        "features": [
            {
                "name": "a_score",
                "group": "structure",
                "auc_pr_drop": -0.0030,
                "auc_pr_drop_sd": 0.003,
            },
            {
                "name": "z_canopy_height",
                "group": "structure",
                "auc_pr_drop": 0.0414,
                "auc_pr_drop_sd": 0.003,
            },
            {
                "name": "z_crown_closure",
                "group": "structure",
                "auc_pr_drop": 0.0232,
                "auc_pr_drop_sd": 0.004,
            },
            {
                "name": "elevation_m",
                "group": "terrain",
                "auc_pr_drop": 0.0914,
                "auc_pr_drop_sd": 0.009,
            },
        ],
        "strata": [
            {
                "stratum": "fire_id",
                "value": "2023_854",
                "n": 567,
                "positives": 186,
                "prevalence": 0.328,
                "auc_pr": 0.4589,
                "auc_pr_baseline": 0.2886,
                "scorable": True,
                "reason": None,
            },
            {
                "stratum": "fire_id",
                "value": "2021_1966",
                "n": 315,
                "positives": 1,
                "prevalence": 0.0032,
                "auc_pr": None,
                "auc_pr_baseline": None,
                "scorable": False,
                "reason": "1 positives, below the floor of 10",
            },
            {
                "stratum": "fire_year",
                "value": "2023",
                "n": 1052,
                "positives": 210,
                "prevalence": 0.1996,
                "auc_pr": 0.4341,
                "auc_pr_baseline": 0.2822,
                "scorable": True,
                "reason": None,
            },
            {
                "stratum": "fire_year",
                "value": "2022",
                "n": 100,
                "positives": 0,
                "prevalence": 0.0,
                "auc_pr": None,
                "auc_pr_baseline": None,
                "scorable": False,
                "reason": "0 positives, below the floor of 10",
            },
        ],
        "misses": {
            "threshold": 0.0687,
            "severe_cells": 408,
            "hit": 262,
            "missed": 146,
            "recall": 0.6422,
            "differences": {
                "isi": {"hit_mean": 5.5641, "missed_mean": 9.3506, "standardised_gap": 0.8659},
                "a_score": {
                    "hit_mean": 0.2873,
                    "missed_mean": -0.0059,
                    "standardised_gap": -0.4328,
                },
            },
            "most_different": ["isi", "a_score"],
        },
        "notes": ["35 of 42 fire_id strata could not be scored for want of positives"],
    }


@pytest.fixture()
def built(validation, diagnostics) -> dict[str, Any]:
    return build_dossier(
        validation,
        diagnostics,
        run_id="run_TEST",
        source_set_id="srcset_TEST",
        generated=datetime(2026, 8, 13, tzinfo=UTC),
    )


def _section(payload: dict[str, Any], name: str) -> dict[str, Any]:
    return next(section for section in payload["sections"] if section["id"] == name)


class TestProvenance:
    def test_the_payload_names_the_run_that_produced_it(self, built) -> None:
        assert built["run_id"] == "run_TEST"
        assert built["source_set_id"] == "srcset_TEST"
        assert built["method_id"] == dossier.METHOD.method_id
        assert built["method_version"] == dossier.DOSSIER_VERSION

    def test_every_headline_figure_names_the_artifact_it_came_from(self, built) -> None:
        """A number on this screen that cannot be traced to a file is a number nobody owns."""
        figures = [figure for section in built["sections"] for figure in section["figures"]]
        assert figures
        assert all(figure["source"] for figure in figures)
        assert all(".json#" in figure["source"] for figure in figures)

    def test_every_figure_can_be_cited_on_its_own(self) -> None:
        """The provenance guard checks figure by figure. "The ids are further up" is not a check."""
        payload = build_dossier(
            {
                "verdict": "PASS",
                "gate_statement": "s",
                "gate_delta": _delta(0.14),
                "attribution_delta": _delta(0.16),
                "calibration_delta": _delta(0.01),
                "folds": 5,
                "leakage": {
                    "buffer_m": 3000.0,
                    "holds": 1.0,
                    "minimum_train_test_distance_m": 3000.1,
                },
                "models": {"m": _model("m")},
                "excluded": {},
                "exclusions_unrecorded_for": [],
            },
            {
                "n_cells": 10,
                "prevalence": 0.1,
                "leave_one_fire_out": {
                    "candidate_with_component_a": 0.2,
                    "baseline_3_fwi_fbp": 0.05,
                    "delta": 0.15,
                },
                "groups": [{"name": "structure", "auc_pr_drop": 0.1, "auc_pr_drop_sd": 0.0}],
                "features": [
                    {
                        "name": "a_score",
                        "group": "structure",
                        "auc_pr_drop": -0.003,
                        "auc_pr_drop_sd": 0.001,
                    }
                ],
                "strata": [
                    {
                        "stratum": "fire_id",
                        "value": "x",
                        "n": 10,
                        "positives": 10,
                        "prevalence": 1.0,
                        "auc_pr": 0.5,
                        "auc_pr_baseline": 0.4,
                        "scorable": True,
                        "reason": None,
                    }
                ],
                "misses": {
                    "threshold": 0.1,
                    "severe_cells": 10,
                    "hit": 5,
                    "missed": 5,
                    "recall": 0.5,
                    "differences": {},
                    "most_different": [],
                },
                "notes": [],
            },
            run_id="run_X",
            source_set_id="set_X",
        )
        figures = [f for section in payload["sections"] for f in section["figures"]]

        assert figures
        for figure in figures:
            assert figure["run_id"] == "run_X"
            assert figure["source_set_id"] == "set_X"
            assert figure["method_id"] == dossier.METHOD.method_id

    def test_the_method_record_is_registrable(self) -> None:
        assert dossier.METHOD.method_id and dossier.METHOD.citation and dossier.METHOD.version

    def test_the_two_artifacts_are_recorded_as_sources(self, tmp_path: Path) -> None:
        sources = dossier.artifact_sources(tmp_path / "validation.json", tmp_path / "d.json")
        assert {source.access_route for source in sources} == {"local-artifact"}
        assert len({source.source_id for source in sources}) == 2


class TestTheDisclosuresComeFirst:
    def test_disclosure_sections_are_ordered_ahead_of_the_verdict(self, built) -> None:
        """Burying a finding below the good news is the failure mode, not deleting it."""
        order = [section["id"] for section in built["sections"]]
        assert order.index("cross_fire_skill") < order.index("verdict")
        assert order.index("cross_fire_skill") < order.index("split_comparison")

    def test_the_disclosures_are_marked_as_such(self, built) -> None:
        marked = {section["id"] for section in built["sections"] if section["disclosure"]}
        assert {"cross_fire_skill", "a_score_decomposition", "where_it_fails"} <= marked
        assert built["disclosure_count"] == len(marked)

    def test_every_disclosure_is_ahead_of_every_ordinary_section(self, built) -> None:
        flags = [section["disclosure"] for section in built["sections"]]
        assert flags == sorted(flags, reverse=True)


class TestTheCrossFireDisclosure:
    def test_it_says_the_baseline_is_at_the_floor(self, built) -> None:
        section = _section(built, "cross_fire_skill")

        assert "no demonstrated skill" in section["statement"]
        assert "0.1039" in section["statement"]
        assert "0.1064" in section["statement"]
        assert "floor, not as a peer" in section["statement"]

    def test_it_reports_the_no_skill_line_as_a_figure_of_its_own(self, built) -> None:
        section = _section(built, "cross_fire_skill")
        labels = [figure["label"] for figure in section["figures"]]

        assert any("no-skill" in label for label in labels)
        assert any(figure["value"] == pytest.approx(0.1064) for figure in section["figures"])

    def test_the_wording_is_derived_rather_than_typed(self, validation, diagnostics) -> None:
        """Give the baseline real cross-fire skill and the sentence has to change."""
        diagnostics["leave_one_fire_out"]["baseline_3_fwi_fbp"] = 0.30
        payload = build_dossier(validation, diagnostics, run_id="r", source_set_id="s")
        statement = _section(payload, "cross_fire_skill")["statement"]

        assert "clears the no-skill line" in statement
        assert "no demonstrated skill" not in statement

    def test_it_admits_the_two_splits_score_different_cells(self, built) -> None:
        assert (
            "not measured over the same set of cells"
            in _section(built, "cross_fire_skill")["caveat"]
        )


class TestTheAblationDoesNotSoftenTheBadRows:
    def test_it_names_the_groups_that_cost_nothing(self, built) -> None:
        statement = _section(built, "group_ablation")["statement"]

        assert "fuel" in statement and "weather" in statement
        assert "no measurable lift" in statement

    def test_it_states_the_range_restriction_that_explains_it(self, built) -> None:
        """Without this the row reads as 'fire weather does not matter', which is not the claim."""
        statement = _section(built, "group_ablation")["statement"]
        assert "narrow by construction" in statement

    def test_the_rows_are_ordered_worst_last(self, built) -> None:
        rows = _section(built, "group_ablation")["rows"]
        assert [row[0] for row in rows] == ["structure", "terrain", "fuel", "weather"]

    def test_a_table_where_everything_helps_says_nothing_unflattering(
        self, validation, diagnostics
    ) -> None:
        for group in diagnostics["groups"]:
            group["auc_pr_drop"] = abs(group["auc_pr_drop"]) + 0.01
        payload = build_dossier(validation, diagnostics, run_id="r", source_set_id="s")

        assert "no measurable lift" not in _section(payload, "group_ablation")["statement"]


class TestTheCompositeColumnDisclosure:
    def test_it_reports_that_the_composite_earns_nothing(self, built) -> None:
        section = _section(built, "a_score_decomposition")

        assert "-0.0030" in section["statement"]
        assert "adds nothing" in section["statement"]
        assert section["rows"][0][0] == "a_score"

    def test_it_carries_the_permutation_caveat(self, built) -> None:
        """The measure over-reports: a planted noise column scored 0.025 against a real 0.58."""
        caveat = _section(built, "a_score_decomposition")["caveat"]

        assert "what the model uses, not what carries signal" in caveat
        assert "0.025" in caveat and "0.58" in caveat

    def test_it_refuses_to_render_without_the_composite_entry(
        self, validation, diagnostics
    ) -> None:
        diagnostics["features"] = [
            row for row in diagnostics["features"] if row["name"] != "a_score"
        ]
        with pytest.raises(MissingEvidenceError, match="a_score"):
            build_dossier(validation, diagnostics, run_id="r", source_set_id="s")


class TestCoverage:
    def test_it_counts_inside_each_dimension_rather_than_pooling(self, built) -> None:
        """Pooling counts every cell once per dimension: the 9,836-of-3,835 bug."""
        section = _section(built, "coverage")
        for row in section["rows"]:
            _, scorable_strata, strata, cells_scorable, cells = row
            assert scorable_strata <= strata
            assert cells_scorable <= cells
            assert cells <= 3835

    def test_it_names_the_years_with_no_positives(self, built) -> None:
        statement = _section(built, "coverage")["statement"]
        assert "2022" in statement
        assert "contribute nothing" in statement

    def test_unscorable_strata_are_listed_with_the_reason(self, built) -> None:
        rows = _section(built, "per_stratum")["rows"]
        unscorable = [row for row in rows if row[5] == "unscorable"]

        assert unscorable
        assert all(row[7] for row in unscorable)

    def test_no_stratum_is_dropped(self, built, diagnostics) -> None:
        assert len(_section(built, "per_stratum")["rows"]) == len(diagnostics["strata"])


class TestWhereItFails:
    def test_it_leads_with_the_weather_concentration(self, built) -> None:
        statement = _section(built, "where_it_fails")["statement"]

        assert "9.35" in statement and "5.56" in statement
        assert "under-reserved" in statement

    def test_it_reports_recall_rather_than_only_the_hits(self, built) -> None:
        statement = _section(built, "where_it_fails")["statement"]
        assert "146" in statement and "0.642" in statement

    def test_the_threshold_is_stated_as_a_choice(self, built) -> None:
        assert "moves every number" in _section(built, "where_it_fails")["caveat"]


class TestExclusions:
    def test_it_says_which_years_have_no_exclusion_bookkeeping(self, built) -> None:
        statement = _section(built, "exclusions")["statement"]

        assert "2015" in statement and "2016" in statement
        assert "not a statement that nothing was dropped" in statement

    def test_the_buffer_is_reported_as_measured_not_as_configured(self, built) -> None:
        rows = dict((row[0], row[1]) for row in _section(built, "exclusions")["rows"])

        assert rows["buffer between train and test, m"] == 3000
        assert rows["measured minimum train-test distance, m"] >= 3000
        assert rows["leakage check holds"] == "yes"

    def test_a_failed_leakage_check_is_shouted(self, validation, diagnostics) -> None:
        validation["leakage"]["holds"] = 0.0
        payload = build_dossier(validation, diagnostics, run_id="r", source_set_id="s")
        rows = dict((row[0], row[1]) for row in _section(payload, "exclusions")["rows"])

        assert rows["leakage check holds"] == "NO"


class TestItRefusesRatherThanBlanks:
    @pytest.mark.parametrize(
        "path", [("gate_delta",), ("calibration_delta",), ("models",), ("leakage",)]
    )
    def test_a_missing_validation_figure_stops_the_build(
        self, validation, diagnostics, path
    ) -> None:
        validation.pop(path[0])
        with pytest.raises(MissingEvidenceError, match=path[0]):
            build_dossier(validation, diagnostics, run_id="r", source_set_id="s")

    @pytest.mark.parametrize("key", ["leave_one_fire_out", "groups", "strata", "misses"])
    def test_a_missing_diagnostic_stops_the_build(self, validation, diagnostics, key) -> None:
        diagnostics.pop(key)
        with pytest.raises(MissingEvidenceError, match=key):
            build_dossier(validation, diagnostics, run_id="r", source_set_id="s")

    def test_a_null_interval_is_not_treated_as_a_zero_interval(
        self, validation, diagnostics
    ) -> None:
        validation["gate_delta"] = None
        with pytest.raises(MissingEvidenceError):
            build_dossier(validation, diagnostics, run_id="r", source_set_id="s")


class TestAgainstTheRealArtifacts:
    """Built over the archive's own files, so a schema drift in either one fails here."""

    @pytest.fixture()
    def real(self) -> dict[str, Any]:
        validation = ARCHIVE / "validation.json"
        diagnostics = ARCHIVE / "diagnostics.json"
        if not (validation.exists() and diagnostics.exists()):
            pytest.skip("the archive artifacts are not built in this checkout")
        return build_dossier(
            json.loads(validation.read_text()),
            json.loads(diagnostics.read_text()),
            run_id="run_REAL",
            source_set_id="srcset_REAL",
        )

    def test_it_builds(self, real) -> None:
        assert real["sections"]
        assert real["verdict"] == "PASS"

    def test_the_real_baseline_is_below_the_real_prevalence(self, real) -> None:
        """The finding the whole disclosure exists for. If this ever stops being true, say so."""
        section = _section(real, "cross_fire_skill")
        by_label = {figure["label"]: figure["value"] for figure in section["figures"]}
        baseline = next(value for label, value in by_label.items() if "baseline_3" in label)
        prevalence = next(value for label, value in by_label.items() if "no-skill" in label)

        assert baseline < prevalence
        assert "no demonstrated skill" in section["statement"]

    def test_every_section_carries_a_statement(self, real) -> None:
        assert all(section["statement"] for section in real["sections"])

    def test_it_is_json_serialisable(self, real) -> None:
        assert json.loads(json.dumps(real))["run_id"] == "run_REAL"
