"""The archive and the catalog.

Provenance by reference is a storage optimisation that must not become an honesty
regression. The tests that matter here are the ones proving a value can still be traced all
the way back: which method, which sources, which access route, retrieved when.
"""

from __future__ import annotations

from datetime import date

import pyarrow as pa
import pytest

from gaia_pipeline.eii import archive
from gaia_pipeline.eii.archive import (
    FACT_COLUMNS,
    MethodRecord,
    SourceRecord,
    finish_run,
    open_catalog,
    read_component,
    record_constraint_event,
    register_method,
    register_sources,
    resolve_provenance,
    start_run,
    write_cells,
    write_component,
)

METHOD = MethodRecord(
    method_id="structure_deviation_v1",
    name="Vegetation structure deviation",
    citation="Parks et al. (2018). Environ. Res. Lett. 13:044037.",
    version="1",
    formula="z = (x - mu_bec_cover) / sigma_bec_cover",
)

SOURCES = [
    SourceRecord(
        dataset="GLAD forest height",
        version="2019",
        access_route="https-mosaic",
        uri="https://glad.umd.edu/Potapov/Forest_height_2019/Forest_height_2019_NAM.tif",
        citation="Potapov et al. (2021). Remote Sens. Environ. 253:112165.",
        native_resolution_m=30.0,
        native_timestep="single epoch",
    ),
    SourceRecord(
        dataset="BC VRI",
        version="R1 2024",
        access_route="bcgw-wfs",
        uri="https://openmaps.gov.bc.ca/geo/pub/wfs",
        citation="British Columbia Vegetation Resources Inventory.",
        native_resolution_m=None,
        native_timestep="annual",
    ),
]


@pytest.fixture()
def catalog(tmp_path):
    conn = open_catalog(tmp_path / "catalog.duckdb")
    yield conn
    conn.close()


def facts(run_id: str, method_id: str, source_set_id: str, n: int = 3) -> pa.Table:
    return pa.table(
        {
            "h3": pa.array([f"88{i:014x}" for i in range(n)], pa.string()),
            "period_start": pa.array([date(2023, 1, 1)] * n, pa.date32()),
            "period_end": pa.array([date(2023, 12, 31)] * n, pa.date32()),
            "component": pa.array(["structure"] * n, pa.string()),
            "value": pa.array([0.1 * i for i in range(n)], pa.float32()),
            "uncertainty_type": pa.array(["sd"] * n, pa.string()),
            "uncertainty_value": pa.array([0.05] * n, pa.float32()),
            "valid_fraction": pa.array([1.0] * n, pa.float32()),
            "method_id": pa.array([method_id] * n, pa.string()),
            "run_id": pa.array([run_id] * n, pa.string()),
            "source_set_id": pa.array([source_set_id] * n, pa.string()),
            "constraint_flags": pa.array([""] * n, pa.string()),
        }
    )


class TestProvenanceByReference:
    def test_the_chain_survives_the_round_trip(self, catalog) -> None:
        method_id = register_method(catalog, METHOD)
        set_id = register_sources(catalog, SOURCES)
        run_id = start_run(
            catalog,
            command="component-a",
            component="structure",
            method_id=method_id,
            source_set_id=set_id,
            parameters={"year": 2023},
        )
        finish_run(catalog, run_id, status="ok")

        chain = resolve_provenance(catalog, run_id)

        assert chain["method"]["citation"] == METHOD.citation
        assert {source["dataset"] for source in chain["sources"]} == {
            "GLAD forest height",
            "BC VRI",
        }
        assert all(source["access_route"] for source in chain["sources"])
        assert all(source["retrieved"] is not None for source in chain["sources"])
        assert chain["parameters"] == {"year": 2023}
        assert chain["status"] == "ok"

    def test_prov_o_names_what_the_value_came_from(self, catalog) -> None:
        set_id = register_sources(catalog, SOURCES)
        run_id = start_run(catalog, command="component-a", source_set_id=set_id)

        chain = resolve_provenance(catalog, run_id)

        assert chain["prov_o"]["wasGeneratedBy"] == run_id
        assert len(chain["prov_o"]["wasDerivedFrom"]) == 2

    def test_the_same_sources_produce_the_same_set_id(self, catalog) -> None:
        """Re-running an ingest must not fork the provenance graph."""
        first = register_sources(catalog, SOURCES)
        second = register_sources(catalog, list(reversed(SOURCES)))
        assert first == second

    def test_a_source_set_cannot_be_empty(self, catalog) -> None:
        with pytest.raises(ValueError, match="no sources"):
            register_sources(catalog, [])

    def test_an_unknown_run_cannot_be_resolved(self, catalog) -> None:
        with pytest.raises(KeyError):
            resolve_provenance(catalog, "run_nope")


class TestPartitions:
    def test_values_read_back_identical(self, catalog, tmp_path) -> None:
        method_id = register_method(catalog, METHOD)
        set_id = register_sources(catalog, SOURCES)
        run_id = start_run(
            catalog, command="component-a", method_id=method_id, source_set_id=set_id
        )

        written = write_component(
            catalog,
            tmp_path / "eii",
            component="structure",
            year=2023,
            facts=facts(run_id, method_id, set_id),
            run_id=run_id,
        )
        back = read_component(catalog, tmp_path / "eii", component="structure", year=2023)

        assert written == 3
        assert back.num_rows == 3
        assert back.column("value").to_pylist() == pytest.approx([0.0, 0.1, 0.2], abs=1e-6)
        assert set(back.column_names) == set(FACT_COLUMNS)

    def test_rewriting_a_year_replaces_it(self, catalog, tmp_path) -> None:
        """A rebuild leaves the year as the rebuild found it, not as a union of attempts."""
        method_id = register_method(catalog, METHOD)
        set_id = register_sources(catalog, SOURCES)
        run_id = start_run(
            catalog, command="component-a", method_id=method_id, source_set_id=set_id
        )

        write_component(
            catalog,
            tmp_path / "eii",
            component="structure",
            year=2023,
            facts=facts(run_id, method_id, set_id, n=5),
            run_id=run_id,
        )
        write_component(
            catalog,
            tmp_path / "eii",
            component="structure",
            year=2023,
            facts=facts(run_id, method_id, set_id, n=2),
            run_id=run_id,
        )

        assert (
            read_component(catalog, tmp_path / "eii", component="structure", year=2023).num_rows
            == 2
        )

    def test_facts_must_be_attributable_to_a_run(self, catalog, tmp_path) -> None:
        with pytest.raises(ValueError, match="unknown run_id"):
            write_component(
                catalog,
                tmp_path / "eii",
                component="structure",
                year=2023,
                facts=facts("run_ghost", "m", "s"),
                run_id="run_ghost",
            )

    def test_missing_columns_are_refused(self, catalog, tmp_path) -> None:
        run_id = start_run(catalog, command="component-a")
        incomplete = facts(run_id, "m", "s").drop_columns(["uncertainty_value"])

        with pytest.raises(ValueError, match="missing required columns"):
            write_component(
                catalog,
                tmp_path / "eii",
                component="structure",
                year=2023,
                facts=incomplete,
                run_id=run_id,
            )

    def test_reading_an_absent_component_returns_an_empty_table_not_an_error(
        self, catalog, tmp_path
    ) -> None:
        table = read_component(catalog, tmp_path / "eii", component="nothing", year=2023)
        assert table.num_rows == 0
        assert set(table.column_names) == set(FACT_COLUMNS)


class TestCellsAndEvents:
    def test_cells_are_persisted_for_id_resolution(self, catalog) -> None:
        cells = pa.table(
            {
                "h3": pa.array(["8828308281fffff"], pa.string()),
                "res": pa.array([8], pa.uint8()),
                "parent_h3": pa.array(["872830828ffffff"], pa.string()),
                "lat": pa.array([49.86], pa.float64()),
                "lon": pa.array([-119.58], pa.float64()),
                "area_km2": pa.array([0.737], pa.float32()),
            }
        )
        assert write_cells(catalog, cells) == 1
        assert catalog.execute("SELECT count(*) FROM h3_cell").fetchone()[0] == 1

    def test_a_fired_rule_is_written_down(self, catalog) -> None:
        run_id = start_run(catalog, command="constraints")
        record_constraint_event(
            catalog,
            run_id=run_id,
            h3="8828308281fffff",
            period_start=date(2023, 8, 1),
            rule="monotonicity:dc",
            outcome="clamped",
            detail="predicted severity fell as DC rose",
        )
        row = catalog.execute("SELECT rule, outcome, detail FROM constraint_event").fetchone()
        assert row == ("monotonicity:dc", "clamped", "predicted severity fell as DC rose")


class TestNonFiniteValuesDoNotReachTheArchive:
    """NaN and NULL both mean unmeasured, and carrying two spellings gives three answers.

    The 2023 Component A partition held 460 NaNs where it meant NULL. A reader counting
    `value IS NOT NULL` scored them; a reader sorting on value put them wherever its
    collation happened to; JSON serialised them to `null` anyway. One boundary, one spelling.
    """

    def _facts(self, values: list[float]) -> pa.Table:
        n = len(values)
        return pa.table(
            {
                "h3": pa.array([f"8812d0232{index}fffff" for index in range(n)]),
                "period_start": pa.array([date(2023, 1, 1)] * n, pa.date32()),
                "period_end": pa.array([date(2023, 8, 14)] * n, pa.date32()),
                "component": pa.array(["eii"] * n),
                "value": pa.array(values, pa.float32()),
                "uncertainty_type": pa.array(["standard_error"] * n),
                "uncertainty_value": pa.array([float("nan")] * n, pa.float32()),
                "valid_fraction": pa.array([1.0] * n, pa.float32()),
                "method_id": pa.array(["m"] * n),
                "run_id": pa.array(["r"] * n),
                "source_set_id": pa.array(["s"] * n),
                "constraint_flags": pa.array([""] * n),
            }
        )

    def test_a_nan_is_written_as_null(self) -> None:
        table = archive._null_out_non_finite(
            self._facts([1.0, float("nan"), 2.0, float("inf"), -float("inf")])
        )
        values = table.column("value").to_pylist()

        assert values[0] == pytest.approx(1.0)
        assert values[1] is None
        assert values[2] == pytest.approx(2.0)
        assert values[3] is None and values[4] is None

    def test_it_reaches_every_float_column_not_only_the_value(self) -> None:
        table = archive._null_out_non_finite(self._facts([1.0, 2.0]))
        assert table.column("uncertainty_value").to_pylist() == [None, None]

    def test_a_finite_table_is_left_exactly_as_it_was(self) -> None:
        facts = self._facts([1.0, 2.0])
        facts = facts.set_column(
            facts.column_names.index("uncertainty_value"),
            "uncertainty_value",
            pa.array([0.1, 0.2], pa.float32()),
        )
        assert archive._null_out_non_finite(facts).equals(facts)

    def test_the_string_columns_are_untouched(self) -> None:
        table = archive._null_out_non_finite(self._facts([float("nan")]))
        assert table.column("h3").to_pylist() == ["8812d02320fffff"]
        assert table.column("component").to_pylist() == ["eii"]
