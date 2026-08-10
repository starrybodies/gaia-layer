"""Component A: structure deviation within a biogeoclimatic context.

The component's only claim is a comparative one — this stand against stands of its own kind —
so most of what follows is about the comparison rather than the arithmetic. That two cells
with identical structure in different contexts score differently is the component working;
that they score the same would mean the stratification is decoration over a study-area
z-score, which is a map of the moisture gradient wearing a different name.

The rest is refusal. A reference of six cells must announce itself, a reference with no spread
must not divide by zero, and a cell with nothing measured in it must come back with nothing
rather than with the zero that reads as perfectly average.

Nothing here touches the network. The z-score tests build their populations as arrays, and the
table tests use a spine over a ring of real cells so that the h3 column is real.
"""

from __future__ import annotations

import h3
import numpy as np
import pyarrow as pa
import pytest

from gaia_pipeline.eii.area import H3_RES
from gaia_pipeline.eii.components import structure
from gaia_pipeline.eii.components.structure import (
    DEGENERATE_REFERENCE,
    MINIMUM_REFERENCE_CELLS,
    NO_STRATUM,
    SIGN,
    SPARSE_REFERENCE,
    UNSTRATIFIED,
    component_a,
    reference_strata,
    zscore_within,
)
from gaia_pipeline.eii.spine import Spine

# A ring over West Kelowna, the same ground the case study is about. Radius 4 is 61 cells,
# which is enough to hold one populated stratum, one that is too small, and a few cells the
# BEC mapping does not place.
TOY_CENTRE = (49.863, -119.583)

POPULATED = 50
SPARSE = 6
UNPLACED = 5


@pytest.fixture(scope="module")
def spine(tmp_path_factory) -> Spine:
    centre = h3.latlng_to_cell(*TOY_CENTRE, H3_RES)
    ring = sorted(h3.grid_disk(centre, 4))
    assert len(ring) == POPULATED + SPARSE + UNPLACED
    return Spine.for_cells(ring, cache_dir=tmp_path_factory.mktemp("structure"))


def _strata(*sizes: int) -> np.ndarray:
    """Consecutive blocks of cells, one stratum each."""
    return np.repeat(np.arange(len(sizes)), sizes)


@pytest.fixture()
def inputs() -> dict[str, np.ndarray]:
    """Structure for the 61 cells of the spine, laid out so every case has a cell.

    Rows 0-49 are one well-populated stratum, rows 50-55 a stratum of six, rows 56-60 cells
    with no BEC unit. Row 1 has only a canopy height and row 2 has nothing at all.
    """
    total = POPULATED + SPARSE + UNPLACED

    canopy = np.concatenate(
        [
            np.linspace(8.0, 32.0, POPULATED),
            np.full(SPARSE, 26.0),
            np.linspace(10.0, 20.0, UNPLACED),
        ]
    )
    closure = np.concatenate(
        [
            np.linspace(10.0, 90.0, POPULATED),
            np.full(SPARSE, 70.0),
            np.linspace(20.0, 60.0, UNPLACED),
        ]
    )
    age = np.concatenate(
        [
            np.linspace(20.0, 220.0, POPULATED),
            np.full(SPARSE, 150.0),
            np.linspace(40.0, 90.0, UNPLACED),
        ]
    )

    closure[1] = np.nan  # row 1 keeps its height and loses the rest
    age[1] = np.nan
    canopy[2] = np.nan  # row 2 has no structure at all
    closure[2] = np.nan
    age[2] = np.nan

    bec = np.concatenate([np.full(POPULATED, 1.0), np.full(SPARSE, 2.0), np.full(UNPLACED, np.nan)])
    cover = np.full(total, 10.0)

    return {
        "canopy_height": canopy,
        "crown_closure": closure,
        "stand_age": age,
        "bec_codes": bec,
        "cover_codes": cover,
    }


class TestReferenceStrata:
    def test_the_stratum_is_the_bec_unit_crossed_with_the_cover_class(self) -> None:
        bec = np.array([1.0, 1.0, 2.0, 2.0])
        cover = np.array([10.0, 20.0, 10.0, 20.0])

        strata = reference_strata(bec, cover)

        assert len(set(strata.tolist())) == 4

    def test_the_same_bec_unit_under_two_cover_classes_is_two_strata(self) -> None:
        """Forest and shrubland in IDFxh1 are not the same population."""
        strata = reference_strata(np.array([1.0, 1.0]), np.array([10.0, 20.0]))
        assert strata[0] != strata[1]

    def test_the_same_pair_is_always_the_same_stratum(self) -> None:
        bec = np.array([3.0, 1.0, 3.0, 1.0])
        cover = np.array([10.0, 10.0, 10.0, 10.0])

        strata = reference_strata(bec, cover)

        assert strata[0] == strata[2]
        assert strata[1] == strata[3]
        assert strata[0] != strata[1]

    def test_a_cell_missing_either_half_has_no_stratum(self) -> None:
        bec = np.array([1.0, np.nan, 1.0])
        cover = np.array([10.0, 10.0, np.nan])

        strata = reference_strata(bec, cover)

        assert strata[0] >= 0
        assert strata[1] == NO_STRATUM
        assert strata[2] == NO_STRATUM


class TestZScoreWithin:
    def test_a_cell_at_its_stratum_mean_scores_exactly_zero(self) -> None:
        values = np.linspace(10.0, 30.0, 41)  # symmetric, so 20.0 is both a member and the mean
        strata = np.zeros(values.size, dtype="int64")

        z, _, flags = zscore_within(values, strata)

        middle = int(np.flatnonzero(values == 20.0)[0])
        assert z[middle] == 0.0
        assert flags[middle] == 0

    def test_a_populated_stratum_is_standardised_to_unit_spread(self) -> None:
        values = np.linspace(5.0, 45.0, 40)
        z, _, _ = zscore_within(values, np.zeros(40, dtype="int64"))

        assert np.mean(z) == pytest.approx(0.0, abs=1e-12)
        assert np.std(z) == pytest.approx(1.0)

    def test_shifting_one_stratums_reference_leaves_the_other_untouched(self) -> None:
        """The test that says the stratification is real rather than decorative."""
        values = np.concatenate([np.linspace(10.0, 30.0, 40), np.linspace(10.0, 30.0, 40)])
        strata = _strata(40, 40)
        probe = 40  # the first cell of the second stratum

        before, _, _ = zscore_within(values, strata)

        # Everything in the second stratum except the probe grows by five metres. The probe
        # itself does not move; what moves is what counts as normal around it.
        shifted = values.copy()
        shifted[41:] += 5.0
        after, _, _ = zscore_within(shifted, strata)

        assert after[probe] < before[probe] - 0.5
        assert np.array_equal(after[:40], before[:40])

    def test_identical_values_in_two_strata_score_differently(self) -> None:
        """The point of the whole component, in four lines."""
        tall = np.linspace(25.0, 45.0, 40)
        short = np.linspace(5.0, 25.0, 40)
        values = np.concatenate([tall, short])
        values[0] = 25.0
        values[40] = 25.0

        z, _, flags = zscore_within(values, _strata(40, 40))

        assert z[0] < 0.0 < z[40]
        assert flags[0] == 0
        assert flags[40] == 0

    def test_a_sparse_stratum_falls_back_to_the_global_reference(self) -> None:
        values = np.concatenate([np.linspace(10.0, 30.0, 40), np.full(SPARSE, 100.0)])
        strata = _strata(40, SPARSE)

        z, stratum_n, flags = zscore_within(values, strata)

        expected = (100.0 - np.mean(values)) / np.std(values)
        assert z[40] == pytest.approx(expected)
        assert bool(flags[40] & SPARSE_REFERENCE)
        assert stratum_n[40] == SPARSE
        assert not flags[:40].any()

    def test_the_sparse_threshold_is_the_named_minimum(self) -> None:
        strata = _strata(60, MINIMUM_REFERENCE_CELLS, MINIMUM_REFERENCE_CELLS - 1)
        values = np.linspace(1.0, 100.0, strata.size)

        _, _, flags = zscore_within(values, strata)

        assert not (flags[60 : 60 + MINIMUM_REFERENCE_CELLS] & SPARSE_REFERENCE).any()
        assert (flags[60 + MINIMUM_REFERENCE_CELLS :] & SPARSE_REFERENCE).all()

    def test_a_lower_minimum_lets_a_small_stratum_be_its_own_reference(self) -> None:
        values = np.concatenate([np.linspace(10.0, 30.0, 40), np.linspace(90.0, 110.0, SPARSE)])
        strata = _strata(40, SPARSE)

        z, _, flags = zscore_within(values, strata, minimum=SPARSE)

        assert flags[40] == 0
        assert abs(z[40]) < 2.0  # against its own kind, 90 m is unremarkable

    def test_a_zero_variance_stratum_scores_zero_rather_than_infinity(self) -> None:
        values = np.concatenate([np.linspace(10.0, 30.0, 40), np.full(35, 17.0)])
        strata = _strata(40, 35)

        z, _, flags = zscore_within(values, strata)

        assert np.isfinite(z).all()
        assert (z[40:] == 0.0).all()
        assert (flags[40:] & DEGENERATE_REFERENCE).all()

    def test_a_missing_value_stays_missing(self) -> None:
        values = np.linspace(10.0, 30.0, 40)
        values[7] = np.nan

        z, _, flags = zscore_within(values, np.zeros(40, dtype="int64"))

        assert np.isnan(z[7])  # and so, in particular, not the zero that reads as average
        assert flags[7] == 0
        assert np.isfinite(np.delete(z, 7)).all()

    def test_missing_values_are_kept_out_of_the_reference(self) -> None:
        values = np.linspace(10.0, 30.0, 41)
        with_gaps = values.copy()
        with_gaps[[0, 40]] = np.nan  # drop a symmetric pair: the mean must not move

        z, stratum_n, _ = zscore_within(values, np.zeros(41, dtype="int64"))
        gapped, gapped_n, _ = zscore_within(with_gaps, np.zeros(41, dtype="int64"))

        assert stratum_n[20] == 41
        assert gapped_n[20] == 39
        assert gapped[20] == pytest.approx(z[20], abs=1e-12)

    def test_an_unplaced_cell_is_scored_against_the_study_area_and_says_so(self) -> None:
        values = np.concatenate([np.linspace(10.0, 30.0, 40), [45.0]])
        strata = np.concatenate([np.zeros(40, dtype="int64"), [NO_STRATUM]])

        z, stratum_n, flags = zscore_within(values, strata)

        assert z[40] == pytest.approx((45.0 - np.mean(values)) / np.std(values))
        assert bool(flags[40] & UNSTRATIFIED)
        assert stratum_n[40] == 0

    def test_mismatched_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError):
            zscore_within(np.zeros(10), np.zeros(9, dtype="int64"))


class TestComponentA:
    def test_the_table_carries_one_row_per_cell_with_the_documented_columns(
        self, spine, inputs
    ) -> None:
        table = component_a(spine, **inputs)

        assert isinstance(table, pa.Table)
        assert table.num_rows == spine.n_cells
        assert table.column_names == [
            "h3",
            "z_canopy_height",
            "z_crown_closure",
            "z_stand_age",
            "a_score",
            "contributing_variables",
            "bec_stratum",
            "reference_n",
            "uncertainty",
            "flags",
        ]
        assert table.column("h3").to_pylist() == spine.cells.column("h3").to_pylist()

    def test_the_score_is_the_signed_mean_of_the_z_scores_present(self, spine, inputs) -> None:
        table = component_a(spine, **inputs)
        row = 0

        parts = [
            SIGN[name] * table.column(f"z_{name}")[row].as_py() for name in structure.VARIABLES
        ]

        assert table.column("contributing_variables")[row].as_py() == 3
        assert table.column("a_score")[row].as_py() == pytest.approx(sum(parts) / 3, rel=1e-5)

    def test_flipping_the_sign_constant_flips_the_score(self, spine, inputs, monkeypatch) -> None:
        """The direction is a hypothesis, so inverting it has to be one edit."""
        before = component_a(spine, **inputs).column("a_score").to_numpy(zero_copy_only=False)

        for name in structure.VARIABLES:
            monkeypatch.setitem(structure.SIGN, name, -1.0)
        after = component_a(spine, **inputs).column("a_score").to_numpy(zero_copy_only=False)

        scored = np.isfinite(before)
        assert scored.any()
        assert np.allclose(after[scored], -before[scored])

    def test_a_score_from_one_variable_is_marked_and_differs_from_one_from_three(
        self, spine, inputs
    ) -> None:
        table = component_a(spine, **inputs)
        one, three = 1, 0  # row 1 kept only its canopy height

        assert table.column("contributing_variables")[one].as_py() == 1
        assert table.column("contributing_variables")[three].as_py() == 3
        assert np.isnan(table.column("z_crown_closure")[one].as_py())
        assert table.column("a_score")[one].as_py() == pytest.approx(
            SIGN["canopy_height"] * table.column("z_canopy_height")[one].as_py(), rel=1e-5
        )
        assert table.column("a_score")[one].as_py() != table.column("a_score")[three].as_py()

    def test_a_cell_with_nothing_measured_scores_nothing(self, spine, inputs) -> None:
        table = component_a(spine, **inputs)
        empty = 2

        assert table.column("contributing_variables")[empty].as_py() == 0
        assert np.isnan(table.column("a_score")[empty].as_py())
        assert np.isnan(table.column("uncertainty")[empty].as_py())
        assert np.isnan(table.column("z_canopy_height")[empty].as_py())
        assert table.column("flags")[empty].as_py() == ""

    def test_missing_inputs_never_become_zero_scores(self, spine, inputs) -> None:
        blank = dict(inputs)
        blank["crown_closure"] = np.full(spine.n_cells, np.nan)

        table = component_a(spine, **blank)
        closure = table.column("z_crown_closure").to_numpy(zero_copy_only=False)

        assert np.isnan(closure).all()
        assert (table.column("contributing_variables").to_numpy() <= 2).all()

    def test_a_sparse_cell_is_flagged_and_less_certain_than_a_populated_one(
        self, spine, inputs
    ) -> None:
        table = component_a(spine, **inputs)
        sparse, populated = POPULATED, 0

        assert "sparse_reference" in table.column("flags")[sparse].as_py()
        assert table.column("flags")[populated].as_py() == ""
        assert table.column("reference_n")[sparse].as_py() == SPARSE
        assert table.column("reference_n")[populated].as_py() >= MINIMUM_REFERENCE_CELLS
        assert (
            table.column("uncertainty")[sparse].as_py()
            > table.column("uncertainty")[populated].as_py()
        )

    def test_an_unplaced_cell_says_it_could_not_be_placed(self, spine, inputs) -> None:
        table = component_a(spine, **inputs)
        unplaced = POPULATED + SPARSE

        assert table.column("bec_stratum")[unplaced].as_py() == NO_STRATUM
        assert "unstratified" in table.column("flags")[unplaced].as_py()
        assert table.column("reference_n")[unplaced].as_py() == 0

    def test_identical_structure_in_two_strata_scores_differently(self, spine, inputs) -> None:
        """Two cells, the same numbers, different neighbours. The component's whole reason."""
        wide = dict(inputs)
        for name in structure.VARIABLES:
            wide[name] = wide[name].copy()

        # Split the cells into two populated strata with different reference means, then plant
        # the same stand in each: 30 m canopy, 80 per cent closure, 200 years.
        half = spine.n_cells // 2
        bec = np.concatenate([np.full(half, 1.0), np.full(spine.n_cells - half, 2.0)])
        wide["bec_codes"] = bec
        wide["cover_codes"] = np.full(spine.n_cells, 10.0)
        wide["canopy_height"] = np.concatenate(
            [np.linspace(35.0, 50.0, half), np.linspace(4.0, 14.0, spine.n_cells - half)]
        )
        wide["crown_closure"] = np.concatenate(
            [np.linspace(85.0, 100.0, half), np.linspace(5.0, 40.0, spine.n_cells - half)]
        )
        wide["stand_age"] = np.concatenate(
            [np.linspace(230.0, 320.0, half), np.linspace(10.0, 90.0, spine.n_cells - half)]
        )
        for name, value in (("canopy_height", 30.0), ("crown_closure", 80.0), ("stand_age", 200.0)):
            wide[name][0] = value
            wide[name][half] = value

        table = component_a(spine, **wide)
        scores = table.column("a_score").to_numpy(zero_copy_only=False)

        assert table.column("flags")[0].as_py() == ""
        assert table.column("flags")[half].as_py() == ""
        assert scores[0] < 0.0 < scores[half]

    def test_inputs_of_the_wrong_length_are_refused(self, spine, inputs) -> None:
        wrong = dict(inputs)
        wrong["stand_age"] = np.zeros(spine.n_cells - 1)

        with pytest.raises(ValueError, match="stand_age"):
            component_a(spine, **wrong)


class TestMethod:
    def test_the_method_records_the_sign_as_a_hypothesis_and_names_both_papers(self) -> None:
        method = structure.STRUCTURE_METHOD

        assert "Parks" in method.citation
        assert "Whitman" in method.citation
        assert method.notes is not None
        assert "hypothesis" in method.notes
        assert method.formula is not None
        assert "SIGN" in method.formula

    def test_the_sign_covers_every_variable_it_combines(self) -> None:
        assert set(SIGN) == set(structure.VARIABLES)
