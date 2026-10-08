"""Deutscher Wetterdienst forecasts via Bright Sky (https://brightsky.dev).

Bright Sky is a free, keyless JSON front for DWD's open data (the MOSMIX
station forecasts). DWD's data is open for any use including commercial
(Datenlizenz Deutschland / GeoNutzV -- credit "Deutscher Wetterdienst" is
required), unlike Open-Meteo's free tier, which is non-commercial only. It is
used for routes inside Germany, where it is the authoritative source.

Trade-offs, stated rather than hidden: the forecast is per *station* (the
nearest one, usually within ~10-20 km) rather than a model grid, one request is
needed per point, and there is no UV index or feels-like temperature in it
(reported as missing, never as zero). Bright Sky is a volunteer-run service
without a service-level agreement, hence the fallback to the other providers.
"""

from __future__ import annotations

import asyncio
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
from bike_routing_agent.weather.models import Condition, HourlyWeather

# Generous box around Germany (lon, lat). Forecast stations just across the
# border are as good as German ones, so no polygon is needed.
GERMANY_LON = (5.5, 15.5)
GERMANY_LAT = (47.0, 55.2)

# Bright Sky's `icon` -> the neutral vocabulary. `hail` goes with thunderstorms
# (the most disruptive bucket); `sleet` is wet snow.
_ICON_CONDITIONS: dict[str, Condition] = {
    "clear-day": "clear",
    "clear-night": "clear",
    "partly-cloudy-day": "partly_cloudy",
    "partly-cloudy-night": "partly_cloudy",
    "cloudy": "cloudy",
    "wind": "cloudy",
    "fog": "fog",
    "rain": "rain",
    "sleet": "snow",
    "snow": "snow",
    "hail": "thunderstorm",
    "thunderstorm": "thunderstorm",
}
# The `condition` field (what is falling / fog); "dry" is decided by the icon.
_CONDITION_FALLBACK: dict[str, Condition] = {
    "dry": "clear",
    "fog": "fog",
    "rain": "rain",
    "sleet": "snow",
    "snow": "snow",
    "hail": "thunderstorm",
    "thunderstorm": "thunderstorm",
}


def in_germany(lon: float, lat: float) -> bool:
    return GERMANY_LON[0] <= lon <= GERMANY_LON[1] and GERMANY_LAT[0] <= lat <= GERMANY_LAT[1]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _clamped(value: float | None, low: float, high: float | None = None) -> float | None:
    if value is None or value < low or (high is not None and value > high):
        return None
    return value


def condition_from_record(record: dict[str, Any]) -> Condition:
    """Precipitation/fog wins over the sky: DWD can report ``condition: rain`` with a
    ``cloudy`` icon (the icon follows the cloud cover), and rain is what matters."""
    kind = record.get("condition")
    if isinstance(kind, str) and kind != "dry" and kind in _CONDITION_FALLBACK:
        return _CONDITION_FALLBACK[kind]
    icon = record.get("icon")
    if isinstance(icon, str) and icon in _ICON_CONDITIONS:
        return _ICON_CONDITIONS[icon]
    if kind == "dry":
        return "clear"
    return "unknown"


def _is_day(icon: Any) -> bool | None:
    if isinstance(icon, str):
        if icon.endswith("-day"):
            return True
        if icon.endswith("-night"):
            return False
    return None


class DwdProvider:
    name = "dwd"
    provides = frozenset({"wind_gust_kmh", "precipitation_probability"})
    attribution = "Weather data: Deutscher Wetterdienst (DWD), via Bright Sky"

    def __init__(
        self,
        *,
        base_url: str = "https://api.brightsky.dev/weather",
        timeout_s: float = 8.0,
        user_agent: str = "bike-routing-agent",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._headers = {"User-Agent": user_agent}
        self._client = client

    def covers(self, points: Sequence[tuple[float, float]]) -> bool:
        """Only routes entirely inside (or just beside) Germany are answered here."""
        return bool(points) and all(in_germany(lon, lat) for lon, lat in points)

    async def _get(self, params: dict[str, str]) -> Any:
        try:
            if self._client is not None:
                response = await self._client.get(
                    self._base_url, params=params, headers=self._headers, timeout=self._timeout_s
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.get(
                        self._base_url, params=params, headers=self._headers
                    )
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
            # 404: no station close enough / no data for the window.
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
        return list(
            await asyncio.gather(
                *(self._forecast_point(lon, lat, start, end) for lon, lat in points)
            )
        )

    async def _forecast_point(
        self, lon: float, lat: float, start: datetime, end: datetime
    ) -> list[HourlyWeather]:
        params = {
            "lat": f"{lat:.4f}",
            "lon": f"{lon:.4f}",
            "date": start.astimezone(UTC).strftime("%Y-%m-%dT%H:00"),
            "last_date": end.astimezone(UTC).strftime("%Y-%m-%dT%H:00"),
            "tz": "UTC",
            "units": "dwd",  # km/h wind, mm precipitation, degrees Celsius
        }
        payload = await self._get(params)
        records = payload.get("weather") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            raise ProviderBadResponseError(
                "weather response has no hourly data", provider=self.name
            )
        return [h for h in (self._parse(r) for r in records if isinstance(r, dict)) if h]

    def _parse(self, record: dict[str, Any]) -> HourlyWeather | None:
        stamp = record.get("timestamp")
        if not isinstance(stamp, str):
            return None
        try:
            when = datetime.fromisoformat(stamp).astimezone(UTC)
        except ValueError:
            return None
        return HourlyWeather(
            time=when,
            temperature_c=_number(record.get("temperature")),
            precipitation_mm=_clamped(_number(record.get("precipitation")), 0),
            precipitation_probability=_clamped(
                _number(record.get("precipitation_probability")), 0, 100
            ),
            wind_speed_kmh=_clamped(_number(record.get("wind_speed")), 0),
            wind_gust_kmh=_clamped(_number(record.get("wind_gust_speed")), 0),
            wind_from_deg=_number(record.get("wind_direction")),
            condition=condition_from_record(record),
            is_day=_is_day(record.get("icon")),
        )

    async def health(self) -> dict:
        """One hour for a point in Germany: proves the service answers and parses."""
        try:
            now = datetime.now(UTC)
            await self._forecast_point(10.52, 52.27, now, now)
        except ProviderRateLimitError:
            return {"status": "degraded", "status_code": 429}
        except (ProviderTimeoutError, ProviderUnavailableError):
            return {"status": "unavailable"}
        except Exception:
            return {"status": "degraded"}
        return {"status": "ok"}
