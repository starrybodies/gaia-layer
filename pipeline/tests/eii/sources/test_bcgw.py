"""The BC Geographic Warehouse client, against recorded responses.

The two things worth guarding here are the ones the service got wrong on the way in: that
paging terminates, and that it terminates without dropping or double-counting a feature.
Everything else is the rasteriser refusing to invent evidence — no feature, no value.

Fixtures under `fixtures/bcgw/` are real GetFeature responses, captured by `_capture` at the
bottom of this file. They are trimmed, and the trimming is not innocent: geometries are
simplified and clipped to the requested box, because a single BEC polygon is the whole zone
across the province and four of them arrive as five megabytes. Attributes are untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import h3
import httpx
import numpy as np
import pytest
import tenacity

from gaia_pipeline.eii.area import H3_RES
from gaia_pipeline.eii.sources import bcgw
from gaia_pipeline.eii.spine import Spine
from gaia_pipeline.schemas.common import BBox

FIXTURES = Path(__file__).parent.parent / "fixtures" / "bcgw"

#: The box the fixtures were captured over, west of Kelowna.
FIXTURE_BBOX = BBox(west=-119.62, south=49.84, east=-119.58, north=49.88)


def _fixture(name: str) -> dict[str, Any]:
    with (FIXTURES / name).open() as handle:
        payload: dict[str, Any] = json.load(handle)
    return payload


def _page(features: list[dict[str, Any]]) -> dict[str, Any]:
    """A response that matched exactly what it returned, i.e. one the client may trust."""
    return {
        "type": "FeatureCollection",
        "features": features,
        "numberMatched": len(features),
        "numberReturned": len(features),
        "timeStamp": "2026-08-10T15:20:22.246Z",
    }


class _Service:
    """Stands in for the warehouse: serves recorded responses, remembers the requests.

    Responses are handed out in order and the last one repeats, which is what a walk over
    quartered boxes wants. `failures` leading 504s exercise the retry.
    """

    def __init__(self, *payloads: dict[str, Any], failures: int = 0) -> None:
        self.payloads = list(payloads)
        self.failures = failures
        self.requests: list[dict[str, str]] = []

    def __call__(self, url: str, *, params: dict[str, str], timeout: float) -> httpx.Response:
        request = httpx.Request("GET", url, params=params)
        self.requests.append(dict(params))

        if self.failures > 0:
            self.failures -= 1
            return httpx.Response(504, json={"message": "upstream timing out"}, request=request)

        payload = self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]
        return httpx.Response(200, json=payload, request=request)

    @property
    def call_count(self) -> int:
        return len(self.requests)


@pytest.fixture
def instant_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the backoff out of the test clock without pretending it is not configured."""
    monkeypatch.setattr(bcgw._get.retry, "wait", tenacity.wait_none())


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def install(*payloads: dict[str, Any], failures: int = 0) -> _Service:
        service = _Service(*payloads, failures=failures)
        monkeypatch.setattr(bcgw.httpx, "get", service)
        return service

    return install


@pytest.fixture(scope="module")
def spine(tmp_path_factory: pytest.TempPathFactory) -> Spine:
    """A spine over the fixture box: sixty-one res-8 cells, about five kilometres across."""
    centre = h3.latlng_to_cell(49.86, -119.60, H3_RES)
    return Spine.for_cells(
        sorted(h3.grid_disk(centre, 4)), cache_dir=tmp_path_factory.mktemp("bcgw")
    )


@pytest.fixture(scope="module")
def bec_features() -> list[dict[str, Any]]:
    return list(_fixture("bec-zones.json")["features"])


@pytest.fixture(scope="module")
def vri_features() -> list[dict[str, Any]]:
    return list(_fixture("vri-stands.json")["features"])


@pytest.fixture(scope="module")
def stream_features() -> list[dict[str, Any]]:
    return list(_fixture("fwa-streams.json")["features"])


class TestPaging:
    def test_a_page_that_matched_what_it_returned_ends_the_walk(self, serve) -> None:
        service = serve(_fixture("vri-stands.json"))

        features, _ = bcgw.fetch_features(bcgw.VRI_LAYER, FIXTURE_BBOX, page_size=100)

        assert service.call_count == 1
        assert len(features) == 87

    def test_a_truncated_page_quarters_the_box(self, serve, vri_features) -> None:
        quarters = [_page(vri_features[i::4]) for i in range(4)]
        service = serve(_fixture("vri-stands-truncated.json"), *quarters)

        features, _ = bcgw.fetch_features(bcgw.VRI_LAYER, FIXTURE_BBOX, page_size=5)

        assert service.call_count == 5
        assert len(features) == 87

    def test_a_feature_two_tiles_both_returned_is_kept_once(self, serve, vri_features) -> None:
        overlapping = [
            _page(vri_features[:40]),
            _page(vri_features[30:70]),
            _page(vri_features[60:]),
            _page([]),
        ]
        serve(_fixture("vri-stands-truncated.json"), *overlapping)

        features, _ = bcgw.fetch_features(bcgw.VRI_LAYER, FIXTURE_BBOX, page_size=5)

        assert len(features) == 87

    def test_the_subdivision_floor_falls_back_to_one_whole_response(
        self, serve, vri_features, monkeypatch
    ) -> None:
        monkeypatch.setattr(bcgw, "MAX_SUBDIVISION", 0)
        service = serve(_fixture("vri-stands-truncated.json"), _page(vri_features))

        features, _ = bcgw.fetch_features(bcgw.VRI_LAYER, FIXTURE_BBOX, page_size=5)

        assert service.call_count == 2
        assert service.requests[1]["count"] == "109"
        assert len(features) == 87

    def test_max_features_stops_the_walk(self, serve) -> None:
        service = serve(_fixture("vri-stands.json"))

        features, _ = bcgw.fetch_features(
            bcgw.VRI_LAYER, FIXTURE_BBOX, page_size=100, max_features=4
        )

        assert service.call_count == 1
        assert len(features) == 4

    def test_the_bbox_is_latitude_first(self, serve) -> None:
        service = serve(_fixture("vri-stands.json"))

        bcgw.fetch_features(bcgw.VRI_LAYER, FIXTURE_BBOX)

        assert service.requests[0]["bbox"] == (
            "49.84,-119.62,49.88,-119.58,urn:ogc:def:crs:EPSG::4326"
        )

    def test_the_geometry_column_is_added_to_a_property_list(self, serve) -> None:
        service = serve(_fixture("bec-zones.json"))

        bcgw.fetch_features(bcgw.BEC_LAYER, FIXTURE_BBOX, properties=["ZONE", "MAP_LABEL"])

        assert service.requests[0]["propertyName"] == "ZONE,MAP_LABEL,GEOMETRY"

    def test_a_gateway_timeout_is_retried(self, serve, instant_retries) -> None:
        service = serve(_fixture("vri-stands.json"), failures=2)

        features, _ = bcgw.fetch_features(bcgw.VRI_LAYER, FIXTURE_BBOX)

        assert service.call_count == 3
        assert len(features) == 87

    def test_an_unknown_layer_is_refused_before_any_request(self, serve) -> None:
        service = serve(_fixture("vri-stands.json"))

        with pytest.raises(KeyError):
            bcgw.fetch_features("pub:WHSE_NOTHING.MADE_UP", FIXTURE_BBOX)

        assert service.call_count == 0


class TestSourceRecord:
    def test_it_names_the_door_we_came_in_through(self, serve) -> None:
        serve(_fixture("vri-stands.json"))

        _, source = bcgw.fetch_features(bcgw.VRI_LAYER, FIXTURE_BBOX)

        assert source.access_route == "bcgw-wfs"
        assert source.licence == "Open Government Licence - British Columbia"
        assert bcgw.VRI_LAYER in unquote(source.uri)
        assert source.native_resolution_m == 10.0
        assert source.native_timestep

    def test_the_version_is_the_day_the_service_served_it(self, serve) -> None:
        serve(_fixture("vri-stands.json"))

        _, source = bcgw.fetch_features(bcgw.VRI_LAYER, FIXTURE_BBOX)

        served = _fixture("vri-stands.json")["timeStamp"][:10]
        assert source.version == f"warehouse state {served}"
        assert source.retrieved.date().isoformat() == served

    def test_the_uri_reproduces_the_request(self, serve) -> None:
        serve(_fixture("bec-zones.json"))

        _, source = bcgw.fetch_features(bcgw.BEC_LAYER, FIXTURE_BBOX)

        assert "bbox=49.84,-119.62,49.88,-119.58,urn:ogc:def:crs:EPSG::4326" in unquote(source.uri)
        assert "request=GetFeature" in source.uri


class TestRasterise:
    def test_a_categorical_attribute_burns_codes_from_its_own_mapping(
        self, bec_features, spine
    ) -> None:
        values, labels = bcgw.rasterise(bec_features, spine, attribute="ZONE", categorical=True)

        burned = set(np.unique(values[np.isfinite(values)]).astype(int).tolist())
        assert burned
        assert burned <= set(labels)
        assert set(labels.values()) <= {f["properties"]["ZONE"] for f in bec_features}

    def test_class_codes_stay_whole_numbers(self, bec_features, spine) -> None:
        values, _ = bcgw.rasterise(bec_features, spine, attribute="ZONE", categorical=True)

        finite = values[np.isfinite(values)]
        assert np.array_equal(finite, np.round(finite))

    def test_a_continuous_attribute_keeps_the_values_it_was_given(
        self, vri_features, spine
    ) -> None:
        values, labels = bcgw.rasterise(
            vri_features, spine, attribute="PROJ_HEIGHT_1", categorical=False
        )

        given = np.array(
            [
                f["properties"]["PROJ_HEIGHT_1"]
                for f in vri_features
                if f["properties"]["PROJ_HEIGHT_1"] is not None
            ],
            dtype="float32",
        )
        burned = np.unique(values[np.isfinite(values)])

        assert burned.size
        assert np.isin(burned, given).all()
        assert labels == {}

    def test_no_features_is_all_nan(self, spine) -> None:
        values, labels = bcgw.rasterise([], spine, attribute="ZONE", categorical=True)

        assert values.shape == spine.grid.shape
        assert np.isnan(values).all()
        assert labels == {}

    def test_features_off_the_grid_burn_nothing_and_raise_nothing(self, spine) -> None:
        elsewhere = [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-100.0, 40.0], [-99.9, 40.0], [-99.9, 40.1], [-100.0, 40.0]]],
                },
                "properties": {"ZONE": "IDF"},
            }
        ]

        values, labels = bcgw.rasterise(elsewhere, spine, attribute="ZONE", categorical=True)

        assert np.isnan(values).all()
        assert labels == {1: "IDF"}

    def test_a_feature_missing_the_attribute_contributes_nothing(self, spine) -> None:
        unmeasured = [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-119.60, 49.86],
                            [-119.59, 49.86],
                            [-119.59, 49.87],
                            [-119.60, 49.86],
                        ]
                    ],
                },
                "properties": {"PROJ_HEIGHT_1": None},
            }
        ]

        values, _ = bcgw.rasterise(unmeasured, spine, attribute="PROJ_HEIGHT_1", categorical=False)

        assert np.isnan(values).all()


class TestPresence:
    def test_a_buffer_covers_more_ground_than_none(self, stream_features, spine) -> None:
        bare = bcgw.rasterise_presence(stream_features, spine)
        buffered = bcgw.rasterise_presence(stream_features, spine, buffer_m=60.0)

        assert bare.any()
        assert buffered.sum() > bare.sum()
        assert bool((buffered | bare == buffered).all())

    def test_an_unbuffered_line_still_registers(self, stream_features, spine) -> None:
        """Streams have no area. Under a centre-of-pixel rule they would vanish."""
        assert bcgw.rasterise_presence(stream_features, spine).sum() > 0

    def test_no_features_is_no_coverage(self, spine) -> None:
        present = bcgw.rasterise_presence([], spine, buffer_m=100.0)

        assert present.shape == spine.grid.shape
        assert present.dtype == bool
        assert not present.any()


# --------------------------------------------------------------------------- fixture capture

_CAPTURES: tuple[tuple[str, str, list[str], int, float], ...] = (
    (
        "bec-zones.json",
        bcgw.BEC_LAYER,
        ["ZONE", "SUBZONE", "VARIANT", "MAP_LABEL", "ZONE_NAME", "SUBZONE_NAME"],
        20,
        0.002,
    ),
    (
        "vri-stands.json",
        bcgw.VRI_LAYER,
        ["PROJ_HEIGHT_1", "CROWN_CLOSURE", "PROJ_AGE_1", "BCLCS_LEVEL_2", "SPECIES_CD_1"],
        500,
        0.0003,
    ),
    (
        "vri-stands-truncated.json",
        bcgw.VRI_LAYER,
        ["PROJ_HEIGHT_1", "CROWN_CLOSURE", "PROJ_AGE_1", "BCLCS_LEVEL_2", "SPECIES_CD_1"],
        5,
        0.0002,
    ),
    (
        "fwa-streams.json",
        bcgw.FWA_STREAMS_LAYER,
        ["LINEAR_FEATURE_ID", "STREAM_ORDER", "GNIS_NAME", "EDGE_TYPE"],
        200,
        0.0003,
    ),
)


def _capture() -> None:
    """Re-record the fixtures from the live service. Run by hand; never from a test.

    Geometries are simplified and clipped to the captured box. The service returns whole
    features, and a BEC zone polygon runs the length of the province, so the untrimmed
    responses are megabytes for a handful of features. Properties are recorded verbatim.
    """
    from shapely.geometry import mapping, shape

    box = shape(FIXTURE_BBOX.as_polygon().model_dump())

    for name, layer, properties, count, tolerance in _CAPTURES:
        response = httpx.get(
            bcgw.BASE_URL,
            params=bcgw._params(layer, FIXTURE_BBOX, properties, count),
            timeout=180.0,
        )
        response.raise_for_status()
        payload = response.json()

        trimmed = []
        for feature in payload["features"]:
            clipped = shape(feature["geometry"]).simplify(tolerance).intersection(box)
            if clipped.is_empty:
                continue
            feature["geometry"] = mapping(clipped)
            trimmed.append(feature)

        # Simplification can push a sliver that only just touched the box back out of it.
        # The counts have to follow, or the fixture claims features it does not carry.
        dropped = len(payload["features"]) - len(trimmed)
        payload["features"] = trimmed
        payload["numberMatched"] -= dropped
        payload["numberReturned"] = len(trimmed)

        path = FIXTURES / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as handle:
            json.dump(payload, handle)
        print(f"{name}: {len(trimmed)} features, {path.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    _capture()
