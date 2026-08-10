"""The v0.2 study area.

The area is a decision, not a detail. It has to contain the fires the validation trains on
and the one the retrodiction is about, and it has to stay small enough that a full pass is
an hour rather than a day. These tests pin both ends of that.
"""

from __future__ import annotations

import math

import pytest
from shapely.geometry import Point, shape

from gaia_pipeline.eii.area import (
    EII_CRS,
    EII_RESOLUTION_M,
    H3_PARENT_RES,
    H3_RES,
    STUDY_AREA,
    STUDY_YEARS,
)

# Places the validation and the case study depend on.
KELOWNA = (-119.496, 49.888)
WEST_KELOWNA = (-119.583, 49.863)
MCDOUGALL_CREEK_IGNITION = (-119.620, 49.930)
VERNON = (-119.272, 50.267)
PENTICTON = (-119.593, 49.499)


@pytest.fixture(scope="module")
def polygon():
    return shape(STUDY_AREA.geometry.model_dump())


@pytest.mark.parametrize(
    ("name", "lon_lat"),
    [
        ("Kelowna", KELOWNA),
        ("West Kelowna", WEST_KELOWNA),
        ("McDougall Creek ignition", MCDOUGALL_CREEK_IGNITION),
        ("Vernon", VERNON),
        ("Penticton", PENTICTON),
    ],
)
def test_contains_the_places_the_work_is_about(
    polygon, name: str, lon_lat: tuple[float, float]
) -> None:
    assert polygon.contains(Point(*lon_lat)), f"{name} is outside the study area"


def test_area_is_big_enough_to_validate_and_small_enough_to_iterate() -> None:
    """Twenty to thirty-five thousand square kilometres.

    Below that there are not enough independent fire events for spatially disjoint folds;
    above it a single pass stops fitting in an evening, which is what killed the prompt's
    200,000 km2 proposal.
    """
    bbox = STUDY_AREA.bbox()
    # Rough planar estimate is enough for a bound this loose.
    mid_lat = (bbox.south + bbox.north) / 2.0
    km_per_deg_lon = 111.32 * math.cos(math.radians(mid_lat))
    area_km2 = (bbox.east - bbox.west) * km_per_deg_lon * (bbox.north - bbox.south) * 110.57

    assert 20_000 < area_km2 < 35_000


def test_grid_is_metric_equal_area_at_landsat_resolution() -> None:
    """BC Albers at 30 m.

    Equal-area matters because Component C is an area fraction and the aggregators weight by
    pixel count; 30 m is the native resolution of Landsat, the GLAD canopy mosaic and the
    finer FBP fuel grid, so nothing is resampled up into detail no sensor recorded.
    """
    assert EII_CRS == "EPSG:3005"
    assert EII_RESOLUTION_M == 30.0
    assert STUDY_AREA.analysis_crs == EII_CRS
    assert STUDY_AREA.grid_resolution_m == EII_RESOLUTION_M


def test_index_and_aggregation_resolutions() -> None:
    assert H3_RES == 8
    assert H3_PARENT_RES == 7


def test_study_period_covers_the_fire_years_the_gate_needs() -> None:
    assert len(STUDY_YEARS) == 10
    assert STUDY_YEARS[0] == 2015
    assert STUDY_YEARS[-1] == 2024
    for year in (2017, 2018, 2021, 2023):
        assert year in STUDY_YEARS


def test_aoi_id_does_not_collide_with_v01() -> None:
    """v0.1's coastal area stays exactly as it is; this is a neighbour, not a migration."""
    assert STUDY_AREA.aoi_id == "okanagan-interior"
