"""Overpass API surface enricher (prototype for issue #3).

Fetches OSM ways inside a buffer around the route geometry and classifies
them through the data-quality policy in ``quality.py``:

    POST {base_url}  data={"data":
        "[out:json][timeout:20];way(around:25,..)[highway];out tags geom;"}

Deliberately prototype-grade, mirroring the routing adapters in
``providers/`` (httpx, retries, structured errors, cache behind the
``CacheBackend`` protocol). The production replacement is a PostGIS
spatial join over a planet extract -- only this class changes, not the
node contract or the summary shape. See docs/enrichment.md.

Overpass quirks absorbed here:

- ``out geom`` geometries are ``{"lat":..,"lon":..}`` objects in lat-first
  order (GeoJSON is lon-first); they are converted to ``Coordinate`` here.
- Route shapes from routing engines can carry thousands of shape points;
  the ``around`` clause is fed a decimated copy (``_MAX_QUERY_POINTS``)
  because query cost grows with the coordinate count.
- Public instances rate limit aggressively; responses are cached under a
  hash of the decimated shape so retries of the same route are free.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Sequence

import httpx

from bike_routing_agent.enrichment.base import SurfaceEnricher, SurfaceSummary
from bike_routing_agent.enrichment.geometry import (
    ObservedWay,
    match_segments_to_ways,
    polyline_length_m,
    route_segments,
)
from bike_routing_agent.enrichment.quality import build_summary, classify_way
from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.models import Coordinate
from bike_routing_agent.providers.base import CacheBackend

USER_AGENT = "bike-routing-agent/0.1"

# Way/around query cost scales with the number of reference points, so long
# route shapes are thinned to this many (first and last always kept).
_MAX_QUERY_POINTS = 400

# A way counts as "on the route" when its polyline passes within this
# multiple of the buffer of a segment midpoint; 2x absorbs the gap between
# the buffer's center-line semantics and midpoint sampling.
_TOLERANCE_FACTOR = 2.0

_MAX_ERROR_BODY_CHARS = 500
_COORD_PRECISION = 5  # decimal places; ~1 m, plenty for way matching


class OverpassEnricher(SurfaceEnricher):
    """``SurfaceEnricher`` backed by a public (or self-hosted) Overpass API."""

    name = "overpass"

    def __init__(
        self,
        *,
        base_url: str = "https://overpass-api.de/api/interpreter",
        timeout_s: float = 20.0,
        max_retries: int = 1,
        buffer_m: float = 25.0,
        cache: CacheBackend | None = None,
        cache_ttl_s: float = 86_400.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if buffer_m <= 0:
            raise ValueError("buffer_m must be positive")
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._buffer_m = buffer_m
        self._cache = cache
        self._cache_ttl_s = cache_ttl_s
        self._client = client

    async def surface_profile(self, coordinates: Sequence[Coordinate]) -> SurfaceSummary:
        route_points = [
            Coordinate(lon=float(p.lon), lat=float(p.lat)) for p in coordinates
        ]
        if len(route_points) < 2:
            raise ProviderBadResponseError(
                "cannot enrich a route with fewer than two shape points",
                provider=self.name,
            )

        query_points = _decimate(route_points, _MAX_QUERY_POINTS)
        cache_key = self._build_cache_key(query_points)
        if self._cache is not None:
            cached = await self._cache.get(cache_key)
            if isinstance(cached, SurfaceSummary):
                return cached

        query = build_overpass_query(
            query_points, buffer_m=self._buffer_m, timeout_s=self._timeout_s
        )
        response = await self._post(query)

        if response.status_code == 429:
            raise ProviderRateLimitError(
                "Overpass rate limited the request",
                provider=self.name,
                detail={"status_code": 429},
            )
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                f"Overpass returned HTTP {response.status_code}",
                provider=self.name,
                detail={
                    "status_code": response.status_code,
                    "body": response.text[:_MAX_ERROR_BODY_CHARS],
                },
            )

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderBadResponseError(
                "Overpass returned invalid JSON",
                provider=self.name,
                detail={"body": response.text[:_MAX_ERROR_BODY_CHARS]},
            ) from exc

        summary = self._summarize(payload, route_points=route_points)
        if self._cache is not None:
            await self._cache.set(cache_key, summary, ttl_s=self._cache_ttl_s)
        return summary

    def _build_cache_key(self, query_points: Sequence[Coordinate]) -> str:
        digest = hashlib.sha256()
        digest.update(f"buffer={self._buffer_m:.1f};".encode())
        for point in query_points:
            digest.update(
                f"{round(point.lat, _COORD_PRECISION)},"
                f"{round(point.lon, _COORD_PRECISION)};".encode()
            )
        return f"overpass:surface:{digest.hexdigest()}"

    async def _post(self, query: str) -> httpx.Response:
        attempt = 0
        while True:
            try:
                if self._client is not None:
                    response = await self._client.post(
                        self._base_url,
                        data={"data": query},
                        headers={"User-Agent": USER_AGENT},
                        timeout=self._timeout_s,
                    )
                else:
                    async with httpx.AsyncClient(
                        timeout=self._timeout_s, headers={"User-Agent": USER_AGENT}
                    ) as client:
                        response = await client.post(self._base_url, data={"data": query})
            except httpx.TimeoutException as exc:
                if attempt >= self._max_retries:
                    raise ProviderTimeoutError(
                        "Overpass request timed out",
                        provider=self.name,
                        detail={"attempts": attempt + 1},
                    ) from exc
                attempt += 1
                await asyncio.sleep(0.5 * attempt)
                continue
            except httpx.HTTPError as exc:
                raise ProviderUnavailableError(
                    "Overpass request failed",
                    provider=self.name,
                    detail={"error": str(exc)},
                ) from exc

            if response.status_code >= 500:
                if attempt >= self._max_retries:
                    raise ProviderUnavailableError(
                        f"Overpass returned HTTP {response.status_code}",
                        provider=self.name,
                        detail={
                            "status_code": response.status_code,
                            "body": response.text[:_MAX_ERROR_BODY_CHARS],
                            "attempts": attempt + 1,
                        },
                    )
                attempt += 1
                await asyncio.sleep(0.5 * attempt)
                continue
            return response

    def _summarize(
        self, payload: object, *, route_points: Sequence[Coordinate]
    ) -> SurfaceSummary:
        ways = _parse_ways(payload, provider=self.name)
        segments = route_segments(route_points)
        total_m = polyline_length_m(route_points)
        if not ways:
            # A corridor with no mapped ways at all is 100% unknown data,
            # not a perfect surface profile.
            return SurfaceSummary(total_m=total_m, unknown_fraction=1.0)

        matches = match_segments_to_ways(
            segments, ways, tolerance_m=self._buffer_m * _TOLERANCE_FACTOR
        )
        return build_summary(
            [
                (segment.length_m, classify_way(way.tags) if way else None)
                for segment, way in zip(segments, matches, strict=True)
            ]
        )

    async def health(self) -> dict:
        """Cheap capability probe: an empty area query answers 200 on a
        healthy interpreter and exposes load/runtime errors otherwise."""
        try:
            if self._client is not None:
                response = await self._client.post(
                    self._base_url,
                    data={"data": "[out:json][timeout:1];node(1);out;"},
                    headers={"User-Agent": USER_AGENT},
                    timeout=self._timeout_s,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout_s, headers={"User-Agent": USER_AGENT}
                ) as client:
                    response = await client.post(
                        self._base_url, data={"data": "[out:json][timeout:1];node(1);out;"}
                    )
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            return {"status": "unavailable", "error": str(exc)}
        if response.status_code == 200:
            return {"status": "ok"}
        return {"status": "degraded", "status_code": response.status_code}


def build_overpass_query(
    coordinates: Sequence[Coordinate], *, buffer_m: float, timeout_s: float
) -> str:
    """Render the ``way(around:..)[highway]`` overpass-ql query for a route shape.

    The ``[highway]`` selector keeps the result set to roads and paths: an
    unfiltered around-query also returns buildings, barriers and land-use
    polygons, whose geometries can out-close a corridor segment and poison
    both surface classification and the road-class mix.
    """
    points = _decimate(coordinates, _MAX_QUERY_POINTS)
    point_list = ",".join(f"{p.lat:.6f},{p.lon:.6f}" for p in points)
    timeout = max(1, math.ceil(timeout_s))
    return (
        f"[out:json][timeout:{timeout}];"
        f"way(around:{buffer_m:.1f},{point_list})[highway];"
        "out tags geom;"
    )


def _decimate(
    coordinates: Sequence[Coordinate], max_points: int
) -> list[Coordinate]:
    if len(coordinates) <= max_points:
        return list(coordinates)
    step = (len(coordinates) - 1) / (max_points - 1)
    picked = [coordinates[round(i * step)] for i in range(max_points)]
    picked[-1] = coordinates[-1]
    return picked


def _parse_ways(payload: object, *, provider: str) -> list[ObservedWay]:
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise ProviderBadResponseError(
            "malformed Overpass response",
            provider=provider,
            detail={"payload_keys": list(payload) if isinstance(payload, dict) else None},
        )
    ways: list[ObservedWay] = []
    for element in payload["elements"]:
        way = _parse_way(element)
        if way is not None:
            ways.append(way)
    return ways


def _parse_way(element: object) -> ObservedWay | None:
    """Decode one ``way`` element with tags and ``out geom`` geometry.

    Malformed elements are skipped rather than failing the whole corridor:
    partial map data is the norm, and what cannot be read simply becomes
    unmatched (unknown) length.
    """
    if not isinstance(element, dict) or element.get("type") != "way":
        return None
    tags = element.get("tags")
    geometry = element.get("geometry")
    way_id = element.get("id")
    if not isinstance(tags, dict) or not isinstance(geometry, list):
        return None
    if not isinstance(way_id, int) or isinstance(way_id, bool):
        return None

    if len(geometry) < 2:
        return None
    points: list[Coordinate] = []
    for item in geometry:
        point = _parse_geometry_point(item)
        if point is None:
            return None  # half-decoded geometry is worse than none
        points.append(point)
    return ObservedWay(way_id=way_id, tags=tags, points=points)


def _parse_geometry_point(item: object) -> Coordinate | None:
    """One ``out geom`` point: ``{"lat": .., "lon": ..}`` (lat-first)."""
    if isinstance(item, dict):
        lat = item.get("lat")
        lon = item.get("lon")
        if (
            isinstance(lat, (int, float))
            and not isinstance(lat, bool)
            and isinstance(lon, (int, float))
            and not isinstance(lon, bool)
        ):
            return Coordinate(lon=float(lon), lat=float(lat))
        return None
    # Lenient fallback for [lat, lon] array forms some gateways emit.
    if (
        isinstance(item, (list, tuple))
        and len(item) == 2
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in item)
    ):
        lat, lon = item
        return Coordinate(lon=float(lon), lat=float(lat))
    return None
