"""The composite, whose only job is to combine honestly.

Two failures matter here and both are about absence. Filling a missing component with zero
would put the strongest possible claim of ordinariness where there is no measurement at all,
and it would do it silently, because zero is a perfectly ordinary value on a departure scale.
And scoring a cell on one component without saying so would let an underwriter compare a
five-component composite against a single reading as though they were the same kind of
number.
"""

from __future__ import annotations

import h3
import numpy as np
import pytest

from gaia_pipeline.eii.area import H3_RES
from gaia_pipeline.eii.components import composite
from gaia_pipeline.eii.spine import Spine


@pytest.fixture(scope="module")
def spine(tmp_path_factory: pytest.TempPathFactory) -> Spine:
    centre = h3.latlng_to_cell(49.9, -119.5, H3_RES)
    return Spine.for_cells(sorted(h3.grid_disk(centre, 1)), tmp_path_factory.mktemp("composite"))


def _all(spine: Spine, value: float) -> dict[str, np.ndarray]:
    return {name: np.full(spine.n_cells, value) for name in composite.COMPONENTS}


class TestCompose:
    def test_equal_components_average_to_themselves(self, spine) -> None:
        table = composite.compose(spine, scores=_all(spine, 1.5))

        assert np.allclose(np.asarray(table.column("eii")), 1.5)
        assert (np.asarray(table.column("contributing_components")) == 5).all()

    def test_every_component_survives_beside_the_index(self, spine) -> None:
        table = composite.compose(spine, scores=_all(spine, 0.5))

        assert set(table.column_names) >= set(composite.COMPONENTS) | {"eii", "uncertainty"}

    def test_a_missing_component_is_not_filled_with_zero(self, spine) -> None:
        """Zero is the middle of a departure scale, which is a claim, not a gap."""
        scores = _all(spine, 2.0)
        scores["c_riparian"] = np.full(spine.n_cells, np.nan)

        table = composite.compose(spine, scores=scores)

        assert np.allclose(np.asarray(table.column("eii")), 2.0)
        assert (np.asarray(table.column("contributing_components")) == 4).all()

    def test_a_cell_on_too_few_components_says_so(self, spine) -> None:
        scores = _all(spine, 2.0)
        for name in ("b_water", "c_riparian", "d_moisture"):
            scores[name] = np.full(spine.n_cells, np.nan)

        table = composite.compose(spine, scores=scores)

        assert (np.asarray(table.column("contributing_components")) == 2).all()
        assert all("thin_index" in value.as_py() for value in table.column("flags"))

    def test_a_cell_with_no_components_scores_nothing(self, spine) -> None:
        table = composite.compose(spine, scores=_all(spine, np.nan))

        index = np.asarray(table.column("eii"))
        assert np.isnan(index).all()
        assert not (index == 0.0).any()
        assert all("no_components" in value.as_py() for value in table.column("flags"))

    def test_a_component_that_was_never_built_is_simply_absent(self, spine) -> None:
        """Not built and not measurable are different, and only one is a fact about the cell."""
        scores = {"a_structure": np.full(spine.n_cells, 1.0)}

        table = composite.compose(spine, scores=scores)

        assert "b_water" not in table.column_names
        assert np.allclose(np.asarray(table.column("eii")), 1.0)

    def test_composing_nothing_is_refused(self, spine) -> None:
        with pytest.raises(ValueError, match="no components"):
            composite.compose(spine, scores={})

    def test_a_thin_index_carries_wider_doubt(self, spine) -> None:
        scores = _all(spine, 1.0)
        doubt = {name: np.full(spine.n_cells, 0.5) for name in composite.COMPONENTS}

        full = composite.compose(spine, scores=scores, uncertainties=doubt)

        thin_scores = dict(scores)
        for name in ("b_water", "c_riparian", "d_moisture"):
            thin_scores[name] = np.full(spine.n_cells, np.nan)
        thin = composite.compose(spine, scores=thin_scores, uncertainties=doubt)

        assert np.asarray(thin.column("uncertainty"))[0] > np.asarray(full.column("uncertainty"))[0]


class TestTheWeighting:
    def test_the_weights_are_equal_and_sum_to_one(self) -> None:
        assert len(set(composite.WEIGHTS.values())) == 1
        assert sum(composite.WEIGHTS.values()) == pytest.approx(1.0)

    def test_the_method_says_the_weights_are_an_admission(self) -> None:
        """If this ever becomes a fitted blend, the note has to change with it."""
        assert "equal" in composite.COMPOSITE_METHOD.notes.lower()
        assert "Component A" in composite.COMPOSITE_METHOD.notes

    def test_the_orientation_is_recorded_where_a_reader_will_find_it(self) -> None:
        assert "positive" in composite.COMPOSITE_METHOD.notes.lower()
