"""Weather provider protocol."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from bike_routing_agent.weather.models import HourlyWeather


class WeatherProvider(Protocol):
    name: str
    # The credit the provider's licence requires wherever its data is shown.
    attribution: str

    async def forecast(
        self, points: Sequence[tuple[float, float]], start: datetime, end: datetime
    ) -> list[list[HourlyWeather]]:
        """Hourly forecasts covering ``[start, end]`` (UTC), one list per ``(lon, lat)``
        point in the order given."""
        ...

    async def health(self) -> dict: ...
