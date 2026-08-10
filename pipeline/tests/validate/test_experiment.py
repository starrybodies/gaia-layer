"""The experiment harness.

These tests are about the apparatus, not the hypothesis. On synthetic ground where the
signal is known to live in the structure columns, the harness must find it; on synthetic
ground where those columns are noise, it must not. A harness that cannot fail cannot pass
either, and the gate would be theatre.
"""

from __future__ import annotations

import numpy as np
import pytest

from gaia_pipeline.validate.experiment import (
    ATTRIBUTION_BASELINE,
    CANDIDATE,
    GATE_BASELINE,
    MODELS,
    columns_for,
    run_experiment,
)
from gaia_pipeline.validate.splits import spatial_folds

RNG = np.random.default_rng(4242)
N = 3000


def ground(*, structure_matters: bool, terrain_matters: bool = False):
    """Synthetic cells with a controllable source of signal."""
    x = RNG.uniform(0.0, 60_000.0, N) + 1_500_000.0
    y = RNG.uniform(0.0, 60_000.0, N) + 500_000.0

    weather = RNG.normal(size=N)
    fuel = RNG.integers(1, 8, size=N).astype(float)
    terrain = RNG.normal(size=N)
    structure = RNG.normal(size=N)

    latent = 0.8 * weather + 0.3 * (fuel > 4)
    if terrain_matters:
        latent = latent + 1.2 * terrain
    if structure_matters:
        latent = latent + 1.5 * structure

    probability = 1.0 / (1.0 + np.exp(-(latent - 1.0)))
    labels = (RNG.uniform(size=N) < probability).astype(int)

    table = {
        "fwi": weather,
        "bui": weather + RNG.normal(scale=0.3, size=N),
        "fbp_fuel_type": fuel,
        "elevation_m": terrain,
        "slope_deg": terrain + RNG.normal(scale=0.3, size=N),
        "z_canopy_height": structure,
        "a_score": structure + RNG.normal(scale=0.2, size=N),
    }
    return table, labels, x, y


@pytest.fixture(scope="module")
def folds():
    _, _, x, y = ground(structure_matters=False)
    return spatial_folds(x, y, n_folds=4, buffer_km=1.0, seed=0), x, y


class TestModelDefinitions:
    def test_the_candidate_sees_everything_the_baselines_do(self) -> None:
        for name, groups in MODELS.items():
            if name == CANDIDATE:
                continue
            assert set(groups) <= set(MODELS[CANDIDATE])

    def test_the_attribution_baseline_differs_from_the_candidate_only_by_structure(self) -> None:
        """The whole point of the fourth model: one variable group between it and candidate."""
        difference = set(MODELS[CANDIDATE]) - set(MODELS[ATTRIBUTION_BASELINE])
        assert difference == {"structure"}

    def test_the_gate_baseline_is_weather_and_fuel_as_specified(self) -> None:
        assert set(MODELS[GATE_BASELINE]) == {"weather", "fuel"}

    def test_only_declared_columns_reach_a_model(self) -> None:
        """A stray target or cell id in the table must not become a feature."""
        columns = columns_for(MODELS[CANDIDATE], ["fwi", "a_score", "h3", "high_severity"])
        assert columns == ["fwi", "a_score"]


class TestHarness:
    def test_it_finds_a_signal_that_is_there(self, folds) -> None:
        split, _, _ = folds
        table, labels, *_ = ground(structure_matters=True)

        result = run_experiment(table, labels, split, n_bootstrap=400)

        assert result.gate_delta is not None
        assert result.gate_delta.point > 0
        assert result.gate_delta.excludes_zero
        assert result.gate_passes

    def test_it_does_not_find_a_signal_that_is_not(self, folds) -> None:
        """The test that makes a pass mean something."""
        split, _, _ = folds
        table, labels, *_ = ground(structure_matters=False)

        result = run_experiment(table, labels, split, n_bootstrap=400)

        assert result.gate_delta is not None
        assert not result.gate_delta.excludes_zero
        assert not result.gate_passes

    def test_terrain_alone_cannot_pass_the_attribution_comparison(self, folds) -> None:
        """Where the fourth baseline earns its place.

        With signal in terrain and none in structure, the candidate beats the spec's
        baseline_3 because it can see terrain. The attribution comparison, which gives
        terrain to both sides, correctly reports nothing.
        """
        split, _, _ = folds
        table, labels, *_ = ground(structure_matters=False, terrain_matters=True)

        result = run_experiment(table, labels, split, n_bootstrap=400)

        assert result.gate_delta is not None
        assert result.attribution_delta is not None
        assert result.gate_delta.point > result.attribution_delta.point
        assert not result.attribution_delta.excludes_zero

    def test_every_model_is_scored_on_the_same_cells(self, folds) -> None:
        split, _, _ = folds
        table, labels, *_ = ground(structure_matters=True)

        result = run_experiment(table, labels, split, n_bootstrap=100)

        counts = {name: model.summary["n_scored"] for name, model in result.models.items()}
        assert len(set(counts.values())) == 1

    def test_cells_never_predicted_are_excluded_and_reported(self, folds) -> None:
        split, _, _ = folds
        table, labels, *_ = ground(structure_matters=True)

        result = run_experiment(table, labels, split, n_bootstrap=100)

        if int((~result.evaluated).sum()) > 0:
            assert any("never predicted" in note for note in result.notes)

    def test_an_empty_feature_table_is_refused(self, folds) -> None:
        split, _, _ = folds
        with pytest.raises(ValueError, match="no usable columns"):
            run_experiment({"h3": np.arange(N).astype(float)}, np.zeros(N, dtype=int), split)
