"""Finds POIs in OpenStreetMap through the Overpass API.

Two shapes of search -- along a route polyline and inside a bounding box --
both one Overpass request for all wanted categories, both cached (a route
searched twice costs one request) and retried on timeouts and 5xx like the
surface enricher. Public Overpass instances are shared infrastructure: the
queries are bounded (decimated polyline, element cap) and identify the client.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Sequence
from typing import Any

import httpx

from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.poi.categories import (
    SERVICE_MAX_OFFSET_M,
    PoiCategory,
    category_for_tags,
)
from bike_routing_agent.poi.geo import BBox, LonLat, corridor_boxes, decimate
from bike_routing_agent.poi.models import QID_RE, WIKIPEDIA_TAG_RE, Poi
from bike_routing_agent.providers.base import CacheBackend

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "bike-routing-agent/0.1 (+https://github.com/JonasHeinickeBio/pyBikeRouter)"
# Query size grows with (selectors x polyline points); 150 points keeps a long route's query
# near 45 kB, and chords between them stay far inside a kilometre-scale corridor.
MAX_QUERY_POINTS = 150
# Upper bound on elements per kind one search returns (a dense city centre would give thousands).
MAX_SIGHT_ELEMENTS = 1500
MAX_SERVICE_ELEMENTS = 1000
_MAX_ERROR_BODY_CHARS = 300
_CACHE_SCHEMA = 1


class OverpassPoiFetcher:
    name = "overpass-poi"

    def __init__(
        self,
        *,
        base_urls: Sequence[str] = ("https://overpass-api.de/api/interpreter",),
        timeout_s: float = 25.0,
        max_retries: int = 0,
        user_agent: str = DEFAULT_USER_AGENT,
        cache: CacheBackend | None = None,
        cache_ttl_s: float = 86_400.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_urls:
            raise ValueError("at least one Overpass URL is needed")
        self._base_urls = list(base_urls)
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._user_agent = user_agent
        self._cache = cache
        self._cache_ttl_s = cache_ttl_s
        self._client = client

    async def along(
        self,
        line: Sequence[LonLat],
        categories: Sequence[PoiCategory],
        *,
        buffer_m: float,
        linked_only: bool = False,
    ) -> list[Poi]:
        """POIs within ``buffer_m`` of the polyline (services: at most 500 m).

        ``linked_only`` asks Overpass only for elements tagged ``wikidata`` or ``wikipedia``:
        a much smaller, faster search when only well-known places matter.
        """
        if len(line) < 2:
            raise ValueError("a route needs at least two points")
        points = decimate(line, MAX_QUERY_POINTS)
        key = _cache_key(
            "along-linked" if linked_only else "along",
            categories,
            buffer_m,
            [f"{lon:.4f},{lat:.4f}" for lon, lat in points],
        )
        return await self._search(
            build_along_query(
                points,
                categories,
                buffer_m=buffer_m,
                timeout_s=self._timeout_s,
                linked_only=linked_only,
            ),
            categories,
            key,
        )

    async def in_bbox(
        self, bbox: tuple[float, float, float, float], categories: Sequence[PoiCategory]
    ) -> list[Poi]:
        """POIs inside ``(min_lon, min_lat, max_lon, max_lat)``."""
        key = _cache_key("bbox", categories, 0.0, [f"{v:.3f}" for v in bbox])
        return await self._search(
            build_bbox_query(bbox, categories, timeout_s=self._timeout_s), categories, key
        )

    async def _search(self, query: str, categories: Sequence[PoiCategory], key: str) -> list[Poi]:
        if self._cache is not None:
            cached = await self._cache.get(key)
            if isinstance(cached, list):
                try:
                    return [Poi.model_validate(item) for item in cached]
                except ValueError:
                    pass  # an entry from another shape: fetch again
        payload = await self._post(query)
        pois = parse_pois(payload, categories)
        if self._cache is not None:
            await self._cache.set(
                key, [p.model_dump(mode="json") for p in pois], ttl_s=self._cache_ttl_s
            )
        return pois

    async def _post(self, query: str) -> Any:
        """POST to each configured instance in turn; the first usable answer wins.

        Public Overpass instances are routinely overloaded (504s after tens of seconds), so
        a timeout, a 5xx, a 429 or an HTML error page moves on to the next instance. When
        every instance failed the last error is raised.
        """
        last: ProviderError | None = None
        for _ in range(self._max_retries + 1):
            for url in self._base_urls:
                try:
                    return await self._post_once(url, query)
                except ProviderError as exc:
                    logger.warning("Overpass %s failed: %s", url, exc.message)
                    last = exc
        assert last is not None
        raise last

    async def _post_once(self, url: str, query: str) -> Any:
        try:
            if self._client is not None:
                response = await self._client.post(
                    url,
                    data={"data": query},
                    headers={"User-Agent": self._user_agent},
                    timeout=self._timeout_s + 5,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout_s + 5, headers={"User-Agent": self._user_agent}
                ) as client:
                    response = await client.post(url, data={"data": query})
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Overpass request timed out", provider=self.name) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "Overpass request failed", provider=self.name, detail={"error": str(exc)}
            ) from exc

        if response.status_code == 429:
            raise ProviderRateLimitError("Overpass rate limited the request", provider=self.name)
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                f"Overpass returned HTTP {response.status_code}",
                provider=self.name,
                detail={"body": response.text[:_MAX_ERROR_BODY_CHARS]},
            )
        try:
            payload = response.json()
        except ValueError as exc:
            # A busy Overpass answers 200 with an HTML error page.
            raise ProviderBadResponseError(
                "Overpass returned invalid JSON",
                provider=self.name,
                detail={"body": response.text[:_MAX_ERROR_BODY_CHARS]},
            ) from exc
        _raise_for_runtime_error(payload, self.name)
        return payload


def _raise_for_runtime_error(payload: Any, provider: str) -> None:
    """Overpass reports a query that timed out or ran out of memory as HTTP 200 with a
    ``remark`` and *empty or partial* elements. Taking that for "nothing found" would hide
    the failure and cache it, so it is an error."""
    remark = payload.get("remark") if isinstance(payload, dict) else None
    if isinstance(remark, str) and "runtime error" in remark:
        error = ProviderTimeoutError if "timed out" in remark else ProviderUnavailableError
        raise error(
            "Overpass could not finish the query", provider=provider, detail={"remark": remark}
        )


def _cache_key(
    kind: str, categories: Sequence[PoiCategory], radius: float, parts: list[str]
) -> str:
    digest = hashlib.sha256()
    digest.update(f"{_CACHE_SCHEMA};{kind};{radius:.0f};".encode())
    digest.update(",".join(c.key for c in categories).encode())
    digest.update(b";" + ";".join(parts).encode())
    return f"poi:{kind}:{digest.hexdigest()}"


def build_along_query(
    points: Sequence[LonLat],
    categories: Sequence[PoiCategory],
    *,
    buffer_m: float,
    timeout_s: float,
    linked_only: bool = False,
) -> str:
    """Bounding-box lookups along the route (see ``corridor_boxes`` for why not ``around``).

    Sights are padded by the buffer, services by their shorter reach; exact distances are
    measured afterwards by the caller.
    """
    sight_boxes = corridor_boxes(points, buffer_m)
    if linked_only:
        return _linked_query(categories, sight_boxes, timeout_s)
    service_boxes = corridor_boxes(points, min(buffer_m, SERVICE_MAX_OFFSET_M))
    sights = [
        f"nwr{selector}{_box(box)};"
        for c in categories
        if c.kind == "sight"
        for selector in c.selectors
        for box in sight_boxes
    ]
    services = [
        f"nwr{selector}{_box(box)};"
        for c in categories
        if c.kind == "service"
        for selector in c.selectors
        for box in service_boxes
    ]
    return _wrap(sights, services, timeout_s)


def build_bbox_query(
    bbox: tuple[float, float, float, float], categories: Sequence[PoiCategory], *, timeout_s: float
) -> str:
    box = _box(bbox)
    sights = [f"nwr{s}{box};" for c in categories if c.kind == "sight" for s in c.selectors]
    services = [f"nwr{s}{box};" for c in categories if c.kind == "service" for s in c.selectors]
    return _wrap(sights, services, timeout_s)


def _linked_query(
    categories: Sequence[PoiCategory], boxes: Sequence[BBox], timeout_s: float
) -> str:
    """Everything tagged ``wikidata`` / ``wikipedia`` in the boxes, then narrowed to the
    categories *in memory*. One index lookup per box and tag instead of one per category
    selector and box: a fraction of the work, which is what the public instance can bear."""
    sights = [c for c in categories if c.kind == "sight"]
    if not sights:
        return _wrap([], [], timeout_s)
    base = "".join(
        f'nwr["{tag}"]{_box(box)};' for box in boxes for tag in ("wikidata", "wikipedia")
    )
    narrowed = "".join(f"nwr.linked{selector};" for c in sights for selector in _bare_selectors(c))
    timeout = max(1, math.ceil(timeout_s))
    return (
        f"[out:json][timeout:{timeout}];({base})->.linked;"
        f"({narrowed});out tags center {MAX_SIGHT_ELEMENTS};"
    )


def _bare_selectors(category: PoiCategory) -> list[str]:
    """Selectors without the trailing link requirement (the base set already has one)."""
    out: list[str] = []
    for selector in category.selectors:
        for suffix in ('["wikidata"]', '["wikipedia"]'):
            if selector.endswith(suffix):
                selector = selector[: -len(suffix)]
                break
        if selector not in out:
            out.append(selector)
    return out


def _box(bbox: tuple[float, float, float, float]) -> str:
    min_lon, min_lat, max_lon, max_lat = bbox
    return f"({min_lat:.5f},{min_lon:.5f},{max_lat:.5f},{max_lon:.5f})"


def _wrap(sights: list[str], services: list[str], timeout_s: float) -> str:
    """Sights and services are output separately so a city full of cafes cannot crowd the
    sights out of the element cap."""
    timeout = max(1, math.ceil(timeout_s))
    blocks = []
    if sights:
        blocks.append(f"({''.join(sights)});out tags center {MAX_SIGHT_ELEMENTS};")
    if services:
        blocks.append(f"({''.join(services)});out tags center {MAX_SERVICE_ELEMENTS};")
    return f"[out:json][timeout:{timeout}];{''.join(blocks)}"


def parse_pois(payload: Any, categories: Sequence[PoiCategory]) -> list[Poi]:
    """Overpass JSON -> POIs. Malformed elements are skipped: partial map data is normal."""
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise ProviderBadResponseError("malformed Overpass response", provider="overpass-poi")
    allowed = list(categories)
    pois: list[Poi] = []
    for element in payload["elements"]:
        poi = _parse_element(element, allowed)
        if poi is not None:
            pois.append(poi)
    return pois


def _parse_element(element: object, allowed: list[PoiCategory]) -> Poi | None:
    if not isinstance(element, dict):
        return None
    kind = element.get("type")
    ident = element.get("id")
    tags = element.get("tags")
    if kind not in ("node", "way", "relation") or not isinstance(ident, int):
        return None
    if not isinstance(tags, dict):
        return None
    tags = {str(k): str(v) for k, v in tags.items()}
    position = element if kind == "node" else element.get("center")
    if not isinstance(position, dict):
        return None
    lat, lon = position.get("lat"), position.get("lon")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    category = category_for_tags(tags, allowed)
    if category is None:
        return None

    wikidata = tags.get("wikidata", "").strip()
    wikipedia = tags.get("wikipedia", "").strip()
    return Poi(
        id=f"{kind}/{ident}",
        name=(tags.get("name") or tags.get("name:en") or tags.get("int_name") or None),
        category=category.key,
        kind=category.kind,
        lon=float(lon),
        lat=float(lat),
        # Only well-formed links are kept: they end up in URLs and API calls.
        wikidata=wikidata if QID_RE.match(wikidata) else None,
        wikipedia=wikipedia if WIKIPEDIA_TAG_RE.match(wikipedia) else None,
        website=_http_url(tags.get("website") or tags.get("contact:website")),
        opening_hours=(tags.get("opening_hours") or "")[:200] or None,
    )


def _http_url(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    return value if value.startswith(("http://", "https://")) and len(value) <= 500 else None


__all__ = [
    "MAX_SERVICE_ELEMENTS",
    "MAX_SIGHT_ELEMENTS",
    "OverpassPoiFetcher",
    "build_along_query",
    "build_bbox_query",
    "parse_pois",
]
