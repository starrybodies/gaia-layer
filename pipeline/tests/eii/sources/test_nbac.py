"""NBAC perimeters, read from a five-record cut of the real 2023 composite.

The fixture is not synthetic. `NBAC_2023_20260513.zip` holds five records lifted byte for
byte out of the national archive — four Okanagan fires including the 12,969 ha McDougall
Creek complex, and one Northwest Territories fire a thousand kilometres outside the study
area — rewritten into a valid shapefile with the original header, projection and attribute
table. So the reader is tested against the bytes it will actually meet, and no test touches
the network: `perimeters` finds the archive already in the cache directory it is handed.

The load-bearing test is the area one. This module parses the shapefile format itself rather
than through a GDAL binding, and NBAC states its own polygon area in `POLY_HA`. Requiring the
geometry we rebuild to reproduce that number in an equal-area projection is a check the
publisher wrote and we cannot fudge: a dropped ring, a misread part offset or a wrong
projection all show up as a wrong number of hectares.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import h3
import numpy as np
import pytest
from pyproj import Transformer
from shapely.geometry import box, shape
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union

from gaia_pipeline.eii.area import EII_CRS, H3_RES, STUDY_AREA, STUDY_YEARS
from gaia_pipeline.eii.sources import nbac
from gaia_pipeline.eii.spine import Spine

FIXTURES = Path(__file__).parent.parent / "fixtures" / "nbac"
LISTING = FIXTURES / "directory-listing.html"

STUDY_POLYGON = shape(STUDY_AREA.geometry.model_dump())

#: BC Albers is equal-area, so a polygon's area in it is hectares on the ground.
TO_EQUAL_AREA = Transformer.from_crs("EPSG:4326", EII_CRS, always_xy=True).transform


@pytest.fixture(scope="module")
def read():
    return nbac.perimeters(2023, cache_dir=FIXTURES)


@pytest.fixture(scope="module")
def national(read):
    return read[0]


@pytest.fixture(scope="module")
def source(read):
    return read[1]


@pytest.fixture(scope="module")
def okanagan():
    return nbac.perimeters(2023, within=STUDY_POLYGON, cache_dir=FIXTURES)[0]


@pytest.fixture(scope="module")
def spine(okanagan, tmp_path_factory):
    """A spine centred on the largest fire, wide enough to reach unburned ground."""
    centre = max(okanagan, key=lambda perimeter: perimeter.area_ha).geometry.representative_point()
    ring = sorted(h3.grid_disk(h3.latlng_to_cell(centre.y, centre.x, H3_RES), 7))
    return Spine.for_cells(ring, cache_dir=tmp_path_factory.mktemp("nbac-spine"))


class TestDirectoryListing:
    def test_every_study_year_is_published(self) -> None:
        years = nbac._parse_listing(LISTING.read_text())
        assert set(STUDY_YEARS) <= set(years)

    def test_the_combined_archive_is_not_mistaken_for_a_year(self) -> None:
        """`NBAC_1972to2025_20260513_shp.zip` sits in the same directory and is a gigabyte."""
        years = nbac._parse_listing(LISTING.read_text())
        assert set(years) == set(range(1972, 2026))

    def test_the_url_carries_the_release_date(self) -> None:
        years = nbac._parse_listing(LISTING.read_text())
        assert years[2023] == f"{nbac.BASE_URL}NBAC_2023_20260513.zip"


class TestSourceRecord:
    def test_the_release_is_the_version(self, source) -> None:
        """Two composites of the same fire year are two different datasets."""
        assert source.version == "20260513"

    def test_the_record_says_where_the_bytes_came_from(self, source) -> None:
        assert source.dataset == "NBAC"
        assert source.access_route == "cwfis-datamart"
        assert source.uri == f"{nbac.BASE_URL}NBAC_2023_20260513.zip"
        assert source.citation.startswith("Canadian Forest Service.")
        assert source.native_timestep == "annual"
        assert source.native_resolution_m is None
        assert source.retrieved.tzinfo is not None

    def test_the_licence_is_the_one_cwfis_publishes(self, source) -> None:
        assert "Open Government Licence - Canada" in source.licence
        assert "Canadian Wildland Fire Information System" in source.licence


class TestPerimeters:
    def test_the_published_area_matches_the_geometry_we_rebuilt(self, national) -> None:
        for perimeter in national:
            measured = shapely_transform(TO_EQUAL_AREA, perimeter.geometry).area / 10_000.0
            assert perimeter.area_ha > 0.0
            assert measured == pytest.approx(perimeter.area_ha, rel=0.01), perimeter.fire_id

    def test_geometry_arrives_in_wgs84(self, national) -> None:
        """NBAC ships in Canada Atlas Lambert, whose ordinates run to millions of metres."""
        for perimeter in national:
            west, south, east, north = perimeter.geometry.bounds
            assert -141.0 < west <= east < -52.0
            assert 41.0 < south <= north < 84.0

    def test_unburned_islands_survive_the_read(self, okanagan) -> None:
        """The McDougall Creek complex has a hundred holes in it, and they are not filled."""
        largest = max(okanagan, key=lambda perimeter: perimeter.area_ha)
        assert sum(len(part.interiors) for part in largest.geometry.geoms) > 50

    def test_the_attribute_mapping_is_the_one_nbac_documents(self, okanagan) -> None:
        largest = max(okanagan, key=lambda perimeter: perimeter.area_ha)
        assert largest.fire_id == "2023_834"
        assert largest.year == 2023
        assert largest.area_ha == pytest.approx(12969.36, abs=0.01)
        assert largest.start_date == date(2023, 7, 1)
        assert largest.end_date == date(2023, 10, 18)

    def test_a_fire_never_ends_before_it_starts(self, national) -> None:
        for perimeter in national:
            if perimeter.start_date and perimeter.end_date:
                assert perimeter.start_date <= perimeter.end_date


class TestClipping:
    def test_a_fire_outside_the_study_area_is_left_out(self, national, okanagan) -> None:
        """2023_226 burned near Great Slave Lake and has no business in an Okanagan sample."""
        assert "2023_226" in {perimeter.fire_id for perimeter in national}
        assert "2023_226" not in {perimeter.fire_id for perimeter in okanagan}

    def test_an_area_with_no_fires_is_empty_rather_than_an_error(self) -> None:
        unburned = box(-119.00, 49.05, -118.90, 49.15)
        found, _ = nbac.perimeters(2023, within=unburned, cache_dir=FIXTURES)
        assert found == []


class TestBurnedArea:
    def test_nothing_burned_is_zero_everywhere(self, spine) -> None:
        assert not nbac.burned_mask([], spine).any()
        assert np.all(nbac.burned_fraction([], spine) == 0.0)

    def test_cells_inside_the_perimeter_are_burned_and_cells_outside_are_not(
        self, okanagan, spine
    ) -> None:
        fraction = nbac.burned_fraction(okanagan, spine)
        assert fraction.max() == pytest.approx(1.0, abs=0.01)
        assert (fraction == 0.0).sum() > 0
        assert (fraction > 0.99).sum() > 20

    def test_the_mask_conserves_the_burned_area(self, okanagan, spine) -> None:
        """Pixel counting and polygon geometry should agree on how much ground burned."""
        to_grid = Transformer.from_crs("EPSG:4326", spine.grid.crs, always_xy=True).transform
        burned = unary_union(
            [shapely_transform(to_grid, perimeter.geometry) for perimeter in okanagan]
        )
        within_grid = burned.intersection(box(*spine.grid.bounds)).area

        pixels = int(nbac.burned_mask(okanagan, spine).sum())
        assert pixels * spine.grid.resolution_m**2 == pytest.approx(within_grid, rel=0.01)
