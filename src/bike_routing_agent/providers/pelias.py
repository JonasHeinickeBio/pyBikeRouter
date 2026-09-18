"""Pelias geocoding adapter (self-hosted openrouteservice only)."""

from __future__ import annotations

import hashlib
from typing import Any, cast

from bike_routing_agent.errors import GeocodingNotFoundError, ProviderBadResponseError
from bike_routing_agent.models import Coordinate, GeocodeCandidate
from bike_routing_agent.providers.base import CacheBackend, InMemoryTTLCache
from bike_routing_agent.providers.ors_client import OpenRouteServiceClient


def _cache_key(query: str, limit: int) -> str:
    """SHA-256 digest of the (provider, query, limit) cache identity."""
    return hashlib.sha256(f"pelias|{query}|{limit}".encode()).hexdigest()


class PeliasGeocoder:
    """GeocodeProvider backed by the Pelias search API served by a
    self-hosted openrouteservice instance (``GET /pelias/v1/search``).

    The public ``api.openrouteservice.org`` does not expose Pelias, so this
    adapter is only usable against a self-hosted ORS backend (config.py
    enforces this at startup). Boundary defaults are fixed at construction
    time because the :class:`GeocodeProvider` protocol only passes a query
    and a result limit.

    Transport errors (timeouts, 429, 5xx exhaustion, invalid JSON) are
    mapped to the structured provider errors by the underlying
    :class:`OpenRouteServiceClient` and propagate unchanged, so the
    ``geocode_locations`` node handles them exactly like ORS routing
    failures.
    """

    name = "pelias"

    def __init__(
        self,
        *,
        client: OpenRouteServiceClient,
        cache: CacheBackend | None = None,
        cache_ttl_s: float = 3600.0,
        boundary_countries: str | None = None,
        boundary_geometries: str | None = None,
        boundary_rect: str | None = None,
    ) -> None:
        """Bind the shared ORS client plus the fixed Pelias boundary filters.

        Boundary defaults are constructor-time because the
        :class:`GeocodeProvider` protocol only passes a query and a limit.
        """
        self._client = client
        self._cache = cache if cache is not None else InMemoryTTLCache()
        self._cache_ttl_s = cache_ttl_s
        self._boundary_countries = boundary_countries
        self._boundary_geometries = boundary_geometries
        self._boundary_rect = boundary_rect

    async def geocode(self, query: str, *, limit: int = 5) -> list[GeocodeCandidate]:
        """Resolve a query via ``/pelias/v1/search`` (cache-first), raising
        :class:`GeocodingNotFoundError` on an empty feature list."""
        key = _cache_key(query, limit)
        cached = await self._cache.get(key)
        if cached is not None:
            return [GeocodeCandidate.model_validate(c) for c in cast(list, cached)]

        payload = await self._client.geocode_search(
            query,
            size=limit,
            boundary_countries=self._boundary_countries,
            boundary_geometries=self._boundary_geometries,
            boundary_rect=self._boundary_rect,
        )

        features = payload.get("features")
        if not isinstance(features, list):
            raise ProviderBadResponseError(
                "unexpected pelias response shape", provider=self.name, detail={"query": query}
            )
        if not features:
            raise GeocodingNotFoundError(f"no geocoding results for '{query}'", query=query)

        candidates = self._normalize(features, query)
        dumped = [c.model_dump(mode="json") for c in candidates]
        await self._cache.set(key, dumped, ttl_s=self._cache_ttl_s)
        return candidates

    def _normalize(self, features: list[Any], query: str) -> list[GeocodeCandidate]:
        """Convert GeoJSON features to candidates sorted by confidence."""
        candidates: list[GeocodeCandidate] = []
        for item in features:
            try:
                geometry = item.get("geometry")
                coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
                if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
                    raise ValueError("feature has no geometry.coordinates pair")
                lon = float(coordinates[0])
                lat = float(coordinates[1])
                properties = item.get("properties")
                properties = properties if isinstance(properties, dict) else {}
                label = str(properties.get("label") or properties.get("name") or "")
                # Pelias reports a 0-1 match-confidence per feature; clamp
                # defensively like NominatimGeocoder does for importance.
                confidence = max(0.0, min(1.0, float(properties.get("confidence", 0.0))))
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                raise ProviderBadResponseError(
                    "malformed pelias feature", provider=self.name, detail={"query": query}
                ) from exc
            candidates.append(
                GeocodeCandidate(
                    label=label,
                    coordinate=Coordinate(lon=lon, lat=lat),
                    confidence=confidence,
                    source=self.name,
                )
            )
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        return candidates
