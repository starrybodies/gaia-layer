"""Component C: riparian extent, and the difference between none and unknown.

A cell with no stream in it has a riparian fraction of zero, and that zero is a measurement:
the Freshwater Atlas mapped this ground and found no water on it. A cell the Atlas does not
cover has no fraction at all. Those two must not arrive as the same number, and most of the
tests here are about keeping them apart.

The other half is the weighting. Riparian extent on its own says where water is, not whether
the corridor along it is intact, and a 30 m band of grass beside a creek is not the same
piece of ground as a 30 m band of cottonwood. So extent is weighted by how the vegetation
inside the band compares with the matrix around it, and the weight is pinned here at the
three points where it has to be right: taller than the matrix, the same as it, and shorter.
"""

from __future__ import annotations

import h3
import numpy as np
import pytest

from gaia_pipeline.eii.area import H3_RES
from gaia_pipeline.eii.components import riparian
from gaia_pipeline.eii.spine import Spine


@pytest.fixture(scope="module")
def spine(tmp_path_factory: pytest.TempPathFactory) -> Spine:
    """A ring over West Kelowna, the ground the case study is about."""
    centre = h3.latlng_to_cell(49.93, -119.57, H3_RES)
    return Spine.for_cells(sorted(h3.grid_disk(centre, 3)), tmp_path_factory.mktemp("riparian"))


def _band(spine: Spine, share: float) -> np.ndarray:
    """A horizontal band of riparian pixels covering roughly `share` of the grid."""
    mask = np.zeros(spine.grid.shape, dtype=bool)
    rows = int(spine.grid.height * share)
    mask[:rows, :] = True
    return mask


class TestVigour:
    def test_a_corridor_taller_than_its_matrix_weighs_full(self) -> None:
        weight = riparian.condition_weight(np.array([riparian.VIGOUR_SCALE_M]), np.array([0.0]))
        assert weight[0] == pytest.approx(1.0)

    def test_a_corridor_the_same_as_its_matrix_weighs_half(self) -> None:
        """No evidence either way about the corridor is not evidence that it is intact."""
        assert riparian.condition_weight(np.array([5.0]), np.array([5.0]))[0] == pytest.approx(0.5)

    def test_a_corridor_shorter_than_its_matrix_weighs_nothing(self) -> None:
        weight = riparian.condition_weight(np.array([0.0]), np.array([riparian.VIGOUR_SCALE_M]))
        assert weight[0] == pytest.approx(0.0)

    def test_it_cannot_leave_the_unit_interval(self) -> None:
        weight = riparian.condition_weight(np.array([80.0, 0.0]), np.array([0.0, 80.0]))
        assert (weight >= 0.0).all()
        assert (weight <= 1.0).all()

    def test_an_unmeasured_corridor_weighs_nothing_known(self) -> None:
        weight = riparian.condition_weight(np.array([np.nan]), np.array([12.0]))
        assert np.isnan(weight[0])


class TestComponentC:
    def test_a_cell_with_no_water_scores_zero_extent_not_missing(self, spine) -> None:
        """The Atlas looked and found nothing, which is a finding."""
        n = spine.n_cells
        table = riparian.component_c(
            spine,
            riparian_mask=np.zeros(spine.grid.shape, dtype=bool),
            canopy=np.full(spine.grid.shape, 10.0, dtype="float32"),
            strata=np.zeros(n, dtype="int64"),
            covered=np.ones(n, dtype=bool),
        )

        fraction = np.asarray(table.column("riparian_fraction"))
        assert np.isfinite(fraction).all()
        assert (fraction == 0.0).all()

    def test_a_cell_the_atlas_does_not_cover_scores_nothing(self, spine) -> None:
        n = spine.n_cells
        table = riparian.component_c(
            spine,
            riparian_mask=np.zeros(spine.grid.shape, dtype=bool),
            canopy=np.full(spine.grid.shape, 10.0, dtype="float32"),
            strata=np.zeros(n, dtype="int64"),
            covered=np.zeros(n, dtype=bool),
        )

        assert np.isnan(np.asarray(table.column("riparian_fraction"))).all()
        assert np.isnan(np.asarray(table.column("c_score"))).all()
        assert "uncovered" in table.column("flags")[0].as_py()

    def test_extent_is_the_share_of_the_cell_the_band_covers(self, spine) -> None:
        n = spine.n_cells
        table = riparian.component_c(
            spine,
            riparian_mask=np.ones(spine.grid.shape, dtype=bool),
            canopy=np.full(spine.grid.shape, 10.0, dtype="float32"),
            strata=np.zeros(n, dtype="int64"),
            covered=np.ones(n, dtype=bool),
        )

        assert np.allclose(np.asarray(table.column("riparian_fraction")), 1.0)

    def test_the_parts_survive_beside_the_combination(self, spine) -> None:
        """A model that disagrees with our weighting must be able to use the pieces."""
        n = spine.n_cells
        table = riparian.component_c(
            spine,
            riparian_mask=_band(spine, 0.5),
            canopy=np.full(spine.grid.shape, 10.0, dtype="float32"),
            strata=np.zeros(n, dtype="int64"),
            covered=np.ones(n, dtype=bool),
        )

        assert set(table.column_names) >= {
            "h3",
            "riparian_fraction",
            "riparian_canopy_m",
            "matrix_canopy_m",
            "riparian_vigour_m",
            "intactness",
            "c_score",
            "uncertainty",
            "flags",
        }

    def test_a_degraded_corridor_scores_above_an_intact_one(self, spine) -> None:
        """Positive is the fire-severe direction, which for riparian means degraded."""
        n = spine.n_cells
        rows = spine.grid.height

        mask = np.zeros(spine.grid.shape, dtype=bool)
        mask[: rows // 2, :] = True

        # Tall inside the band in the west half, short inside it in the east half.
        canopy = np.full(spine.grid.shape, 5.0, dtype="float32")
        canopy[: rows // 2, : spine.grid.width // 2] = 20.0
        canopy[: rows // 2, spine.grid.width // 2 :] = 1.0

        table = riparian.component_c(
            spine,
            riparian_mask=mask,
            canopy=canopy,
            strata=np.zeros(n, dtype="int64"),
            covered=np.ones(n, dtype=bool),
        )

        vigour = np.asarray(table.column("riparian_vigour_m"))
        score = np.asarray(table.column("c_score"))
        both = np.isfinite(vigour) & np.isfinite(score)

        assert both.sum() > 4
        # Where the corridor is taller than its matrix, the score is lower.
        assert np.corrcoef(vigour[both], score[both])[0, 1] < -0.5

    def test_the_sign_is_stated(self) -> None:
        assert riparian.SIGN == -1.0
        assert "degraded" in riparian.STRUCTURE_OF_THE_SIGN
