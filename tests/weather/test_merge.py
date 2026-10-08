"""Filling one provider's forecast gaps from another's -- pure merge and the service."""

from datetime import UTC, datetime, timedelta

import pytest

from bike_routing_agent.errors import ProviderUnavailableError
from bike_routing_agent.weather.merge import fill_gaps, missing_fields
from bike_routing_agent.weather.models import HourlyWeather
from bike_routing_agent.weather.service import WeatherService

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
POINTS = [(10.52, 52.27), (10.53, 52.16)]


def hour(offset_h=0, minutes=0, **kw) -> HourlyWeather:
    return HourlyWeather(time=T0 + timedelta(hours=offset_h, minutes=minutes), **kw)


# -- fill_gaps ---------------------------------------------------------------------------


def test_only_missing_fields_are_filled_and_the_primary_is_never_overwritten():
    primary = [[hour(0, temperature_c=10, wind_gust_kmh=30)]]
    secondary = [
        [hour(0, temperature_c=99, wind_gust_kmh=99, uv_index=4, apparent_temperature_c=7)]
    ]
    merged, filled = fill_gaps(primary, secondary, missing_fields(primary))
    got = merged[0][0]
    assert (got.temperature_c, got.wind_gust_kmh) == (10, 30)  # primary wins, nothing averaged
    assert (got.uv_index, got.apparent_temperature_c) == (4, 7)
    assert filled == {"uv_index", "apparent_temperature_c"}


def test_core_fields_are_never_taken_from_a_secondary():
    primary = [[hour(0)]]  # no temperature, wind or condition either
    secondary = [[hour(0, temperature_c=5, wind_speed_kmh=20, wind_from_deg=90, uv_index=2)]]
    merged, filled = fill_gaps(primary, secondary, {"temperature_c", "wind_speed_kmh", "uv_index"})
    got = merged[0][0]
    assert got.temperature_c is None and got.wind_speed_kmh is None and got.uv_index == 2
    assert filled == {"uv_index"}


def test_hours_are_matched_by_time_within_half_an_hour():
    primary = [[hour(0), hour(1), hour(2)]]
    secondary = [[hour(1, uv_index=3), hour(2, minutes=20, uv_index=5)]]
    merged, _ = fill_gaps(primary, secondary, {"uv_index"})
    assert [h.uv_index for h in merged[0]] == [None, 3, 5]  # 12:00 has no donor within 30 min


def test_a_secondary_of_another_shape_contributes_nothing():
    primary = [[hour(0)], [hour(0)]]
    merged, filled = fill_gaps(primary, [[hour(0, uv_index=1)]], {"uv_index"})
    assert filled == set() and [h.uv_index for s in merged for h in s] == [None, None]
    merged, filled = fill_gaps(primary, primary, set())
    assert filled == set()


def test_missing_fields_reports_what_is_absent_anywhere():
    full = HourlyWeather(
        time=T0,
        apparent_temperature_c=1,
        uv_index=1,
        wind_gust_kmh=1,
        precipitation_probability=1,
    )
    assert missing_fields([[full]]) == set()
    assert missing_fields([[full, hour(1, uv_index=2)]]) == {
        "apparent_temperature_c",
        "wind_gust_kmh",
        "precipitation_probability",
    }


# -- the service -------------------------------------------------------------------------


class Fake:
    def __init__(self, name, make, provides=(), covers=None, fail=False):
        self.name, self.attribution = name, f"data by {name}"
        self.provides, self.make, self.fail, self.calls = frozenset(provides), make, fail, 0
        if covers is not None:
            self.covers = covers

    async def forecast(self, points, start, end):
        self.calls += 1
        if self.fail:
            raise ProviderUnavailableError("down", provider=self.name)
        return [[self.make(h) for h in range(2)] for _ in points]

    async def health(self):
        return {"status": "ok"}


def dwd_like():
    return Fake(
        "dwd",
        lambda h: hour(h, temperature_c=8, wind_speed_kmh=15, wind_gust_kmh=30),
        provides={"wind_gust_kmh", "precipitation_probability"},
    )


def open_meteo_like():
    return Fake(
        "open-meteo",
        lambda h: hour(
            h, temperature_c=9, uv_index=3, apparent_temperature_c=6, precipitation_probability=40
        ),
        provides={"apparent_temperature_c", "uv_index", "precipitation_probability"},
    )


async def test_gaps_are_filled_from_the_next_provider_and_both_are_credited():
    dwd, om = dwd_like(), open_meteo_like()
    forecast = await WeatherService([dwd, om], merge=True).forecast(POINTS, T0, T0)
    assert forecast.provider == "dwd" and forecast.sources == ("dwd", "open-meteo")
    got = forecast.series[0][0]
    assert got.temperature_c == 8 and got.wind_gust_kmh == 30  # DWD's own values stay
    assert got.uv_index == 3 and got.apparent_temperature_c == 6  # filled
    assert got.precipitation_probability == 40
    assert forecast.attribution == "data by dwd; data by open-meteo"


async def test_merging_is_off_unless_asked_for():
    dwd, om = dwd_like(), open_meteo_like()
    forecast = await WeatherService([dwd, om]).forecast(POINTS, T0, T0)
    assert forecast.sources == ("dwd",) and om.calls == 0
    assert forecast.series[0][0].uv_index is None


async def test_no_extra_request_when_nothing_is_missing_or_nothing_could_fill_it():
    complete = Fake(
        "open-meteo",
        lambda h: hour(
            h, uv_index=1, apparent_temperature_c=1, wind_gust_kmh=1, precipitation_probability=1
        ),
    )
    other = open_meteo_like()
    one = await WeatherService([complete, other], merge=True).forecast(POINTS, T0, T0)
    assert one.sources == ("open-meteo",) and other.calls == 0

    # a fallback that supplies none of the missing fields is not asked
    no_help = Fake("met-no", lambda h: hour(h), provides=())
    two = await WeatherService([dwd_like(), no_help], merge=True).forecast(POINTS, T0, T0)
    assert two.sources == ("dwd",) and no_help.calls == 0


async def test_a_failing_or_non_covering_donor_is_ignored_not_fatal():
    broken = Fake("open-meteo", lambda h: hour(h), provides={"uv_index"}, fail=True)
    forecast = await WeatherService([dwd_like(), broken], merge=True).forecast(POINTS, T0, T0)
    assert forecast.sources == ("dwd",) and forecast.series[0][0].uv_index is None
    away = Fake(
        "open-meteo", lambda h: hour(h, uv_index=3), provides={"uv_index"}, covers=lambda p: False
    )
    forecast = await WeatherService([dwd_like(), away], merge=True).forecast(POINTS, T0, T0)
    assert forecast.sources == ("dwd",) and away.calls == 0


async def test_a_donor_that_adds_nothing_is_not_credited():
    useless = Fake("open-meteo", lambda h: hour(h), provides={"uv_index"})  # has no UV after all
    forecast = await WeatherService([dwd_like(), useless], merge=True).forecast(POINTS, T0, T0)
    assert forecast.sources == ("dwd",) and forecast.attribution == "data by dwd"


async def test_fallback_still_works_when_the_primary_fails():
    primary = Fake("dwd", lambda h: hour(h), provides=set(), fail=True)
    om = open_meteo_like()
    forecast = await WeatherService([primary, om], merge=True).forecast(POINTS, T0, T0)
    assert forecast.provider == "open-meteo" and forecast.sources == ("open-meteo",)
    with pytest.raises(ProviderUnavailableError):
        await WeatherService([primary], merge=True).forecast(POINTS, T0, T0)


async def test_donor_results_are_cached_per_provider():
    dwd, om = dwd_like(), open_meteo_like()
    service = WeatherService([dwd, om], merge=True)
    await service.forecast(POINTS, T0, T0)
    await service.forecast(POINTS, T0, T0)
    assert dwd.calls == 1 and om.calls == 1
