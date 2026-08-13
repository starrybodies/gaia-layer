"""The retrodiction, and the two ways it could be made to look better than it is.

The first is leakage: fitting on 2023 and then predicting 2023 produces a triumphant case
study and no information. The second is selection: reporting the communities the model got
right and quietly dropping the ones it missed. Both are easy to do by accident and neither
would be visible in the output, so both are pinned here.

The fixture is synthetic, because the properties under test are about the procedure rather
than about the Okanagan: a model that trains on future data will score suspiciously well on
any fixture, and a report that hides misses will hide them on any fixture too.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pyarrow as pa
import pytest

from gaia_pipeline.validate.retrodiction import retrodict

RNG = np.random.default_rng(7)


def _features(n_past: int = 600, n_fire: int = 120) -> pa.Table:
    """Labelled cells: several past fires, and the one being retrodicted."""
    total = n_past + n_fire

    signal = RNG.normal(size=total)
    labels = (signal + RNG.normal(scale=0.7, size=total) > 1.0).astype(int)

    years = np.concatenate([RNG.choice([2018, 2019, 2021], size=n_past), np.full(n_fire, 2023)])
    fire_ids = np.concatenate(
        [np.array([f"past-{index % 7}" for index in range(n_past)]), np.full(n_fire, "target")]
    )

    return pa.table(
        {
            "h3": pa.array([f"cell-{index:05d}" for index in range(total)], pa.string()),
            "fire_year": pa.array(years, pa.int32()),
            "fire_id": pa.array(fire_ids, pa.string()),
            "high_severity": pa.array(labels, pa.int8()),
            "dnbr": pa.array(400.0 + 300.0 * signal, pa.float32()),
            "ffmc": pa.array(80.0 + signal, pa.float32()),
            "dmc": pa.array(30.0 + signal, pa.float32()),
            "dc": pa.array(300.0 + 40.0 * signal, pa.float32()),
            "isi": pa.array(8.0 + signal, pa.float32()),
            "bui": pa.array(60.0 + 10.0 * signal, pa.float32()),
            "fwi": pa.array(20.0 + 5.0 * signal, pa.float32()),
            "vpd_kpa": pa.array(2.0 + 0.3 * signal, pa.float32()),
            "fbp_fuel_type": pa.array(RNG.choice([2.0, 7.0, 31.0], size=total), pa.float32()),
            "elevation_m": pa.array(600.0 + 200.0 * signal, pa.float32()),
            "slope_deg": pa.array(15.0 + 5.0 * signal, pa.float32()),
            "aspect_deg": pa.array(RNG.uniform(0, 360, size=total), pa.float32()),
            "heat_load": pa.array(0.8 + 0.05 * signal, pa.float32()),
            "z_canopy_height": pa.array(signal, pa.float32()),
            "z_crown_closure": pa.array(signal * 0.8, pa.float32()),
            "z_stand_age": pa.array(signal * 0.6, pa.float32()),
            "a_score": pa.array(signal * 0.9, pa.float32()),
        }
    )


@pytest.fixture(scope="module")
def features() -> pa.Table:
    return _features()


class TestTheHoldOut:
    def test_it_trains_only_on_earlier_years(self, features) -> None:
        """The whole exercise is worthless if the fire's own year is in the training set."""
        result = retrodict(features, fire_id="target", places={})

        assert 2023 not in result.trained_on_years
        assert result.trained_on_years == [2018, 2019, 2021]

    def test_it_predicts_every_cell_of_the_fire(self, features) -> None:
        result = retrodict(features, fire_id="target", places={})

        assert result.n_cells == 120

    def test_a_fire_with_no_cells_is_refused(self, features) -> None:
        with pytest.raises(ValueError, match="no cells found"):
            retrodict(features, fire_id="a-fire-that-did-not-happen", places={})

    def test_a_fire_with_no_history_behind_it_is_refused(self, features) -> None:
        with pytest.raises(ValueError, match="nothing to train on"):
            retrodict(features, fire_id="target", places={}, fire_year=2015)


class TestReportingTheMisses:
    def test_hits_and_misses_are_both_counted(self, features) -> None:
        result = retrodict(features, fire_id="target", places={})

        assert result.hits + result.misses == result.observed_severe_cells
        assert result.hits + result.false_alarms == result.flagged_cells

    def test_recall_and_precision_come_out_of_those_counts(self, features) -> None:
        result = retrodict(features, fire_id="target", places={})

        assert result.recall == pytest.approx(result.hits / (result.hits + result.misses))
        assert result.precision == pytest.approx(result.hits / (result.hits + result.false_alarms))

    def test_a_place_that_burned_unflagged_is_named_a_miss(self) -> None:
        """The word MISSED has to appear, because that is the sentence nobody wants to write."""
        from gaia_pipeline.validate.retrodiction import PlaceOutcome

        outcome = PlaceOutcome(
            name="Somewhere",
            h3="cell-1",
            lat=49.9,
            lon=-119.5,
            predicted=0.05,
            flagged=False,
            observed_high_severity=True,
            dnbr=900.0,
        )

        assert "MISSED" in outcome.verdict

    def test_a_place_with_no_label_at_all_says_why(self, features) -> None:
        """A lakeshore community at a fire's edge is exactly what the labelling drops."""
        result = retrodict(
            features,
            fire_id="target",
            places={"cell-does-not-exist": ("Nowhere", 49.9, -119.5)},
        )

        assert len(result.places) == 1
        assert result.places[0].predicted is None
        assert result.places[0].verdict == "not scored"
        assert any("no severity label at all" in note for note in result.notes)

    def test_a_place_labelled_under_another_fire_says_which(self, features) -> None:
        """Not asked and got wrong are different, and so are the two reasons for not asking."""
        cell = features.column("h3")[10].as_py()
        result = retrodict(features, fire_id="target", places={cell: ("Elsewhere", 49.9, -119.5)})

        assert result.places[0].verdict == "not scored"
        assert any("labelled under past-" in note for note in result.notes)

    def test_a_place_inside_the_perimeter_gets_a_verdict(self, features) -> None:
        cell = features.column("h3")[700].as_py()
        result = retrodict(features, fire_id="target", places={cell: ("Somewhere", 49.9, -119.5)})

        assert result.places[0].predicted is not None
        assert result.places[0].verdict != "not scored"


class TestTheThreshold:
    def test_the_flag_is_a_share_of_the_fire_stated_in_advance(self, features) -> None:
        result = retrodict(features, fire_id="target", places={}, flag_quantile=0.8)

        assert result.flagged_cells == pytest.approx(result.n_cells * 0.2, abs=2)

    def test_a_stricter_threshold_flags_fewer_cells(self, features) -> None:
        loose = retrodict(features, fire_id="target", places={}, flag_quantile=0.5)
        strict = retrodict(features, fire_id="target", places={}, flag_quantile=0.95)

        assert strict.flagged_cells < loose.flagged_cells


class TestTheContext:
    def test_structure_loss_is_labelled_as_context_and_not_a_target(self) -> None:
        from gaia_pipeline.validate.retrodiction import STRUCTURE_LOSS_CONTEXT

        assert STRUCTURE_LOSS_CONTEXT["structures_lost_reported_august_2023"] == 189
        assert STRUCTURE_LOSS_CONTEXT["structures_lost_revised_2025"] == 303
        assert "no claims" in STRUCTURE_LOSS_CONTEXT["caveat"].lower()
        assert "context" in STRUCTURE_LOSS_CONTEXT["caveat"].lower()

    def test_the_as_of_date_is_before_the_fire_started(self) -> None:
        from gaia_pipeline.validate.retrodiction import AS_OF

        assert date(2023, 8, 15) > AS_OF
