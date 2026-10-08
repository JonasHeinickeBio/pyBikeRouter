"""Open-Meteo forecast provider (https://open-meteo.com): free, no API key.

Terms (checked 2026-10): free for non-commercial use, data under CC BY 4.0
(attribution required), fair-use limits of 600 calls/minute, 5,000/hour and
10,000/day. Several locations go in one request, so one plan costs one call.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.weather.conditions import condition_from_wmo
from bike_routing_agent.weather.models import HourlyWeather

HOURLY_VARIABLES = (
    "temperature_2m",
    "apparent_temperature",
    "precipitation",
    "precipitation_probability",
    "weather_code",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "uv_index",
    "is_day",
)


def _at(values: Any, index: int) -> Any:
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class OpenMeteoProvider:
    name = "open-meteo"
    # Optional fields it can supply to fill gaps in another provider's forecast.
    provides = frozenset(
        {"apparent_temperature_c", "uv_index", "wind_gust_kmh", "precipitation_probability"}
    )
    attribution = "Weather data by Open-Meteo.com (CC BY 4.0)"

    def __init__(
        self,
        *,
        base_url: str = "https://api.open-meteo.com/v1/forecast",
        timeout_s: float = 8.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._client = client

    async def _get(self, params: dict[str, str]) -> Any:
        try:
            if self._client is not None:
                response = await self._client.get(
                    self._base_url, params=params, timeout=self._timeout_s
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.get(self._base_url, params=params)
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
        if not points:
            return []
        params = {
            "latitude": ",".join(f"{lat:.4f}" for _, lat in points),
            "longitude": ",".join(f"{lon:.4f}" for lon, _ in points),
            "hourly": ",".join(HOURLY_VARIABLES),
            "wind_speed_unit": "kmh",
            "timezone": "UTC",
            "timeformat": "unixtime",
            "start_hour": start.astimezone(UTC).strftime("%Y-%m-%dT%H:00"),
            "end_hour": end.astimezone(UTC).strftime("%Y-%m-%dT%H:00"),
        }
        payload = await self._get(params)
        # One location answers with an object, several with a list of them.
        locations = payload if isinstance(payload, list) else [payload]
        if len(locations) != len(points) or not all(isinstance(x, dict) for x in locations):
            raise ProviderBadResponseError(
                "unexpected weather response shape", provider=self.name
            )
        return [self._parse(location) for location in locations]

    def _parse(self, location: dict[str, Any]) -> list[HourlyWeather]:
        hourly = location.get("hourly")
        if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
            raise ProviderBadResponseError(
                "weather response has no hourly data", provider=self.name
            )
        columns: dict[str, Any] = hourly

        def number(name: str, i: int, low: float, high: float | None = None) -> float | None:
            return _clamped(_number(_at(columns.get(name), i)), low, high)

        series: list[HourlyWeather] = []
        for i, stamp in enumerate(columns["time"]):
            if not isinstance(stamp, int):
                continue
            code = _at(columns.get("weather_code"), i)
            is_day = _at(columns.get("is_day"), i)
            code = code if isinstance(code, int) and not isinstance(code, bool) else None
            series.append(
                HourlyWeather(
                    time=datetime.fromtimestamp(stamp, tz=UTC),
                    temperature_c=_number(_at(columns.get("temperature_2m"), i)),
                    apparent_temperature_c=_number(_at(columns.get("apparent_temperature"), i)),
                    precipitation_mm=number("precipitation", i, 0),
                    precipitation_probability=number("precipitation_probability", i, 0, 100),
                    wind_speed_kmh=number("wind_speed_10m", i, 0),
                    wind_gust_kmh=number("wind_gusts_10m", i, 0),
                    wind_from_deg=_number(_at(columns.get("wind_direction_10m"), i)),
                    condition=condition_from_wmo(code),
                    weather_code=code,
                    uv_index=number("uv_index", i, 0),
                    is_day=bool(is_day) if isinstance(is_day, (int, bool)) else None,
                )
            )
        return series

    async def health(self) -> dict:
        """One-hour forecast for one point: the cheapest request that proves the
        service answers and parses."""
        try:
            now = datetime.now(UTC)
            await self.forecast([(0.0, 0.0)], now, now)
        except ProviderRateLimitError:
            return {"status": "degraded", "status_code": 429}
        except (ProviderTimeoutError, ProviderUnavailableError):
            return {"status": "unavailable"}
        except Exception:
            return {"status": "degraded"}
        return {"status": "ok"}


def _clamped(value: float | None, low: float, high: float | None) -> float | None:
    """Out-of-range values from a provider read as missing, not as a crash."""
    if value is None or value < low or (high is not None and value > high):
        return None
    return value
