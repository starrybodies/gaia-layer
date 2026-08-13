"""BC Geographical Names, so that place coordinates are looked up rather than remembered.

The Kelowna retrodiction turns on two named places: Traders Cove and Wilson Landing, the
communities on Westside Road that the McDougall Creek fire reached. Typing their coordinates
in from memory would put the least verifiable numbers in the build at the exact point where
the demonstration makes its claim, and nothing downstream could catch a wrong one — a
plausible latitude produces a plausible cell and a plausible answer about the wrong ground.

So they come from the province's own gazetteer, anonymously, and the response carries a
source record like any other measurement. The service answers in BC Albers, which is already
the analysis CRS, so no reprojection stands between the lookup and the cell.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from pyproj import Transformer
from tenacity import retry, stop_after_attempt, wait_exponential

from ..archive import SourceRecord

log = logging.getLogger(__name__)

SEARCH_URL = "https://apps.gov.bc.ca/pub/bcgnws/names/search"

#: The service returns EPSG:3005, the same BC Albers the analysis grid is on.
GAZETTEER_CRS = "EPSG:3005"


@dataclass(frozen=True)
class Place:
    """One named place, as the gazetteer holds it."""

    name: str
    feature_type: str
    lat: float
    lon: float
    easting: float
    northing: float


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=20))
def _search(name: str, limit: int) -> dict[str, Any]:
    with httpx.Client(timeout=60.0) as client:
        response = client.get(
            SEARCH_URL,
            params={"name": name, "outputFormat": "json", "itemsPerPage": str(limit)},
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return payload


def find(name: str, *, feature_type: str | None = None, limit: int = 10) -> Place:
    """The best match for a name, optionally requiring a feature type.

    `feature_type` matters more than it looks. "Traders Cove" is both a cove and a community,
    and they are four hundred metres apart — inside one H3 cell here, but the habit of
    naming which one is wanted is what stops the next lookup silently taking a bay when it
    meant a town.

    Raises rather than guessing when nothing matches. A retrodiction about a place that could
    not be located is not a weaker retrodiction, it is a different one.
    """
    payload = _search(name, limit)
    features = list(payload.get("features") or [])
    if not features:
        raise RuntimeError(f"the BC gazetteer has no feature named {name!r}")

    to_wgs84 = Transformer.from_crs(GAZETTEER_CRS, "EPSG:4326", always_xy=True)

    for feature in features:
        properties = feature.get("properties") or {}
        kind = str(properties.get("featureType") or "")
        if feature_type is not None and not kind.lower().startswith(feature_type.lower()):
            continue

        easting, northing = (float(value) for value in feature["geometry"]["coordinates"])
        lon, lat = to_wgs84.transform(easting, northing)
        return Place(
            name=str(properties.get("name") or name),
            feature_type=kind,
            lat=float(lat),
            lon=float(lon),
            easting=easting,
            northing=northing,
        )

    raise RuntimeError(
        f"the BC gazetteer has {len(features)} features named {name!r} but none of type "
        f"{feature_type!r}"
    )


def source() -> SourceRecord:
    return SourceRecord(
        dataset="BC Geographical Names",
        version="live",
        access_route="bcgnws",
        uri=SEARCH_URL,
        citation=(
            "Province of British Columbia. BC Geographical Names Web Service. "
            "GeoBC, Ministry of Water, Land and Resource Stewardship."
        ),
        native_resolution_m=None,
        native_timestep="point feature, no time dimension",
        licence="Open Government Licence - British Columbia",
    )
