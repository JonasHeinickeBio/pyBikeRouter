"""Weather lookup with provider fallback and caching.

Providers are tried in order; the first that answers wins, so a free service
being down (or rate-limiting us) costs one failed call, not the feature. Results
are cached per provider, keyed by the (rounded) points and the hour window, so
repeated plans for the same area and time reuse one upstream request -- which is
also what the providers' terms ask of well-behaved clients.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from bike_routing_agent.errors import ProviderUnavailableError
from bike_routing_agent.providers.base import CacheBackend, InMemoryTTLCache
from bike_routing_agent.weather.base import WeatherProvider
from bike_routing_agent.weather.models import HourlyWeather

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeatherForecast:
    provider: str
    attribution: str
    series: list[list[HourlyWeather]]
    retrieved_at: datetime


def _cache_key(
    provider: str, points: Sequence[tuple[float, float]], start: datetime, end: datetime
) -> str:
    where = ";".join(f"{lon:.4f},{lat:.4f}" for lon, lat in points)
    window = f"{start.astimezone(UTC).isoformat()}/{end.astimezone(UTC).isoformat()}"
    return hashlib.sha256(f"weather|{provider}|{window}|{where}".encode()).hexdigest()


class WeatherService:
    def __init__(
        self,
        providers: Sequence[WeatherProvider],
        *,
        cache: CacheBackend | None = None,
        cache_ttl_s: float = 1800.0,
    ) -> None:
        if not providers:
            raise ValueError("WeatherService needs at least one provider")
        self._providers = list(providers)
        self._cache = cache if cache is not None else InMemoryTTLCache()
        self._cache_ttl_s = cache_ttl_s

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self._providers]

    async def forecast(
        self, points: Sequence[tuple[float, float]], start: datetime, end: datetime
    ) -> WeatherForecast:
        last_error: Exception | None = None
        for provider in self._providers:
            # A regional provider (DWD) is skipped for routes it does not cover.
            covers = getattr(provider, "covers", None)
            if covers is not None and not covers(points):
                continue
            key = _cache_key(provider.name, points, start, end)
            cached = await self._cache.get(key)
            if isinstance(cached, dict):
                try:
                    return WeatherForecast(
                        provider=provider.name,
                        attribution=provider.attribution,
                        series=[
                            [HourlyWeather.model_validate(h) for h in per_point]
                            for per_point in cast(list[list[Any]], cached["series"])
                        ],
                        retrieved_at=datetime.fromisoformat(cached["retrieved_at"]),
                    )
                except (KeyError, TypeError, ValueError):
                    logger.warning("ignoring unreadable cached weather entry")
            try:
                series = await provider.forecast(points, start, end)
            except Exception as exc:  # a provider failing is an expected, recoverable event
                logger.warning("weather provider %s failed: %s", provider.name, exc)
                last_error = exc
                continue
            retrieved_at = datetime.now(UTC)
            await self._cache.set(
                key,
                {
                    "retrieved_at": retrieved_at.isoformat(),
                    "series": [[h.model_dump(mode="json") for h in s] for s in series],
                },
                ttl_s=self._cache_ttl_s,
            )
            return WeatherForecast(provider.name, provider.attribution, series, retrieved_at)
        if last_error is None:
            raise ProviderUnavailableError(
                "no weather provider covers these points", provider="weather"
            )
        raise ProviderUnavailableError(
            "no weather provider could be reached", provider="weather"
        ) from last_error

    async def health(self) -> dict[str, Any]:
        """``ok`` when every provider answers, ``degraded`` when only some do
        (the fallback still works), ``unavailable`` when none does."""
        results = [await p.health() for p in self._providers]
        statuses = [r.get("status") for r in results]
        if all(s == "ok" for s in statuses):
            return {"status": "ok"}
        if any(s == "ok" for s in statuses):
            return {"status": "degraded"}
        return {"status": "unavailable"}
