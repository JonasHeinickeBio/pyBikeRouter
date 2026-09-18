"""Tests for the Valhalla and BRouter provider stubs.

The stubs are deliberate in this milestone: route() must fail as a structured
provider-unavailable error (never a crash, never a silent empty candidate),
and health probes must report honestly.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from bike_routing_agent.errors import ProviderUnavailableError
from bike_routing_agent.models import Coordinate, RouteConstraints, RoutingRequest
from bike_routing_agent.providers.brouter import BRouterAdapter
from bike_routing_agent.providers.valhalla import ValhallaAdapter

VALHALLA_BASE = "http://valhalla.test"
VALHALLA_STATUS_URL = f"{VALHALLA_BASE}/status"


def make_request() -> RoutingRequest:
    return RoutingRequest(
        origin=Coordinate(lon=10.5267, lat=52.2689),
        destination=Coordinate(lon=10.5450, lat=52.2201),
        constraints=RouteConstraints(bike_type="gravel"),
    )


async def test_valhalla_route_raises_structured_unavailable() -> None:
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)
    assert adapter.name == "valhalla"

    with pytest.raises(ProviderUnavailableError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.code == "provider_unavailable"
    assert exc_info.value.provider == "valhalla"
    assert "not implemented" in str(exc_info.value)
    assert exc_info.value.to_dict()["code"] == "provider_unavailable"
    assert exc_info.value.to_dict()["provider"] == "valhalla"


@respx.mock
async def test_valhalla_health_ok_when_status_returns_200() -> None:
    respx.get(VALHALLA_STATUS_URL).mock(return_value=httpx.Response(200))
    adapter = ValhallaAdapter(base_url=f"{VALHALLA_BASE}/", timeout_s=1.0)

    assert await adapter.health() == {"status": "ok"}


@respx.mock
async def test_valhalla_health_degraded_on_non_200() -> None:
    respx.get(VALHALLA_STATUS_URL).mock(return_value=httpx.Response(503))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE, timeout_s=1.0)

    assert await adapter.health() == {"status": "degraded"}


@respx.mock
async def test_valhalla_health_unavailable_on_connection_error() -> None:
    respx.get(VALHALLA_STATUS_URL).mock(side_effect=httpx.ConnectError("refused"))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE, timeout_s=1.0)

    result = await adapter.health()

    assert result["status"] == "unavailable"
    assert "refused" in result["error"]


async def test_brouter_route_raises_structured_unavailable() -> None:
    adapter = BRouterAdapter(base_url="http://brouter.test", timeout_s=1.0)
    assert adapter.name == "brouter"

    with pytest.raises(ProviderUnavailableError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.code == "provider_unavailable"
    assert exc_info.value.provider == "brouter"
    assert "not implemented" in str(exc_info.value)


async def test_brouter_health_reports_not_implemented() -> None:
    adapter = BRouterAdapter(base_url="http://brouter.test")

    assert await adapter.health() == {"status": "not_implemented"}
