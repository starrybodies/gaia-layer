"""Burn severity.

This is the target variable, so an error here does not degrade the model, it redefines what
the model is being asked to predict and quietly invalidates the gate. The tests below drive
the compositing path with synthetic scenes rather than the network, which lets them assert
things a fixture cannot: that an undetected cloud cannot move the composite, that a scene
with no clear pixels contributes nothing, and that a missing pre-fire season produces no
severity rather than a plausible number.
"""

from __future__ import annotations

import numpy as np
import pytest
from rasterio.transform import Affine

from gaia_pipeline.eii.sources import landsat
from gaia_pipeline.eii.sources.landsat import (
    DNBR_MODERATE_HIGH,
    HIGH_SEVERITY_DNBR,
    _clear_mask,
    _composite_nbr,
    is_high_severity,
    severity_class,
)

SHAPE = (8, 8)
TRANSFORM = Affine(30.0, 0.0, 1_500_000.0, 0.0, -30.0, 600_000.0)


class FakeAsset:
    def __init__(self, href: str) -> None:
        self.href = href


class FakeItem:
    """A scene identified only by the arrays it should produce."""

    def __init__(self, name: str) -> None:
        self.id = name
        self.assets = {
            landsat.NIR_ASSET: FakeAsset(f"{name}:nir"),
            landsat.SWIR2_ASSET: FakeAsset(f"{name}:swir"),
            landsat.QA_ASSET: FakeAsset(f"{name}:qa"),
        }


def scene(nir: float, swir: float, qa: int = 0) -> dict[str, np.ndarray]:
    return {
        "nir": np.full(SHAPE, (nir - landsat.REFLECTANCE_OFFSET) / landsat.REFLECTANCE_SCALE),
        "swir": np.full(SHAPE, (swir - landsat.REFLECTANCE_OFFSET) / landsat.REFLECTANCE_SCALE),
        "qa": np.full(SHAPE, float(qa)),
    }


@pytest.fixture()
def reader(monkeypatch):
    """Serve synthetic arrays in place of remote assets, keyed by the fake href."""
    registry: dict[str, dict[str, np.ndarray]] = {}

    def fake_read(url, bounds, crs, transform, shape, *, resampling):
        name, band = url.split(":")
        return registry[name][band].astype("float32")

    monkeypatch.setattr(landsat, "_read_asset", fake_read)
    return registry


def composite(reader, scenes: dict[str, dict[str, np.ndarray]]):
    reader.update(scenes)
    items = [FakeItem(name) for name in scenes]
    return _composite_nbr(items, (0.0, 0.0, 240.0, 240.0), "EPSG:3005", TRANSFORM, SHAPE)


class TestQualityMask:
    @pytest.mark.parametrize("bit", [0, 1, 2, 3, 4, 5])
    def test_every_rejected_condition_is_rejected(self, bit: int) -> None:
        assert not _clear_mask(np.array([[float(1 << bit)]]))[0, 0]

    def test_a_clear_pixel_survives(self) -> None:
        assert _clear_mask(np.array([[0.0]]))[0, 0]

    def test_bits_above_the_rejected_range_are_ignored(self) -> None:
        """Bits 6 and up carry confidence levels, not verdicts."""
        assert _clear_mask(np.array([[float(1 << 6)]]))[0, 0]


class TestCompositing:
    def test_a_single_clear_scene_gives_its_own_nbr(self, reader) -> None:
        nbr, count = composite(reader, {"a": scene(nir=0.3, swir=0.1)})
        assert count == 1
        assert nbr[0, 0] == pytest.approx((0.3 - 0.1) / (0.3 + 0.1), abs=1e-4)

    def test_the_median_ignores_an_outlier_scene(self, reader) -> None:
        """The reason this is a median: one bad scene must not move the answer."""
        clean = (0.3, 0.1)
        nbr, count = composite(
            reader,
            {
                "a": scene(*clean),
                "b": scene(*clean),
                "c": scene(nir=0.05, swir=0.4),  # an unflagged cloud shadow
            },
        )
        assert count == 3
        assert nbr[0, 0] == pytest.approx((0.3 - 0.1) / (0.3 + 0.1), abs=1e-4)

    def test_a_flagged_scene_contributes_nothing(self, reader) -> None:
        nbr, count = composite(
            reader,
            {"a": scene(nir=0.3, swir=0.1), "b": scene(nir=0.9, swir=0.9, qa=1 << 3)},
        )
        assert count == 1
        assert nbr[0, 0] == pytest.approx(0.5, abs=1e-4)

    def test_no_clear_observations_yields_missing_not_zero(self, reader) -> None:
        nbr, count = composite(reader, {"a": scene(nir=0.3, swir=0.1, qa=1 << 3)})
        assert count == 0
        assert np.isnan(nbr).all()

    def test_no_scenes_at_all_yields_missing(self, reader) -> None:
        nbr, count = composite(reader, {})
        assert count == 0
        assert np.isnan(nbr).all()

    def test_an_unreadable_scene_does_not_lose_the_fire(self, reader, monkeypatch) -> None:
        """One 404 in a season is a scene lost, not a fire lost."""
        good = FakeItem("a")
        broken = FakeItem("b")
        reader["a"] = scene(nir=0.3, swir=0.1)

        original = landsat._read_asset

        def flaky(url, *args, **kwargs):
            if url.startswith("b:"):
                raise OSError("HTTP 404")
            return original(url, *args, **kwargs)

        monkeypatch.setattr(landsat, "_read_asset", flaky)

        nbr, count = _composite_nbr(
            [good, broken], (0.0, 0.0, 240.0, 240.0), "EPSG:3005", TRANSFORM, SHAPE
        )
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
