"""Burn severity from Sentinel-2.

This is the target variable, so an error here does not degrade the model, it redefines what
the model is being asked to predict and quietly invalidates the gate. The tests drive the
compositing path with synthetic scenes rather than the network, which lets them assert
things a fixture cannot: that an undetected cloud cannot move the composite, that a scene
with no clear pixels contributes nothing, and that the reflectance offset introduced with
processing baseline 04.00 is applied to the scenes that need it and only those.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from rasterio.transform import Affine

from gaia_pipeline.eii.sources import severity
from gaia_pipeline.eii.sources.severity import (
    BASELINE_0400_OFFSET,
    DNBR_MODERATE_HIGH,
    HIGH_SEVERITY_DNBR,
    REFLECTANCE_SCALE,
    SCL_REJECT,
    _clear_mask,
    _composite_nbr,
    _offset_for,
    is_high_severity,
    severity_class,
)

SHAPE = (8, 8)
TRANSFORM = Affine(20.0, 0.0, 1_500_000.0, 0.0, -20.0, 600_000.0)

#: Scene classification value 4 is vegetation: clear ground, and what most of a forest is.
CLEAR = 4


class FakeAsset:
    def __init__(self, href: str) -> None:
        self.href = href


class FakeItem:
    """A scene identified only by the arrays it should produce."""

    def __init__(self, name: str, *, baseline: float = 2.07, acquired: str = "2019-07-01") -> None:
        self.id = name
        self.datetime = datetime.fromisoformat(acquired).replace(tzinfo=UTC)
        self.properties = {"s2:processing_baseline": baseline, "eo:cloud_cover": 5.0}
        self.assets = {
            severity.NIR_ASSET: FakeAsset(f"{name}:nir"),
            severity.SWIR2_ASSET: FakeAsset(f"{name}:swir"),
            severity.SCL_ASSET: FakeAsset(f"{name}:scl"),
        }


def scene(
    nir: float, swir: float, scl: int = CLEAR, *, offset: float = 0.0
) -> dict[str, np.ndarray]:
    """Encode physical reflectances the way a product with this offset would store them."""
    return {
        "nir": np.full(SHAPE, nir / REFLECTANCE_SCALE - offset),
        "swir": np.full(SHAPE, swir / REFLECTANCE_SCALE - offset),
        "scl": np.full(SHAPE, float(scl)),
    }


@pytest.fixture()
def reader(monkeypatch):
    """Serve synthetic arrays in place of remote assets, keyed by the fake href."""
    registry: dict[str, dict[str, np.ndarray]] = {}

    def fake_read(url, crs, transform, shape, *, resampling):
        name, band = url.split(":")
        return registry[name][band].astype("float32")

    monkeypatch.setattr(severity, "_read_asset", fake_read)
    return registry


def composite(reader, scenes: dict[str, dict[str, np.ndarray]], items=None):
    reader.update(scenes)
    items = items or [FakeItem(name) for name in scenes]
    return _composite_nbr(items, "EPSG:3005", TRANSFORM, SHAPE)


def nbr_of(nir: float, swir: float) -> float:
    return (nir - swir) / (nir + swir)


class TestSceneClassification:
    @pytest.mark.parametrize("value", SCL_REJECT)
    def test_every_rejected_class_is_rejected(self, value: int) -> None:
        assert not _clear_mask(np.array([[float(value)]]))[0, 0]

    @pytest.mark.parametrize("value", [4, 5, 7])
    def test_clear_ground_classes_survive(self, value: int) -> None:
        """Vegetation, bare soil and unclassified are all ground we can measure."""
        assert _clear_mask(np.array([[float(value)]]))[0, 0]

    def test_water_is_excluded(self) -> None:
        """A lake has an NBR and it is not a severity."""
        assert not _clear_mask(np.array([[6.0]]))[0, 0]


class TestReflectanceOffset:
    def test_a_modern_scene_takes_the_offset(self) -> None:
        assert _offset_for(FakeItem("a", baseline=5.0)) == BASELINE_0400_OFFSET

    def test_an_older_scene_does_not(self) -> None:
        assert _offset_for(FakeItem("a", baseline=2.07)) == 0.0

    def test_the_acquisition_date_decides_when_the_baseline_is_missing(self) -> None:
        item = FakeItem("a", acquired="2023-07-01")
        item.properties = {}
        assert _offset_for(item) == BASELINE_0400_OFFSET

    def test_the_offset_actually_changes_nbr(self, reader) -> None:
        """NBR is a normalised difference, so a common additive offset does not cancel.

        This is the trap the offset handling exists for: reading a 2023 scene with the
        pre-2022 convention shifts every severity value rather than failing loudly.
        """
        physical = nbr_of(0.3, 0.1)

        modern = composite(
            reader,
            {"m": scene(nir=0.3, swir=0.1, offset=BASELINE_0400_OFFSET)},
            items=[FakeItem("m", baseline=5.0, acquired="2023-07-01")],
        )[0]
        misread = composite(
            reader,
            {"w": scene(nir=0.3, swir=0.1, offset=BASELINE_0400_OFFSET)},
            items=[FakeItem("w", baseline=2.07, acquired="2019-07-01")],
        )[0]

        assert modern[0, 0] == pytest.approx(physical, abs=1e-4)
        assert abs(misread[0, 0] - physical) > 0.01


class TestCompositing:
    def test_a_single_clear_scene_gives_its_own_nbr(self, reader) -> None:
        nbr, count = composite(reader, {"a": scene(nir=0.3, swir=0.1)})
        assert count == 1
        assert nbr[0, 0] == pytest.approx(nbr_of(0.3, 0.1), abs=1e-4)

    def test_the_median_ignores_an_outlier_scene(self, reader) -> None:
        """The reason this is a median: one bad scene must not move the answer."""
        nbr, count = composite(
            reader,
            {
                "a": scene(nir=0.3, swir=0.1),
                "b": scene(nir=0.3, swir=0.1),
                "c": scene(nir=0.05, swir=0.4),  # an unflagged cloud shadow
            },
        )
        assert count == 3
        assert nbr[0, 0] == pytest.approx(nbr_of(0.3, 0.1), abs=1e-4)

    def test_a_clouded_scene_contributes_nothing(self, reader) -> None:
        nbr, count = composite(
            reader,
            {"a": scene(nir=0.3, swir=0.1), "b": scene(nir=0.9, swir=0.9, scl=9)},
        )
        assert count == 1
        assert nbr[0, 0] == pytest.approx(nbr_of(0.3, 0.1), abs=1e-4)

    def test_no_clear_observations_yields_missing_not_zero(self, reader) -> None:
        nbr, count = composite(reader, {"a": scene(nir=0.3, swir=0.1, scl=9)})
        assert count == 0
        assert np.isnan(nbr).all()

    def test_no_scenes_at_all_yields_missing(self, reader) -> None:
        nbr, count = composite(reader, {})
        assert count == 0
        assert np.isnan(nbr).all()

    def test_an_unreadable_scene_does_not_lose_the_fire(self, reader, monkeypatch) -> None:
        """One 404 in a season is a scene lost, not a fire lost."""
        reader["a"] = scene(nir=0.3, swir=0.1)
        original = severity._read_asset

        def flaky(url, *args, **kwargs):
            if url.startswith("b:"):
                raise OSError("HTTP 404")
            return original(url, *args, **kwargs)

        monkeypatch.setattr(severity, "_read_asset", flaky)

        nbr, count = _composite_nbr([FakeItem("a"), FakeItem("b")], "EPSG:3005", TRANSFORM, SHAPE)
        assert count == 1
        assert np.isfinite(nbr).all()


class TestSeverityClasses:
    @pytest.mark.parametrize(
        ("dnbr", "expected"),
        [(50.0, 0.0), (150.0, 1.0), (300.0, 2.0), (500.0, 3.0), (800.0, 4.0)],
    )
    def test_key_and_benson_breakpoints(self, dnbr: float, expected: float) -> None:
        assert severity_class(np.array([dnbr]))[0] == expected

    def test_the_boundary_belongs_to_the_higher_class(self) -> None:
        assert severity_class(np.array([DNBR_MODERATE_HIGH]))[0] == 4.0

    def test_unknown_severity_stays_unknown(self) -> None:
        assert np.isnan(severity_class(np.array([np.nan]))[0])

    def test_the_training_label_is_the_high_class(self) -> None:
        assert is_high_severity(np.array([HIGH_SEVERITY_DNBR]))[0]
        assert not is_high_severity(np.array([HIGH_SEVERITY_DNBR - 1.0]))[0]

    def test_missing_severity_is_never_labelled_true(self) -> None:
        """A cell we could not measure is not a negative example; it is not an example."""
        assert not is_high_severity(np.array([np.nan]))[0]
