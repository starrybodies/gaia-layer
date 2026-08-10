"""The v0.2 study area and the grid it is measured on.

v0.1 works over the Southern Gulf Islands: coastal, small, and about as wet as a
Douglas-fir zone gets. v0.2 needs the opposite. The claim under test is that landscape
condition explains burn severity beyond weather and fuel type, and testing it takes ground
that burns — repeatedly, at scale, with enough separate events to build spatially disjoint
folds out of.

The Okanagan and the interior immediately around it is that ground. It carries the 2017,
2018, 2021 and 2023 fire years, it is the driest forested part of southern British
Columbia, and it contains McDougall Creek, which is the case study the whole pitch rests
on. It is also about seven times the size of the v0.1 area and no larger, because the
binding constraint on this work is how fast a full pass runs, not how much map it covers.
"""

from __future__ import annotations

from ..config import AreaOfInterest
from ..schemas.common import Polygon

#: BC Albers. Equal-area, which Component C's riparian fraction depends on being true, and
#: the projection every provincial dataset in this build already arrives in.
EII_CRS = "EPSG:3005"

#: 30 m: the native resolution of Landsat C2, the GLAD canopy-height mosaic and the finer
#: national FBP fuel grid. Resampling any of them finer would fabricate detail.
EII_RESOLUTION_M = 30.0

#: Resolution 8 hexes are about 0.74 km2. Small enough that a parcel-scale underwriting
#: question lands inside a handful of them, large enough that a 28,000 km2 archive is tens
#: of thousands of rows per period rather than millions.
H3_RES = 8

#: Resolution 7 is the portfolio aggregation, about 5.2 km2, seven res-8 cells to a parent.
H3_PARENT_RES = 7

#: 2015 is the first full year of Sentinel-2 and SMAP; 2024 is the last complete fire season
#: with settled perimeters. Ten years is enough for a temporal hold-out that is not a
#: rounding error.
STUDY_YEARS = tuple(range(2015, 2025))

#: West of Penticton to east of the Monashee foothills, the border to north of Vernon.
#: Roughly 28,000 km2.
_WEST, _SOUTH, _EAST, _NORTH = -120.60, 49.00, -118.40, 50.60

STUDY_AREA = AreaOfInterest(
    aoi_id="okanagan-interior",
    name="Okanagan and southern interior",
    description=(
        "Southern interior British Columbia from the international boundary north past "
        "Vernon, spanning the Okanagan valley and the dry Interior Douglas-fir and "
        "Ponderosa Pine zones on either side of it. Chosen for the density of large fires "
        "in the 2015-2024 record and because it contains the 2023 McDougall Creek fire."
    ),
    geometry=Polygon(
        type="Polygon",
        coordinates=[
            [
                [_WEST, _SOUTH],
                [_EAST, _SOUTH],
                [_EAST, _NORTH],
                [_WEST, _NORTH],
                [_WEST, _SOUTH],
            ]
        ],
    ),
    analysis_crs=EII_CRS,
    grid_resolution_m=EII_RESOLUTION_M,
)
