"""MET Norway Locationforecast provider (https://api.met.no): free, no API key.

Terms: data under CC BY 4.0 / NLOD (attribution required); every request must
carry an identifying ``User-Agent`` (generic client names are rejected with
403), coordinates should be sent with at most 4 decimals, and responses should
be cached (we do, through the weather service). The compact format has no
precipitation probability, gusts or UV index in the hourly data used here, so
those fields stay missing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.weather.conditions import condition_from_met_symbol
from bike_routing_agent.weather.models import HourlyWeather

_MS_TO_KMH = 3.6
# Be a polite client: a handful of requests in flight, not one per sample at once.
_MAX_CONCURRENCY = 4


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class MetNoProvider:
    name = "met-no"
    provides: frozenset[str] = frozenset()  # the compact format has none of the optional fields
    attribution = "Weather data from MET Norway (CC BY 4.0)"

    def __init__(
        self,
        *,
        user_agent: str,
        base_url: str = "https://api.met.no/weatherapi/locationforecast/2.0/compact",
        timeout_s: float = 8.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._client = client

    async def _get(self, lon: float, lat: float) -> Any:
        params = {"lat": f"{lat:.4f}", "lon": f"{lon:.4f}"}
        headers = {"User-Agent": self._user_agent}
        try:
            if self._client is not None:
                response = await self._client.get(
                    self._base_url, params=params, headers=headers, timeout=self._timeout_s
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.get(self._base_url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("weather request timed out", provider=self.name) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("weather request failed", provider=self.name) from exc
        if response.status_code == 429:
            raise ProviderRateLimitError("weather rate limit reached", provider=self.name)
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"weather service returned HTTP {response.status_code}", provider=self.name
            )
        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"weather service rejected the request (HTTP {response.status_code})",
                provider=self.name,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(
                "weather service returned invalid JSON", provider=self.name
            ) from exc

    async def forecast(
        self, points: Sequence[tuple[float, float]], start: datetime, end: datetime
    ) -> list[list[HourlyWeather]]:
        semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

        async def one(point: tuple[float, float]) -> list[HourlyWeather]:
            async with semaphore:
                payload = await self._get(*point)
            return self._parse(payload, start, end)

        return list(await asyncio.gather(*(one(p) for p in points)))

    def _parse(self, payload: Any, start: datetime, end: datetime) -> list[HourlyWeather]:
        try:
            timeseries = payload["properties"]["timeseries"]
        except (KeyError, TypeError) as exc:
            raise ProviderBadResponseError(
                "weather response has no timeseries", provider=self.name
            ) from exc
        if not isinstance(timeseries, list):
            raise ProviderBadResponseError("weather response has no timeseries", provider=self.name)

        window_start = start.astimezone(UTC) - timedelta(hours=1)
        window_end = end.astimezone(UTC) + timedelta(hours=1)
        series: list[HourlyWeather] = []
        for entry in timeseries:
            try:
                when = datetime.fromisoformat(str(entry["time"]).replace("Z", "+00:00"))
                data = entry["data"]
                details = data["instant"]["details"]
            except (KeyError, TypeError, ValueError):
                continue
            if not window_start <= when <= window_end:
                continue
            period = data.get("next_1_hours") or data.get("next_6_hours") or {}
            hours = 1 if "next_1_hours" in data else 6
            amount = _number((period.get("details") or {}).get("precipitation_amount"))
            speed = _number(details.get("wind_speed"))
            gust = _number(details.get("wind_speed_of_gust"))
            symbol = (period.get("summary") or {}).get("symbol_code")
            series.append(
                HourlyWeather(
                    time=when,
                    temperature_c=_number(details.get("air_temperature")),
                    # per-hour rate even when only a 6-hour total is available
                    precipitation_mm=None if amount is None or amount < 0 else amount / hours,
                    wind_speed_kmh=None if speed is None or speed < 0 else speed * _MS_TO_KMH,
                    wind_gust_kmh=None if gust is None or gust < 0 else gust * _MS_TO_KMH,
                    wind_from_deg=_number(details.get("wind_from_direction")),
                    condition=condition_from_met_symbol(symbol),
                    is_day=(
                        True
                        if isinstance(symbol, str) and symbol.endswith("_day")
                        else False
                        if isinstance(symbol, str) and symbol.endswith("_night")
                        else None
                    ),
                )
            )
        return series

    async def health(self) -> dict:
        try:
            await self._get(10.0, 52.0)
        except ProviderRateLimitError:
            return {"status": "degraded", "status_code": 429}
        except (ProviderTimeoutError, ProviderUnavailableError):
            return {"status": "unavailable"}
        except Exception:
            return {"status": "degraded"}
        return {"status": "ok"}
