"""The time zone at a place, so opening hours can be read in the local time.

OSM opening hours are written in the local time of the place ("Mo-Fr 09:00-18:00"), while the
arrival at a sight is an instant. Turning one into the other needs the place's IANA zone, which
nothing in the standard library can derive from a coordinate. Open-Meteo (already the default
weather source) names it when asked with ``timezone=auto``, so one small request per route answers
it. The answer is cached by a coarse grid cell (zones are large), and a failure only means the
opening hours are not checked: the lookup never raises.

One zone is asked for per route (at its middle). A ride that crosses a zone border reads the hours
beyond it an hour off; that is the price of one request, and the page says which zone it used.
"""

from __future__ import annotations

import logging
import re

import httpx

from bike_routing_agent.providers.base import CacheBackend, InMemoryTTLCache

logger = logging.getLogger(__name__)

# "Europe/Berlin", "America/Argentina/Buenos_Aires", "Etc/GMT+5", "UTC": an IANA identifier.
_ZONE_RE = re.compile(r"^[A-Za-z]+(?:/[A-Za-z0-9_+\-]+){0,2}\Z")
_TTL_S = 30 * 86_400.0


def valid_zone(value: object) -> str | None:
    """The IANA zone name when ``value`` looks like one, else ``None``."""
    if isinstance(value, str) and len(value) <= 64 and _ZONE_RE.match(value):
        return value
    return None


def _cell(lon: float, lat: float) -> str:
    return f"tz|{round(lat, 1):.1f},{round(lon, 1):.1f}"


class OpenMeteoZoneLookup:
    """``zone_at(lon, lat)`` -> IANA zone name or ``None`` (see the module docs)."""

    def __init__(
        self,
        *,
        base_url: str = "https://api.open-meteo.com/v1/forecast",
        timeout_s: float = 5.0,
        cache: CacheBackend | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._cache = cache if cache is not None else InMemoryTTLCache()
        self._client = client

    async def zone_at(self, lon: float, lat: float) -> str | None:
        key = _cell(lon, lat)
        cached = await self._cache.get(key)
        if isinstance(cached, str) and valid_zone(cached):
            return cached
        zone = await self._ask(lon, lat)
        if zone is not None:
            await self._cache.set(key, zone, ttl_s=_TTL_S)
        return zone

    async def _ask(self, lon: float, lat: float) -> str | None:
        params = {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "timezone": "auto",
            "hourly": "temperature_2m",
            "forecast_hours": "1",
        }
        try:
            if self._client is not None:
                response = await self._client.get(
                    self._base_url, params=params, timeout=self._timeout_s
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.get(self._base_url, params=params)
            if response.status_code != 200:
                logger.info("time zone lookup answered HTTP %s", response.status_code)
                return None
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("time zone lookup failed: %s", exc)
            return None
        return valid_zone(payload.get("timezone")) if isinstance(payload, dict) else None
