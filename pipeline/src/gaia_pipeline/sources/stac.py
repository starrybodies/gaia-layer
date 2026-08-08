"""STAC access to the Sentinel-2 L2A archive.

Isolated behind a small adapter so the endpoint can be swapped. v0.1 uses Element 84's
Earth Search, which is anonymous; Microsoft Planetary Computer serves the same collection
but requires SAS signing, which is one more thing between a fresh machine and a demo.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime

from pystac_client import Client
from tenacity import retry, stop_after_attempt, wait_exponential

from ..indices.spectral import REQUIRED_ASSETS

log = logging.getLogger(__name__)

EARTH_SEARCH_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
SOURCE = "ESA/Copernicus"
ACCESS_ROUTE = "earth-search-v1"

# Scene-level cloud filter. Deliberately loose — the scene classification layer does the
# real masking per pixel, and a 55%-cloud scene can still be clear over our area.
MAX_SCENE_CLOUD_COVER = 60.0


@dataclass(frozen=True)
class Scene:
    """One Sentinel-2 acquisition, reduced to what the pipeline needs."""

    item_id: str
    tile: str
    acquired_at: datetime
    cloud_cover: float
    epsg: int
    processing_baseline: str
    boa_offset_applied: bool
    assets: dict[str, str]

    @property
    def observation_id(self) -> str:
        return f"{COLLECTION}:{self.item_id}"

    @property
    def boa_offset(self) -> float:
        """Radiometric offset to add to raw digital numbers before scaling.

        From processing baseline 04.00 Sentinel-2 L2A carries a -1000 offset. Earth Search
        applies it to the COGs it publishes and says so in
        ``earthsearch:boa_offset_applied``; when it has already been applied, applying it
        again would shift every reflectance by 0.1 and every index with it.
        """
        if self.boa_offset_applied:
            return 0.0
        try:
            baseline = float(self.processing_baseline)
        except ValueError:
            return 0.0
        return -1000.0 if baseline >= 4.0 else 0.0


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, min=2, max=30), reraise=True)
def search_scenes(
    bbox: tuple[float, float, float, float],
    start: date,
    end: date,
    *,
    max_cloud_cover: float = MAX_SCENE_CLOUD_COVER,
    endpoint: str = EARTH_SEARCH_URL,
) -> list[Scene]:
    """Every usable scene intersecting ``bbox`` between ``start`` and ``end``, inclusive."""
    client = Client.open(endpoint)
    search = client.search(
        collections=[COLLECTION],
        bbox=list(bbox),
        datetime=f"{start.isoformat()}/{end.isoformat()}",
        query={"eo:cloud_cover": {"lt": max_cloud_cover}},
    )

    scenes: list[Scene] = []
    for item in search.items():
        props = item.properties
        assets = {name: item.assets[name].href for name in REQUIRED_ASSETS if name in item.assets}
        missing = set(REQUIRED_ASSETS) - assets.keys()
        if missing:
            log.warning("skipping %s: missing assets %s", item.id, sorted(missing))
            continue

        acquired = item.datetime or datetime.fromisoformat(str(props.get("datetime")))
        if acquired.tzinfo is None:
            acquired = acquired.replace(tzinfo=UTC)

        scenes.append(
            Scene(
                item_id=item.id,
                tile=str(props.get("grid:code") or props.get("s2:mgrs_tile") or "unknown"),
                acquired_at=acquired,
                cloud_cover=float(props.get("eo:cloud_cover", 100.0)),
                epsg=int(
                    props.get("proj:epsg") or props.get("proj:code", "EPSG:4326").split(":")[-1]
                ),
                processing_baseline=str(props.get("s2:processing_baseline", "")),
                boa_offset_applied=bool(props.get("earthsearch:boa_offset_applied", False)),
                assets=assets,
            )
        )

    return _deduplicate(scenes)


def _deduplicate(scenes: list[Scene]) -> list[Scene]:
    """Keep one scene per tile and acquisition date.

    The archive lists reprocessed versions of the same acquisition side by side. Compositing
    both would double-weight that date, so the highest processing baseline wins.
    """
    best: dict[tuple[str, date], Scene] = {}
    for scene in scenes:
        key = (scene.tile, scene.acquired_at.date())
        incumbent = best.get(key)
        if incumbent is None or scene.processing_baseline > incumbent.processing_baseline:
            best[key] = scene
    return sorted(best.values(), key=lambda s: (s.acquired_at, s.tile))


def select_for_month(scenes: list[Scene], max_per_tile: int) -> list[Scene]:
    """The least-cloudy ``max_per_tile`` scenes from each tile.

    Per tile, not overall. Taking the globally clearest scenes would happily pick three from
    one tile and leave the rest of the area with no observation at all.
    """
    by_tile: dict[str, list[Scene]] = defaultdict(list)
    for scene in scenes:
        by_tile[scene.tile].append(scene)

    selected: list[Scene] = []
    for tile_scenes in by_tile.values():
        ranked = sorted(tile_scenes, key=lambda s: (s.cloud_cover, s.acquired_at))
        selected.extend(ranked[:max_per_tile])
    return sorted(selected, key=lambda s: (s.acquired_at, s.tile))
