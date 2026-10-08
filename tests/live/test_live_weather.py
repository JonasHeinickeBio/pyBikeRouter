"""Live check of the real weather APIs (excluded from the default run).

    poetry run pytest -m live tests/live/test_live_weather.py

Both services are free and keyless; MET Norway needs an identifying User-Agent.
"""

from datetime import UTC, datetime, timedelta

import pytest

from bike_routing_agent.weather.dwd import DwdProvider
from bike_routing_agent.weather.met_no import MetNoProvider
from bike_routing_agent.weather.open_meteo import OpenMeteoProvider

pytestmark = pytest.mark.live

POINTS = [(10.5267, 52.2689), (10.5361, 52.1688)]  # Braunschweig, Wolfenbüttel


def window():
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return start, start + timedelta(hours=3)


async def test_open_meteo_answers_with_plausible_hourly_values():
    start, end = window()
    provider = OpenMeteoProvider()
    series = await provider.forecast(POINTS, start, end)
    assert len(series) == 2 and all(len(s) >= 3 for s in series)
    hour = series[0][0]
    assert -40 < hour.temperature_c < 50 and 0 <= hour.wind_speed_kmh < 200
    assert hour.wind_from_deg is not None and 0 <= hour.wind_from_deg <= 360
    assert (await provider.health()) == {"status": "ok"}


async def test_met_no_answers_with_plausible_hourly_values():
    start, end = window()
    provider = MetNoProvider(user_agent="bike-routing-agent-tests/0.1 github.com/JonasHeinickeBio")
    series = await provider.forecast(POINTS[:1], start, end)
    assert len(series[0]) >= 3
    hour = series[0][0]
    assert -40 < hour.temperature_c < 50 and hour.wind_speed_kmh is not None


async def test_dwd_via_bright_sky_answers_for_germany():
    start, end = window()
    provider = DwdProvider(user_agent="bike-routing-agent-tests/0.1 github.com/JonasHeinickeBio")
    series = await provider.forecast(POINTS, start, end)
    assert len(series) == 2 and all(len(s) >= 3 for s in series)
    hour = series[0][0]
    assert -40 < hour.temperature_c < 50 and 0 <= hour.wind_speed_kmh < 200
    assert hour.wind_from_deg is not None and 0 <= hour.wind_from_deg <= 360
    assert hour.uv_index is None  # DWD publishes none; missing, not zero
    assert (await provider.health()) == {"status": "ok"}
