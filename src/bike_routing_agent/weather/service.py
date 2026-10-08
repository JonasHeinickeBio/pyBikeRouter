"""Weather lookup with provider fallback and caching.

Providers are tried in order; the first that answers wins, so a free service
being down (or rate-limiting us) costs one failed call, not the feature. Results
are cached per provider, keyed by the (rounded) points and the hour window, so
repeated plans for the same area and time reuse one upstream request -- which is
also what the providers' terms ask of well-behaved clients.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, cast

from bike_routing_agent.errors import ProviderUnavailableError
from bike_routing_agent.providers.base import CacheBackend, InMemoryTTLCache
from bike_routing_agent.weather.base import WeatherProvider
from bike_routing_agent.weather.merge import fill_gaps, missing_fields
from bike_routing_agent.weather.models import HourlyWeather

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeatherForecast:
    provider: str
    attribution: str
    series: list[list[HourlyWeather]]
    retrieved_at: datetime
    # Every provider whose data is in ``series``, the primary one first.
    sources: tuple[str, ...] = ()


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
        merge: bool = False,
    ) -> None:
        """``merge``: after the first provider answers, ask the others that can supply
        what it lacks (see ``weather/merge.py``) and fill only those gaps."""
        if not providers:
            raise ValueError("WeatherService needs at least one provider")
        self._providers = list(providers)
        self._cache = cache if cache is not None else InMemoryTTLCache()
        self._cache_ttl_s = cache_ttl_s
        self._merge = merge

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self._providers]

    @staticmethod
    def _covers(provider: WeatherProvider, points: Sequence[tuple[float, float]]) -> bool:
        # A regional provider (DWD) declines routes it does not cover.
        covers = getattr(provider, "covers", None)
        return covers is None or bool(covers(points))

    async def _fetch(
        self,
        provider: WeatherProvider,
        points: Sequence[tuple[float, float]],
        start: datetime,
        end: datetime,
    ) -> tuple[list[list[HourlyWeather]], datetime]:
        """One provider's forecast, from the cache when fresh. Raises when it fails."""
        key = _cache_key(provider.name, points, start, end)
        cached = await self._cache.get(key)
        if isinstance(cached, dict):
            try:
                return (
                    [
                        [HourlyWeather.model_validate(h) for h in per_point]
                        for per_point in cast(list[list[Any]], cached["series"])
                    ],
                    datetime.fromisoformat(cached["retrieved_at"]),
                )
            except (KeyError, TypeError, ValueError):
                logger.warning("ignoring unreadable cached weather entry")
        series = await provider.forecast(points, start, end)
        retrieved_at = datetime.now(UTC)
        await self._cache.set(
            key,
            {
                "retrieved_at": retrieved_at.isoformat(),
                "series": [[h.model_dump(mode="json") for h in s] for s in series],
            },
            ttl_s=self._cache_ttl_s,
        )
        return series, retrieved_at

    async def forecast(
        self, points: Sequence[tuple[float, float]], start: datetime, end: datetime
    ) -> WeatherForecast:
        last_error: Exception | None = None
        for index, provider in enumerate(self._providers):
            if not self._covers(provider, points):
                continue
            try:
                series, retrieved_at = await self._fetch(provider, points, start, end)
            except Exception as exc:  # a provider failing is an expected, recoverable event
                logger.warning("weather provider %s failed: %s", provider.name, exc)
                last_error = exc
                continue
            forecast = WeatherForecast(
                provider.name, provider.attribution, series, retrieved_at, (provider.name,)
            )
            if self._merge:
                forecast = await self._fill_gaps(
                    forecast, self._providers[index + 1 :], points, start, end
                )
            return forecast
        if last_error is None:
            raise ProviderUnavailableError(
                "no weather provider covers these points", provider="weather"
            )
        raise ProviderUnavailableError(
            "no weather provider could be reached", provider="weather"
        ) from last_error

    async def _fill_gaps(
        self,
        forecast: WeatherForecast,
        others: Sequence[WeatherProvider],
        points: Sequence[tuple[float, float]],
        start: datetime,
        end: datetime,
    ) -> WeatherForecast:
        """Ask only providers that can supply a missing field, concurrently, best effort."""
        gaps = missing_fields(forecast.series)
        useful = [
            p for p in others if gaps & set(getattr(p, "provides", ())) and self._covers(p, points)
        ]
        if not gaps or not useful:
            return forecast
        results = await asyncio.gather(
            *(self._fetch(p, points, start, end) for p in useful), return_exceptions=True
        )
        series, sources, credits = forecast.series, list(forecast.sources), [forecast.attribution]
        for provider, result in zip(useful, results, strict=True):
            if isinstance(result, BaseException):
                logger.info("weather provider %s could not fill gaps: %s", provider.name, result)
                continue
            gaps = missing_fields(series)
            series, filled = fill_gaps(series, result[0], gaps)
            if filled:
                sources.append(provider.name)
                credits.append(provider.attribution)
        if len(sources) == 1:
            return forecast
        return replace(
            forecast, series=series, sources=tuple(sources), attribution="; ".join(credits)
        )

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
