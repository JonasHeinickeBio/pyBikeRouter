"""WeatherService (fallback, cache) and the weather node, with fake providers."""

from datetime import UTC, datetime, timedelta

import pytest

from bike_routing_agent.errors import ProviderRateLimitError, ProviderUnavailableError
from bike_routing_agent.nodes.weather import build_weather_node
from bike_routing_agent.providers.base import InMemoryTTLCache
from bike_routing_agent.weather.models import HourlyWeather
from bike_routing_agent.weather.service import WeatherService

NOW = datetime(2026, 10, 7, 12, 30, tzinfo=UTC)


def hourly(start: datetime, hours: int, **kw) -> list[HourlyWeather]:
    base = start.replace(minute=0, second=0, microsecond=0)
    return [HourlyWeather(time=base + timedelta(hours=i), **kw) for i in range(hours)]


class FakeProvider:
    def __init__(self, name="fake", *, error=None, wind_from=270.0, hours=12, health="ok"):
        self.name = name
        self.attribution = f"data by {name}"
        self.error = error
        self.wind_from = wind_from
        self.hours = hours
        self.calls: list[tuple] = []
        self._health = health

    async def forecast(self, points, start, end):
        self.calls.append((list(points), start, end))
        if self.error:
            raise self.error
        return [
            hourly(
                start,
                self.hours,
                temperature_c=11.0,
                wind_speed_kmh=20.0,
                wind_from_deg=self.wind_from,
                condition="clear",
                precipitation_probability=5,
            )
            for _ in points
        ]

    async def health(self):
        return {"status": self._health}


# -- service ----------------------------------------------------------------------------


async def test_the_first_working_provider_wins_and_is_credited():
    first, second = FakeProvider("a"), FakeProvider("b")
    forecast = await WeatherService([first, second]).forecast([(10.0, 52.0)], NOW, NOW)
    assert forecast.provider == "a" and forecast.attribution == "data by a"
    assert second.calls == []


async def test_a_failing_provider_falls_back_to_the_next(caplog):
    failing = FakeProvider("a", error=ProviderRateLimitError("slow down", provider="a"))
    backup = FakeProvider("b")
    forecast = await WeatherService([failing, backup]).forecast([(10.0, 52.0)], NOW, NOW)
    assert forecast.provider == "b" and len(failing.calls) == 1


async def test_when_every_provider_fails_the_error_says_so():
    service = WeatherService(
        [FakeProvider("a", error=RuntimeError("x")), FakeProvider("b", error=OSError("y"))]
    )
    with pytest.raises(ProviderUnavailableError, match="no weather provider"):
        await service.forecast([(10.0, 52.0)], NOW, NOW)


async def test_results_are_cached_per_points_and_window():
    provider = FakeProvider()
    service = WeatherService([provider], cache=InMemoryTTLCache())
    first = await service.forecast([(10.0, 52.0)], NOW, NOW + timedelta(hours=2))
    again = await service.forecast([(10.0, 52.0)], NOW, NOW + timedelta(hours=2))
    assert len(provider.calls) == 1
    assert again.series == first.series and again.retrieved_at == first.retrieved_at
    await service.forecast([(10.0, 52.0)], NOW, NOW + timedelta(hours=3))  # other window
    await service.forecast([(10.1, 52.0)], NOW, NOW + timedelta(hours=2))  # other point
    assert len(provider.calls) == 3


async def test_a_cached_entry_is_not_used_for_another_provider():
    cache = InMemoryTTLCache()
    a = FakeProvider("a")
    await WeatherService([a], cache=cache).forecast([(10.0, 52.0)], NOW, NOW)
    b = FakeProvider("b")
    forecast = await WeatherService([b], cache=cache).forecast([(10.0, 52.0)], NOW, NOW)
    assert forecast.provider == "b" and len(b.calls) == 1


async def test_failures_are_not_cached():
    provider = FakeProvider(error=RuntimeError("down"))
    service = WeatherService([provider], cache=InMemoryTTLCache())
    for _ in range(2):
        with pytest.raises(ProviderUnavailableError):
            await service.forecast([(10.0, 52.0)], NOW, NOW)
    assert len(provider.calls) == 2


async def test_an_unreadable_cache_entry_is_refetched():
    cache = InMemoryTTLCache()
    provider = FakeProvider()
    service = WeatherService([provider], cache=cache)
    await service.forecast([(10.0, 52.0)], NOW, NOW)
    for key in list(cache._store):
        cache._store[key] = (cache._store[key][0], {"series": "garbage"})
    await service.forecast([(10.0, 52.0)], NOW, NOW)
    assert len(provider.calls) == 2


def test_a_service_needs_a_provider():
    with pytest.raises(ValueError):
        WeatherService([])


@pytest.mark.parametrize(
    ("healths", "expected"),
    [
        (["ok", "ok"], "ok"),
        (["ok", "unavailable"], "degraded"),
        (["degraded", "unavailable"], "unavailable"),
    ],
)
async def test_service_health_summarises_the_providers(healths, expected):
    service = WeatherService([FakeProvider(f"p{i}", health=h) for i, h in enumerate(healths)])
    assert (await service.health())["status"] == expected


# -- node ----------------------------------------------------------------------------------------


def candidate(provider="ors", *, distance_m=20_000, duration_s=3600, lat0=52.0, geometry=None):
    return {
        "provider": provider,
        "provider_profile": "p",
        "geometry_geojson": geometry
        or {
            "type": "LineString",
            "coordinates": [[10.0, lat0], [10.0, lat0 + 0.18]],
        },  # ~20 km north
        "metrics": {"distance_m": distance_m, "duration_s": duration_s},
        "score": 0.9,
        "warnings": [],
    }


def node(provider=None, **kw):
    service = WeatherService([provider or FakeProvider()])
    return build_weather_node(service=service, now=lambda: NOW, **kw), service


def state(*candidates, **extra):
    return {"candidates": list(candidates), "selected_candidate": candidates[0], **extra}


async def test_weather_is_attached_to_every_candidate_and_the_selected_one():
    run, _ = node()
    update = await run(state(candidate("ors"), candidate("brouter", lat0=52.3)))

    assert update["weather_status"] == "ok"
    assert all(c["weather"]["provider"] == "fake" for c in update["candidates"])
    assert update["selected_candidate"] == update["candidates"][0]
    w = update["candidates"][0]["weather"]
    assert w["departure"].startswith("2026-10-07T12:30") and w["duration_source"] == "provider"
    # riding north with a west wind (270): pure crosswind, no head/tailwind
    assert w["summary"]["headwind_mean_kmh"] == pytest.approx(0, abs=0.5)
    assert w["samples"][0]["crosswind_kmh"] == pytest.approx(-20, abs=0.5)  # from the left


async def test_the_selected_candidate_follows_its_rank_not_its_position():
    run, _ = node()
    first, second = candidate("ors"), candidate("brouter", lat0=52.3)
    update = await run({"candidates": [first, second], "selected_candidate": second})
    assert update["selected_candidate"]["provider"] == "brouter"


async def test_a_requested_departure_shifts_the_forecast_window():
    provider = FakeProvider()
    run, _ = node(provider)
    departure = (NOW + timedelta(hours=5)).isoformat()
    update = await run(state(candidate(), departure_time=departure))
    _, start, end = provider.calls[0]
    assert start == datetime(2026, 10, 7, 17, 0, tzinfo=UTC) and end == datetime(
        2026, 10, 7, 19, 0, tzinfo=UTC
    )
    assert update["candidates"][0]["weather"]["departure"].startswith("2026-10-07T17:30")


async def test_the_forecast_hour_follows_the_position_along_the_ride():
    run, _ = node(max_samples=3)
    update = await run(state(candidate(duration_s=7200)))
    times = [s["time"] for s in update["candidates"][0]["weather"]["samples"]]
    assert [t[11:16] for t in times] == ["12:30", "13:30", "14:30"]


async def test_a_missing_duration_is_estimated_and_says_so():
    run, _ = node()
    c = candidate(distance_m=15_000)
    c["metrics"]["duration_s"] = None
    update = await run(state(c))
    w = update["candidates"][0]["weather"]
    assert w["duration_source"] == "estimated"
    assert (
        datetime.fromisoformat(w["arrival"]) - datetime.fromisoformat(w["departure"])
    ).total_seconds() == 3600


async def test_a_stale_departure_is_read_as_now_and_a_far_one_is_not_covered():
    run, _ = node()
    stale = (NOW - timedelta(hours=6)).isoformat()
    update = await run(state(candidate(), departure_time=stale))
    assert update["candidates"][0]["weather"]["departure"].startswith("2026-10-07T12:30")
    far = (NOW + timedelta(days=40)).isoformat()
    assert await run(state(candidate(), departure_time=far)) == {"weather_status": "not_covered"}
    garbage = await run(state(candidate(), departure_time="next tuesday-ish"))
    assert garbage["weather_status"] == "ok"  # unreadable -> now


async def test_naive_and_zulu_departures_are_read_as_utc():
    run, _ = node()
    for text in ("2026-10-07T15:00:00", "2026-10-07T15:00:00Z", "2026-10-07T17:00:00+02:00"):
        update = await run(state(candidate(), departure_time=text))
        assert update["candidates"][0]["weather"]["departure"].startswith("2026-10-07T15:00"), text


async def test_one_lookup_serves_all_candidates_and_nearby_points_share_a_cell():
    provider = FakeProvider()
    run, _ = node(provider, max_samples=2)
    await run(state(candidate("a"), candidate("b", lat0=52.0001)))  # almost the same route
    assert len(provider.calls) == 1
    points = provider.calls[0][0]
    assert len(points) == 2  # start and end cells, shared by both candidates


async def test_a_provider_outage_leaves_the_plan_alone():
    run, _ = node(FakeProvider(error=RuntimeError("down")))
    original = candidate()
    update = await run(state(original))
    assert update == {"weather_status": "unavailable"}


async def test_a_forecast_that_does_not_cover_the_ride_is_reported_as_such():
    run, _ = node(FakeProvider(hours=1))  # only the departure hour
    update = await run(state(candidate(duration_s=4 * 3600)))
    # the departure sample is covered, so weather exists, but later ones were dropped
    assert update["weather_status"] == "ok"
    assert len(update["candidates"][0]["weather"]["samples"]) < 3

    far_future = FakeProvider(hours=0)
    run2, _ = node(far_future)
    assert (await run2(state(candidate())))["weather_status"] == "not_covered"


async def test_unusable_geometry_is_skipped_without_a_lookup():
    provider = FakeProvider()
    run, _ = node(provider)
    update = await run(state(candidate(geometry={"type": "Point", "coordinates": [1, 2]})))
    assert update == {"weather_status": "skipped"} and provider.calls == []


async def test_without_a_service_or_candidates_the_node_does_nothing():
    off = build_weather_node(service=None, now=lambda: NOW)
    assert await off(state(candidate())) == {}
    run, _ = node()
    assert await run({"candidates": []}) == {}
