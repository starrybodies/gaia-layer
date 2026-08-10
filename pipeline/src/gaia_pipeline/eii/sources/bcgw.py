"""The BC Geographic Warehouse WFS: biogeoclimatic zone, forest inventory, freshwater atlas.

Three of the v0.2 components read provincial vector data. The alternative to this service is
the DataBC Distribution Service, which mails a download link for a provincial extract — tens
of gigabytes of file geodatabase for layers of which the study area needs a few thousand
features. The WFS serves the same warehouse tables anonymously, synchronously and clipped by
bounding box, which is the only route that runs unattended inside the runbook's budget.

Two things about the service shape this module, both discovered the hard way.

The bbox axis order is latitude first. The request declares `urn:ogc:def:crs:EPSG::4326`,
and under the URN form of the code EPSG's own axis order applies, so the box reads
south,west,north,east while the GeoJSON that comes back is lon,lat like any other. Getting
this backwards returns an empty collection rather than an error, which is the worst way for
a mistake to present itself.

The standard paging parameter does not work. `startIndex` requires GeoServer to establish a
total order, and of these five layers only BEC is published with a primary key; the rest are
views over Oracle with no key and no sort column the gateway can order inside its sixty
second ceiling. Every paged request against VRI or the Freshwater Atlas returns 504, with or
without an explicit `sortBy`, while the identical request without `startIndex` returns in a
quarter of a second. So paging here is spatial: ask for a box, and if the response says more
features matched than it returned, quarter the box and ask again. Features are collapsed on
a content digest rather than on the GeoJSON `id`, because the server mints those ids per
request for keyless layers and the same polygon comes back under a different id each time.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import numpy as np
from pyproj import Transformer
from rasterio.features import rasterize
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform
from tenacity import retry, stop_after_attempt, wait_exponential

from ...config import settings
from ...schemas.common import BBox
from ..archive import SourceRecord
from ..spine import Spine

log = logging.getLogger(__name__)

BASE_URL = "https://openmaps.gov.bc.ca/geo/pub/wfs"
ACCESS_ROUTE = "bcgw-wfs"
LICENCE = "Open Government Licence - British Columbia"

BEC_LAYER = "pub:WHSE_FOREST_VEGETATION.BEC_BIOGEOCLIMATIC_POLY"
VRI_LAYER = "pub:WHSE_FOREST_VEGETATION.VEG_COMP_LYR_R1_POLY"
FWA_STREAMS_LAYER = "pub:WHSE_BASEMAPPING.FWA_STREAM_NETWORKS_SP"
FWA_LAKES_LAYER = "pub:WHSE_BASEMAPPING.FWA_LAKES_POLY"
FWA_WETLANDS_LAYER = "pub:WHSE_BASEMAPPING.FWA_WETLANDS_POLY"

#: Every one of these tables calls its geometry column GEOMETRY. A `propertyName` list that
#: omits it returns features with a null geometry, so it is always appended.
GEOMETRY_PROPERTY = "GEOMETRY"

#: Six halvings takes the study area's two degrees down to about two kilometres a side. A box
#: that still overflows at that size is not a paging problem, so it is fetched whole instead.
MAX_SUBDIVISION = 6


@dataclass(frozen=True)
class _Layer:
    """What provenance needs to say about a warehouse table that the service will not."""

    dataset: str
    citation: str
    native_resolution_m: float
    native_timestep: str


#: Native resolution for a vector product is its mapping scale expressed as ground distance:
#: half a millimetre on the published sheet, which is 10 m at 1:20,000 and 125 m at
#: 1:250,000. It is a statement about how finely the polygon boundaries were drawn, not a
#: pixel size, and it is the number that stops a consumer reading a 30 m analysis grid as
#: evidence that BEC boundaries are known to 30 m.
_LAYERS: dict[str, _Layer] = {
    BEC_LAYER: _Layer(
        dataset="BEC Map biogeoclimatic zones, subzones and variants",
        citation=(
            "British Columbia Ministry of Forests. BEC Map: biogeoclimatic ecosystem "
            "classification zone, subzone and variant polygons "
            "(WHSE_FOREST_VEGETATION.BEC_BIOGEOCLIMATIC_POLY). BC Geographic Warehouse."
        ),
        native_resolution_m=125.0,
        native_timestep="irregular (revised as mapping is updated)",
    ),
    VRI_LAYER: _Layer(
        dataset="VRI forest vegetation composite rank 1 layer",
        citation=(
            "British Columbia Ministry of Forests. Vegetation Resources Inventory, forest "
            "vegetation composite rank 1 polygons "
            "(WHSE_FOREST_VEGETATION.VEG_COMP_LYR_R1_POLY). BC Geographic Warehouse."
        ),
        native_resolution_m=10.0,
        native_timestep="annual (attributes projected to a reference year)",
    ),
    FWA_STREAMS_LAYER: _Layer(
        dataset="Freshwater Atlas stream network",
        citation=(
            "British Columbia Ministry of Water, Land and Resource Stewardship. Freshwater "
            "Atlas stream network (WHSE_BASEMAPPING.FWA_STREAM_NETWORKS_SP). BC Geographic "
            "Warehouse."
        ),
        native_resolution_m=10.0,
        native_timestep="irregular (revised as mapping is updated)",
    ),
    FWA_LAKES_LAYER: _Layer(
        dataset="Freshwater Atlas lakes",
        citation=(
            "British Columbia Ministry of Water, Land and Resource Stewardship. Freshwater "
            "Atlas lake polygons (WHSE_BASEMAPPING.FWA_LAKES_POLY). BC Geographic Warehouse."
        ),
        native_resolution_m=10.0,
        native_timestep="irregular (revised as mapping is updated)",
    ),
    FWA_WETLANDS_LAYER: _Layer(
        dataset="Freshwater Atlas wetlands",
        citation=(
            "British Columbia Ministry of Water, Land and Resource Stewardship. Freshwater "
            "Atlas wetland polygons (WHSE_BASEMAPPING.FWA_WETLANDS_POLY). BC Geographic "
            "Warehouse."
        ),
        native_resolution_m=10.0,
        native_timestep="irregular (revised as mapping is updated)",
    ),
}


def fetch_features(
    layer: str,
    bbox: BBox,
    *,
    properties: list[str] | None = None,
    page_size: int = 1000,
    max_features: int | None = None,
) -> tuple[list[dict[str, Any]], SourceRecord]:
    """Every feature of a layer intersecting a bounding box, paged until exhausted.

    Paging is by quartering the box, not by `startIndex`; see the module docstring for why.
    `page_size` is how many features one request may return before its box is split, so it
    trades request count against response size rather than bounding the result.

    `max_features` is a stop for exploration and nothing else. It returns a truncated view of
    the layer with no way to tell which part, so nothing that feeds the archive should set it.
    """
    meta = _LAYERS[layer]

    collected: dict[str, dict[str, Any]] = {}
    served: str | None = None
    pending: list[tuple[BBox, int]] = [(bbox, 0)]

    while pending:
        tile, depth = pending.pop()
        payload = _get(layer, tile, properties, page_size)
        served = str(payload.get("timeStamp") or "") or served

        features = list(payload.get("features") or [])
        matched = int(payload.get("numberMatched") or len(features))

        if matched > len(features):
            if depth < MAX_SUBDIVISION:
                log.debug("%s: %d features in one box, quartering", layer, matched)
                pending.extend((quarter, depth + 1) for quarter in _quarters(tile))
                continue
            # Deeper splitting has stopped paying: take the whole box in one response
            # rather than return a subset and call it the layer.
            log.info("%s: %d features at the subdivision floor, fetching whole", layer, matched)
            features = list(_get(layer, tile, properties, matched).get("features") or [])

        for feature in features:
            collected[_fingerprint(feature)] = feature

        if max_features is not None and len(collected) >= max_features:
            break

    found = list(collected.values())
    if max_features is not None:
        found = found[:max_features]

    log.info("%s: %d features over %s", layer, len(found), bbox.as_tuple())
    return found, _source_record(layer, bbox, meta, served)


def rasterise(
    features: list[dict[str, Any]],
    spine: Spine,
    *,
    attribute: str,
    categorical: bool,
) -> tuple[np.ndarray, dict[int, str]]:
    """Burn a feature attribute onto the spine's grid. float32, NaN where no feature.

    Returns the grid and, for a categorical attribute, the code-to-label mapping needed to
    read it back; the mapping is empty for a continuous one. The mapping has to travel with
    the array because the codes are assigned from whatever labels this particular fetch
    contained, so the same zone is not guaranteed the same integer in two different areas.

    Codes start at one. Nothing downstream may average them: a class code is a name, and the
    mean of two names is a third name that no polygon carried. Aggregate them through
    `Spine.majority`.
    """
    geometries = _projected(features, spine)

    pairs = [
        (geometry, feature["properties"][attribute])
        for geometry, feature in geometries
        if (feature.get("properties") or {}).get(attribute) is not None
    ]

    labels: dict[int, str] = {}
    if categorical:
        labels = {code: label for code, label in enumerate(sorted({str(v) for _, v in pairs}), 1)}
        codes = {label: code for code, label in labels.items()}
        burn = [(geometry, float(codes[str(value)])) for geometry, value in pairs]
    else:
        burn = [(geometry, float(value)) for geometry, value in pairs]

    if not burn:
        return np.full(spine.grid.shape, np.nan, dtype="float32"), labels

    burned: np.ndarray = rasterize(
        burn,
        out_shape=spine.grid.shape,
        transform=spine.grid.transform,
        fill=np.nan,
        dtype="float32",
        all_touched=False,
    )
    return burned, labels


def rasterise_presence(
    features: list[dict[str, Any]], spine: Spine, *, buffer_m: float = 0.0
) -> np.ndarray:
    """Boolean coverage of the features, optionally buffered. For riparian extent.

    Buffering happens after projection, in the grid's metres, because a buffer in degrees is
    a different distance north to south than east to west and the riparian band is a real
    width on the ground.

    `all_touched`: a stream is a line with no area, so under the centre-of-pixel rule an
    unbuffered network would vanish from most of the pixels it runs through. Presence asks
    whether water is there, not whether it covers half the pixel.
    """
    geometries = [geometry for geometry, _ in _projected(features, spine)]
    if buffer_m > 0.0:
        geometries = [geometry.buffer(buffer_m) for geometry in geometries]

    if not geometries:
        return np.zeros(spine.grid.shape, dtype=bool)

    burned = rasterize(
        [(geometry, 1) for geometry in geometries],
        out_shape=spine.grid.shape,
        transform=spine.grid.transform,
        fill=0,
        dtype="uint8",
        all_touched=True,
    )
    present: np.ndarray = burned.astype(bool)
    return present


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=3, min=3, max=60), reraise=True)
def _get(layer: str, bbox: BBox, properties: list[str] | None, count: int) -> dict[str, Any]:
    """One GetFeature call. Retried: the gateway sheds load as a 504 under concurrency."""
    response = httpx.get(
        BASE_URL,
        params=_params(layer, bbox, properties, count),
        timeout=settings().http_timeout_s,
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload


def _params(
    layer: str, bbox: BBox, properties: list[str] | None, count: int | None
) -> dict[str, str]:
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": layer,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        # Latitude first: the URN form of the CRS code carries EPSG's axis order.
        "bbox": (f"{bbox.south},{bbox.west},{bbox.north},{bbox.east},urn:ogc:def:crs:EPSG::4326"),
    }
    if properties:
        wanted = dict.fromkeys([*properties, GEOMETRY_PROPERTY])
        params["propertyName"] = ",".join(wanted)
    if count is not None:
        params["count"] = str(count)
    return params


def _quarters(bbox: BBox) -> list[BBox]:
    mid_lon = (bbox.west + bbox.east) / 2.0
    mid_lat = (bbox.south + bbox.north) / 2.0
    return [
        BBox(west=bbox.west, south=bbox.south, east=mid_lon, north=mid_lat),
        BBox(west=mid_lon, south=bbox.south, east=bbox.east, north=mid_lat),
        BBox(west=bbox.west, south=mid_lat, east=mid_lon, north=bbox.north),
        BBox(west=mid_lon, south=mid_lat, east=bbox.east, north=bbox.north),
    ]


def _fingerprint(feature: dict[str, Any]) -> str:
    """Identity of a feature by its content, since the server's `id` is not stable.

    Two responses that describe the same polygon describe it identically, so the digest of
    the geometry and attributes collapses a feature that a boundary split returned twice.
    """
    body = json.dumps(
        {"geometry": feature.get("geometry"), "properties": feature.get("properties")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _projected(features: list[dict[str, Any]], spine: Spine) -> list[tuple[Any, dict[str, Any]]]:
    """Feature geometries in the grid's CRS, paired with the features they came from."""
    to_grid = Transformer.from_crs("EPSG:4326", spine.grid.crs, always_xy=True).transform
    return [
        (shapely_transform(to_grid, shape(feature["geometry"])), feature)
        for feature in features
        if feature.get("geometry") is not None
    ]


def _source_record(layer: str, bbox: BBox, meta: _Layer, served: str | None) -> SourceRecord:
    """Provenance for one read of one layer over one box.

    The warehouse publishes no edition identifier over WFS. It is a live database rather than
    a release, so the date the service itself stamped on the response is the only thing that
    distinguishes one read of a layer from another, and that is what goes down as the version.
    """
    retrieved = _timestamp(served)
    return SourceRecord(
        dataset=meta.dataset,
        version=f"warehouse state {retrieved.date().isoformat()}",
        access_route=ACCESS_ROUTE,
        uri=str(httpx.URL(BASE_URL, params=_params(layer, bbox, None, None))),
        citation=meta.citation,
        native_resolution_m=meta.native_resolution_m,
        native_timestep=meta.native_timestep,
        licence=LICENCE,
        retrieved=retrieved,
    )


def _timestamp(served: str | None) -> datetime:
    if not served:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(served).astimezone(UTC)
    except ValueError:
        log.warning("unparseable service timestamp %r, recording retrieval time", served)
        return datetime.now(UTC)
