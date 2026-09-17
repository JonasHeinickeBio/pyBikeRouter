"""Provider protocols and a minimal cache abstraction.

Adapters (ORS, Valhalla, a geocoder, ...) implement these Protocols. Graph
nodes and scoring code depend only on these interfaces, never on a concrete
adapter, so a new backend can be swapped in without touching orchestration.
"""

from __future__ import annotations

import time
from typing import Protocol

from bike_routing_agent.models import GeocodeCandidate, RouteCandidate, RoutingRequest


class RoutingProvider(Protocol):
    name: str

    async def route(self, request: RoutingRequest) -> RouteCandidate: ...

    async def health(self) -> dict: ...


class GeocodeProvider(Protocol):
    name: str

    async def geocode(self, query: str, *, limit: int = 5) -> list[GeocodeCandidate]: ...


class CacheBackend(Protocol):
    async def get(self, key: str) -> object | None: ...

    async def set(self, key: str, value: object, *, ttl_s: float) -> None: ...


class InMemoryTTLCache:
    """Process-local TTL cache. Adequate for a single API instance; swap for
    a shared backend (Redis, etc.) behind the same CacheBackend protocol for
    multi-instance deployments."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, object]] = {}

    async def get(self, key: str) -> object | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: object, *, ttl_s: float) -> None:
        self._store[key] = (time.monotonic() + ttl_s, value)
