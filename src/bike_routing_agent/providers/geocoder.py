"""OSM Nominatim geocoding adapter."""

from __future__ import annotations

import hashlib
import json
from typing import cast

import httpx

from bike_routing_agent.errors import GeocodingNotFoundError, ProviderBadResponseError
from bike_routing_agent.models import Coordinate, GeocodeCandidate
from bike_routing_agent.providers.base import CacheBackend, InMemoryTTLCache


def _cache_key(query: str, limit: int) -> str:
    return hashlib.sha256(f"{query}|{limit}".encode()).hexdigest()


class NominatimGeocoder:
    """GeocodeProvider backed by the OSM Nominatim search API."""

    name = "nominatim"

    def __init__(
        self,
        *,
        base_url: str,
        user_agent: str,
        timeout_s: float,
        cache: CacheBackend | None = None,
        cache_ttl_s: float = 3600.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent
        self._timeout_s = timeout_s
        self._cache = cache if cache is not None else InMemoryTTLCache()
        self._cache_ttl_s = cache_ttl_s
        self._client = client

    async def geocode(self, query: str, *, limit: int = 5) -> list[GeocodeCandidate]:
        key = _cache_key(query, limit)
        cached = await self._cache.get(key)
        if cached is not None:
            return [GeocodeCandidate.model_validate(c) for c in cast(list, cached)]

        params = {"q": query, "format": "jsonv2", "limit": str(limit), "addressdetails": "0"}
        headers = {"User-Agent": self._user_agent}

        search_url = f"{self._base_url}/search"
        try:
            if self._client is not None:
                response = await self._client.get(
                    search_url, params=params, headers=headers, timeout=self._timeout_s
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.get(search_url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderBadResponseError(
                "geocoder request timed out", provider=self.name, detail={"query": query}
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderBadResponseError(
                f"geocoder returned HTTP {exc.response.status_code}",
                provider=self.name,
                detail={"query": query, "status_code": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderBadResponseError(
                "geocoder request failed",
                provider=self.name,
                detail={"query": query, "error": str(exc)},
            ) from exc
        except json.JSONDecodeError as exc:
            raise ProviderBadResponseError(
                "geocoder returned invalid JSON", provider=self.name, detail={"query": query}
            ) from exc

        if not isinstance(payload, list):
            raise ProviderBadResponseError(
                "unexpected geocoder response shape", provider=self.name, detail={"query": query}
            )

        if not payload:
            raise GeocodingNotFoundError(f"no geocoding results for '{query}'", query=query)

        candidates = self._normalize(payload)
        dumped = [c.model_dump(mode="json") for c in candidates]
        await self._cache.set(key, dumped, ttl_s=self._cache_ttl_s)
        return candidates

    def _normalize(self, payload: list[dict]) -> list[GeocodeCandidate]:
        candidates: list[GeocodeCandidate] = []
        for item in payload:
            try:
                lon = float(item["lon"])
                lat = float(item["lat"])
                label = str(item.get("display_name", ""))
                importance = float(item.get("importance", 0.0))
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderBadResponseError(
                    "malformed geocoder result entry", provider=self.name, detail={"item": item}
                ) from exc
            confidence = max(0.0, min(1.0, importance))
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
