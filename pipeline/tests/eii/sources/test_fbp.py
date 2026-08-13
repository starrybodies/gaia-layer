"""FBP fuel types, and the one thing a categorical layer can do wrong.

A fuel-type grid has no continuous values to be slightly off by. It fails in exactly one
way: a code comes out the far end that was never in the source, because something averaged
or interpolated on the way through. That failure is silent — every invented code is a real
fuel type with a real name — and it would land in the baseline the whole validation
experiment is measured against.

So the tests here are conservation tests. Nothing may appear that the source did not
contain, at the pixel step and again at the cell step, and anything the grid does not cover
must arrive missing rather than as a plausible number.

The fixture is a 40 x 40 km window of the real 100 m grid over West Kelowna, held in the
source's own projection so the reads exercise the reprojection path.
"""

from __future__ import annotations

from pathlib import Path

import h3
import numpy as np
import pytest
import rasterio

from gaia_pipeline.eii.area import H3_RES
from gaia_pipeline.eii.sources import fbp
from gaia_pipeline.eii.spine import Spine

FIXTURE = Path(__file__).parent.parent / "fixtures" / "fbp" / "fbp-fueltypes-kelowna-100m.tif"

#: West Kelowna, comfortably inside the fixture window.
TOY_CENTRE = (49.863, -119.583)

#: On the fixture's top edge, so a spine here half hangs off the raster. The fixture is a
#: rectangle in the source's Lambert projection, which is a rotated quadrilateral in
#: lat/lon: at this longitude the edge sits at about 50.052, not at a round number.
EDGE_CENTRE = (50.052, -119.583)


def _spine(centre: tuple[float, float], cache_dir: Path) -> Spine:
    ring = sorted(h3.grid_disk(h3.latlng_to_cell(*centre, H3_RES), 1))
    return Spine.for_cells(ring, cache_dir=cache_dir)


@pytest.fixture(scope="module")
def source_codes() -> set[int]:
    """Every code in the fixture, read straight off the file."""
    with rasterio.open(FIXTURE) as src:
        window = src.read(1)
        return {int(code) for code in np.unique(window) if code != src.nodata}


@pytest.fixture(scope="module")
def spine(tmp_path_factory: pytest.TempPathFactory) -> Spine:
    return _spine(TOY_CENTRE, tmp_path_factory.mktemp("fbp"))


@pytest.fixture(scope="module")
def fetched(spine: Spine) -> tuple[np.ndarray, object]:
    """`fetch` against the fixture instead of the network."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(fbp, "FBP_URL", str(FIXTURE))
        return fbp.fetch(spine)


class TestLegend:
    def test_the_published_codes_are_all_present(self) -> None:
        """Spot checks against the NRCan colour classification the mapping came from."""
        assert fbp.FUEL_CLASSES[7].startswith("C-7")
        assert fbp.FUEL_CLASSES[13].startswith("D-1/D-2")
        assert fbp.FUEL_CLASSES[31].startswith("O-1a")
        assert fbp.FUEL_CLASSES[101] == "Non-fuel"
        assert fbp.FUEL_CLASSES[102] == "Water"

    def test_mixture_codes_carry_their_percentage(self) -> None:
        assert fbp.FUEL_CLASSES[415] == "M-1 boreal mixedwood, leafless (15% conifer)"
        assert fbp.FUEL_CLASSES[625] == "M-1/M-2 boreal mixedwood (25% conifer)"
        assert fbp.FUEL_CLASSES[750] == "M-3 dead balsam fir mixedwood, leafless (50% dead fir)"

    def test_the_legend_explains_every_code_in_the_source(self, source_codes: set[int]) -> None:
        """A code the table cannot name would be dropped as nodata and silently lost."""
        assert source_codes <= set(fbp.FUEL_CLASSES)


class TestFetch:
    def test_every_value_is_missing_or_a_published_class(
        self, fetched: tuple[np.ndarray, object]
    ) -> None:
        values, _ = fetched
        finite = values[np.isfinite(values)]
        assert set(finite.astype("int64").tolist()) <= set(fbp.FUEL_CLASSES)

    def test_the_read_invents_no_classes(
        self, fetched: tuple[np.ndarray, object], source_codes: set[int]
    ) -> None:
        """Nearest neighbour over a categorical raster must be a subset, not a blend."""
        values, _ = fetched
        finite = values[np.isfinite(values)]
        assert set(finite.astype("int64").tolist()) <= source_codes

    def test_the_window_is_actually_covered(self, fetched: tuple[np.ndarray, object]) -> None:
        values, _ = fetched
        assert np.isfinite(values).all()

    def test_off_the_raster_is_missing_not_zero(self, tmp_path: Path) -> None:
        straddling = _spine(EDGE_CENTRE, tmp_path)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(fbp, "FBP_URL", str(FIXTURE))
            values, _ = fbp.fetch(straddling)

        assert np.isnan(values).any()
        assert np.isfinite(values).any()
        assert not (values == 0).any()

    def test_a_grid_off_the_raster_altogether_refuses(self, tmp_path: Path) -> None:
        """Silence is a result; a grid with no fuel types under it is not."""
        elsewhere = _spine((52.5, -113.5), tmp_path)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(fbp, "FBP_URL", str(FIXTURE))
            with pytest.raises(RuntimeError, match="no FBP fuel types"):
                fbp.fetch(elsewhere)

    def test_the_source_record_says_where_it_came_from(
        self, fetched: tuple[np.ndarray, object]
    ) -> None:
        _, source = fetched
        assert source.access_route == "cwfis-datamart"
        assert source.native_resolution_m == 100.0
        assert source.native_timestep == "single epoch (2024 release)"
        assert source.licence == "Open Government Licence - Canada"
        # The record names what was actually read, which under test is the fixture.
        assert source.uri == str(FIXTURE)

    def test_the_module_points_at_the_100_m_grid(self) -> None:
        assert fbp.FBP_URL.endswith("FBP_fueltypes_Canada_100m_EPSG3978_20240527.tif")


class TestCellAggregation:
    @pytest.fixture(scope="class")
    def aggregated(self, spine: Spine) -> tuple[np.ndarray, np.ndarray, object]:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(fbp, "FBP_URL", str(FIXTURE))
            return fbp.cell_fuel_type(spine)

    def test_the_majority_is_a_code_the_cell_contains(
        self, spine: Spine, fetched: tuple[np.ndarray, object], aggregated: tuple[np.ndarray, ...]
    ) -> None:
        """The failure this guards against is a mean dressed up as a class."""
        values, _ = fetched
        winner = aggregated[0]

        index = spine.index.reshape(-1)
        flat = values.reshape(-1)
        for cell in range(spine.n_cells):
            if not np.isfinite(winner[cell]):
                continue
            inside = flat[index == cell]
            assert winner[cell] in set(inside[np.isfinite(inside)].tolist())

    def test_the_majority_is_the_most_common_code(
        self, spine: Spine, fetched: tuple[np.ndarray, object], aggregated: tuple[np.ndarray, ...]
    ) -> None:
        values, _ = fetched
        winner = aggregated[0]

        index = spine.index.reshape(-1)
        flat = values.reshape(-1)
        for cell in range(spine.n_cells):
            inside = flat[index == cell]
            inside = inside[np.isfinite(inside)]
            codes, counts = np.unique(inside, return_counts=True)
            assert winner[cell] == codes[int(np.argmax(counts))]

    def test_the_share_is_a_fraction_of_the_cell(self, aggregated: tuple[np.ndarray, ...]) -> None:
        share = aggregated[1]
        assert np.all(share >= 0.0)
        assert np.all(share <= 1.0)
        assert np.all(share > 0.0)

    def test_the_source_travels_with_the_cells(self, aggregated: tuple[np.ndarray, ...]) -> None:
        assert aggregated[2].dataset == "Canadian Forest FBP Fuel Types (CanFG)"
        assert aggregated[2].native_resolution_m == 100.0
