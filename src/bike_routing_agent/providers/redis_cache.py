"""Redis-backed ``CacheBackend`` (issue #29): geocode and Overpass results
shared by every API instance.

A cache is an optimisation, never a dependency: **every Redis failure
degrades to a cache miss**. ``get`` returns ``None`` and ``set`` does nothing
(neither ever raises), and after a failure a short circuit-open period skips
Redis entirely so a dead server does not add its timeout to every request.

Values are stored as JSON (never pickle: a shared cache must not be able to
execute code on the reader). Keys are ``<prefix>:v<N>:<key>``; bumping
``SCHEMA_VERSION`` when a cached value's shape changes invalidates old
entries without a manual flush.

``redis`` is an optional dependency (the ``cache`` extra), imported when the
backend is built.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Bump when the JSON shape of any cached value changes.
SCHEMA_VERSION = 1


class RedisCacheBackend:
    def __init__(
        self,
        url: str,
        *,
        prefix: str = "bike-routing",
        timeout_s: float = 2.0,
        retry_after_s: float = 30.0,
        client: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._prefix = f"{prefix}:v{SCHEMA_VERSION}:"
        self._retry_after_s = retry_after_s
        self._clock = clock
        self._open_until = 0.0
        self._client = client if client is not None else self._build_client(url, timeout_s)

    @staticmethod
    def _build_client(url: str, timeout_s: float) -> Any:
        try:
            from redis import asyncio as redis_asyncio
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise RuntimeError(
                "CACHE_BACKEND=redis needs the redis client; install the cache extra: "
                "pip install 'bike-routing-agent[cache]'"
            ) from exc
        return redis_asyncio.from_url(
            url,
            socket_connect_timeout=timeout_s,
            socket_timeout=timeout_s,
            decode_responses=True,
        )

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    @property
    def circuit_open(self) -> bool:
        return self._clock() < self._open_until

    def _tripped(self, operation: str, exc: Exception) -> None:
        if not self.circuit_open:  # one warning per outage, not one per request
            logger.warning(
                "redis cache %s failed (%s: %s); serving uncached for %.0f s",
                operation,
                type(exc).__name__,
                exc,
                self._retry_after_s,
            )
        self._open_until = self._clock() + self._retry_after_s

    async def get(self, key: str) -> object | None:
        if self.circuit_open:
            return None
        try:
            raw = await self._client.get(self._key(key))
        except Exception as exc:  # RedisError, OSError, TimeoutError, ...
            self._tripped("get", exc)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            # Someone else's data under our key: a miss, and not worth tripping.
            logger.warning("redis cache entry for %r is not valid JSON; ignoring it", key)
            return None

    async def set(self, key: str, value: object, *, ttl_s: float) -> None:
        if self.circuit_open:
            return
        try:
            payload = json.dumps(value)
        except (TypeError, ValueError):
            logger.warning("value for cache key %r is not JSON-serialisable; not cached", key)
            return
        try:
            await self._client.set(self._key(key), payload, px=max(1, int(ttl_s * 1000)))
        except Exception as exc:
            self._tripped("set", exc)

    async def ping(self) -> dict[str, str]:
        """Readiness probe; never raises (a down cache is degraded, not fatal)."""
        try:
            await self._client.ping()
        except Exception:
            logger.warning("redis cache ping failed", exc_info=True)
            return {"status": "unavailable"}
        return {"status": "ok"}

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()
