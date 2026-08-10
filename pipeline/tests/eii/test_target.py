"""The severity label.

An error here does not degrade the model, it changes the question. So these tests are about
what gets refused: half-burned cells, unmeasured cells, and fires with too little imagery
behind them must not become labels, because each of them would quietly teach the model
something false.
"""

from __future__ import annotations

import h3
import numpy as np
import pyarrow as pa
import pytest
from rasterio.transform import Affine

from gaia_pipeline.eii import target
from gaia_pipeline.eii.area import H3_RES
from gaia_pipeline.eii.sources.landsat import HIGH_SEVERITY_DNBR, SeverityWindow
from gaia_pipeline.eii.spine import Spine
from gaia_pipeline.eii.target import (
    MINIMUM_SCENES,
    merge_reburns,
    severity_labels,
)

CENTRE = (49.863, -119.583)


@pytest.fixture(scope="module")
def spine(tmp_path_factory):
    ring = sorted(h3.grid_disk(h3.latlng_to_cell(*CENTRE, H3_RES), 1))
    return Spine.for_cells(ring, cache_dir=tmp_path_factory.mktemp("target"))


class FakePerimeter:
    def __init__(self, fire_id: str, geometry=None) -> None:
        self.fire_id = fire_id
        self.geometry = geometry
        self.year = 2023
        self.area_ha = 1000.0


def window_for(spine, value: float, *, pre: int = 4, post: int = 4) -> SeverityWindow:
    """A severity patch aligned to the spine's own grid, filled with one value."""
    surface = np.full(spine.grid.shape, value, dtype="float32")
    return SeverityWindow(
        dnbr=surface,
        rbr=surface / 1000.0,
        nbr_pre=np.full(spine.grid.shape, 0.5, dtype="float32"),
        nbr_post=np.full(spine.grid.shape, 0.1, dtype="float32"),
        transform=Affine(
            spine.grid.resolution_m,
            0.0,
            spine.grid.left,
            0.0,
            -spine.grid.resolution_m,
            spine.grid.top,
        ),
        crs=spine.grid.crs,
        observations_pre=pre,
        observations_post=post,
    )


@pytest.fixture()
def wiring(monkeypatch, spine):
    """Replace the two network-facing calls with controllable stand-ins."""
    state = {
        "burned": np.ones(spine.n_cells, dtype="float32"),
        "dnbr": 800.0,
        "pre": 4,
        "post": 4,
        "perimeters": [FakePerimeter("2023_834")],
    }

    monkeypatch.setattr(
        target.nbac, "perimeters", lambda year, within=None: (state["perimeters"], _source())
    )
    monkeypatch.setattr(target.nbac, "burned_fraction", lambda perimeters, sp: state["burned"])
    monkeypatch.setattr(
        target.landsat,
        "severity_for_bounds",
        lambda bounds, year, **kwargs: (
            window_for(spine, state["dnbr"], pre=state["pre"], post=state["post"]),
            [],
        ),
    )
    return state


def _source():
    from gaia_pipeline.eii.archive import SourceRecord

    return SourceRecord(
        dataset="NBAC",
        version="test",
        access_route="fixture",
        uri="fixture://nbac",
        citation="test",
    )


class TestLabelling:
    def test_a_fully_burned_severely_burned_cell_is_labelled_true(self, spine, wiring) -> None:
        labels = severity_labels(spine, (2023,))

        assert labels.table.num_rows == spine.n_cells
        assert all(labels.table.column("high_severity").to_pylist())
        assert labels.prevalence == 1.0

    def test_a_fully_burned_lightly_burned_cell_is_labelled_false(self, spine, wiring) -> None:
        wiring["dnbr"] = HIGH_SEVERITY_DNBR - 200.0
        labels = severity_labels(spine, (2023,))

        assert labels.table.num_rows == spine.n_cells
        assert not any(labels.table.column("high_severity").to_pylist())

    def test_the_threshold_boundary_counts_as_high(self, spine, wiring) -> None:
        wiring["dnbr"] = HIGH_SEVERITY_DNBR
        assert all(severity_labels(spine, (2023,)).table.column("high_severity").to_pylist())


class TestRefusals:
    def test_a_half_burned_cell_is_dropped_not_labelled(self, spine, wiring) -> None:
        """Severity averaged over burned and unburned ground describes nothing."""
        wiring["burned"] = np.full(spine.n_cells, 0.3, dtype="float32")

        labels = severity_labels(spine, (2023,))

        assert labels.table.num_rows == 0
        assert labels.excluded["partly_burned_cells"] == spine.n_cells

    def test_a_cell_just_over_the_line_is_kept(self, spine, wiring) -> None:
        wiring["burned"] = np.full(spine.n_cells, 0.51, dtype="float32")
        assert severity_labels(spine, (2023,)).table.num_rows == spine.n_cells

    def test_a_fire_without_enough_scenes_produces_no_labels(self, spine, wiring) -> None:
        """Missing severity is not low severity."""
        wiring["pre"] = MINIMUM_SCENES - 1

        labels = severity_labels(spine, (2023,))

        assert labels.table.num_rows == 0
        assert labels.excluded["fires_without_imagery"] == 1

    def test_an_unmeasurable_cell_is_dropped_not_called_negative(self, spine, wiring) -> None:
        wiring["dnbr"] = float("nan")

        labels = severity_labels(spine, (2023,))

        assert labels.table.num_rows == 0
        assert labels.excluded["unmeasured_cells"] == spine.n_cells

    def test_a_year_with_no_fires_is_not_an_error(self, spine, wiring) -> None:
        wiring["perimeters"] = []
        labels = severity_labels(spine, (2023,))

        assert labels.table.num_rows == 0
        assert labels.excluded["fires_outside_area"] == 1

    def test_every_exclusion_is_counted(self, spine, wiring) -> None:
        """A silent drop is the failure mode this whole structure exists to prevent."""
        labels = severity_labels(spine, (2023,))
        assert set(labels.excluded) == {
            "partly_burned_cells",
            "unmeasured_cells",
            "fires_without_imagery",
            "fires_outside_area",
        }


class TestReburns:
    def test_a_second_fire_on_the_same_cell_is_flagged(self, spine, wiring) -> None:
        wiring["perimeters"] = [FakePerimeter("2023_834"), FakePerimeter("2023_900")]

        labels = severity_labels(spine, (2023,))
        reburn = labels.table.column("reburn").to_pylist()

        assert labels.table.num_rows == spine.n_cells * 2
        assert sum(reburn) == spine.n_cells

    def test_merging_keeps_the_worst_fire_not_the_last(self) -> None:
        table = pa.table(
            {
                "h3": pa.array(["a", "a", "b"], pa.string()),
                "fire_year": pa.array([2017, 2021, 2018], pa.int16()),
                "fire_id": pa.array(["x", "y", "z"], pa.string()),
                "dnbr": pa.array([900.0, 200.0, 500.0], pa.float32()),
                "burned_fraction": pa.array([1.0, 1.0, 1.0], pa.float32()),
                "measured_fraction": pa.array([1.0, 1.0, 1.0], pa.float32()),
                "high_severity": pa.array([True, False, False], pa.bool_()),
                "reburn": pa.array([False, True, False], pa.bool_()),
            }
        )

        merged = merge_reburns(table)

        assert merged.num_rows == 2
        kept = dict(
            zip(merged.column("h3").to_pylist(), merged.column("dnbr").to_pylist(), strict=True)
        )
        assert kept["a"] == pytest.approx(900.0)

    def test_merging_an_empty_table_is_not_an_error(self, spine, wiring) -> None:
        wiring["perimeters"] = []
        assert merge_reburns(severity_labels(spine, (2023,)).table).num_rows == 0
