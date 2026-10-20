"""The time zone lookup behind the opening-hours check: asks once per grid cell, never raises."""

import httpx
import pytest

from bike_routing_agent.poi.zone import OpenMeteoZoneLookup, valid_zone


def lookup(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenMeteoZoneLookup(client=client)


def answering(zone="Europe/Berlin", status=200):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(status, json={"timezone": zone, "hourly": {"time": []}})

    return handler, seen


@pytest.mark.parametrize(
    "value",
    ["Europe/Berlin", "UTC", "America/Argentina/Buenos_Aires", "Etc/GMT+5", "Asia/Ho_Chi_Minh"],
)
def test_iana_names_are_accepted(value):
    assert valid_zone(value) == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        5,
        "",
        "../../etc/passwd",
        "Europe/Berlin\n",
        "a/b/c/d",
        "x" * 80,
        "<script>",
        "Europe Berlin",
    ],
)
def test_anything_else_is_not_a_zone(value):
    assert valid_zone(value) is None


async def test_the_zone_is_asked_for_with_auto_and_one_hour():
    handler, seen = answering()
    assert await lookup(handler).zone_at(9.73, 52.37) == "Europe/Berlin"
    assert seen == [
        {
            "latitude": "52.3700",
            "longitude": "9.7300",
            "timezone": "auto",
            "hourly": "temperature_2m",
            "forecast_hours": "1",
        }
    ]


async def test_nearby_places_share_one_request():
    handler, seen = answering()
    zones = lookup(handler)
    first = await zones.zone_at(9.73, 52.37)
    second = await zones.zone_at(9.74, 52.38)  # same 0.1 degree cell
    assert first == second == "Europe/Berlin" and len(seen) == 1


async def test_a_failure_is_none_and_is_not_remembered():
    state = {"fail": True}

    def handler(request):
        if state["fail"]:
            return httpx.Response(503)
        return httpx.Response(200, json={"timezone": "Europe/Paris"})

    zones = lookup(handler)
    assert await zones.zone_at(2.35, 48.85) is None
    state["fail"] = False
    assert await zones.zone_at(2.35, 48.85) == "Europe/Paris"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["a list"]),
        httpx.Response(200, json={"timezone": "../evil"}),
        httpx.Response(200, json={}),
        httpx.Response(429),
    ],
)
async def test_unusable_answers_are_none(response):
    assert await lookup(lambda request: response).zone_at(0.0, 0.0) is None


async def test_a_network_error_is_none():
    def handler(request):
        raise httpx.ConnectError("down")

    assert await lookup(handler).zone_at(0.0, 0.0) is None
