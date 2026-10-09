"""Tests for the BRouter adapter against recorded-style fixtures.

BRouter responds with plain-text errors (no JSON bodies) and string-typed
GeoJSON summary values; both shapes are pinned here together with the
request-parameter contract (lonlats ordering, custom_ profile names).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderNoRouteError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.models import Coordinate, RouteConstraints, RoutingRequest
from bike_routing_agent.providers.brouter import BRouterAdapter

BROUTER_BASE = "http://brouter.test"
BROUTER_URL = f"{BROUTER_BASE}/brouter"
ROBOTS_URL = f"{BROUTER_BASE}/robots.txt"


def make_request(**constraint_kwargs: object) -> RoutingRequest:
    constraint_kwargs.setdefault("bike_type", "gravel")
    return RoutingRequest(
        origin=Coordinate(lon=10.5267132, lat=52.2689081),
        destination=Coordinate(lon=10.5450128, lat=52.2201356),
        constraints=RouteConstraints(**constraint_kwargs),  # type: ignore[arg-type]
    )


@respx.mock
async def test_route_normalizes_fixture_into_candidate(brouter_route_response: dict) -> None:
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json=brouter_route_response))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    candidate = await adapter.route(make_request())

    assert candidate.provider == "brouter"
    assert candidate.provider_profile == "custom_gravel-v2"
    assert candidate.metrics.distance_m == 5761.0
    assert candidate.metrics.duration_s == 1224.0
    assert candidate.metrics.ascent_m == 64.0
    assert candidate.metrics.descent_m is None
    assert candidate.geometry_geojson["type"] == "LineString"
    # What was set per request is recorded: avoid_high_traffic_roads=True (the default)
    # turns the gravel profile's traffic switch on.
    assert candidate.provenance == {
        "provider": "brouter",
        "profile": "custom_gravel-v2",
        "profile_overrides": {"consider_traffic_estimate": 1},
    }
    assert candidate.raw_provider_response == brouter_route_response
    # Traffic is now applied per request, so only the ferry limitation is left to warn about.
    assert len(candidate.warnings) == 1
    assert "avoid_ferries=True is not enforced by custom_gravel-v2" in candidate.warnings[0]


@respx.mock
async def test_route_sends_lonlats_and_profile_params(brouter_route_response: dict) -> None:
    route = respx.get(BROUTER_URL).mock(
        return_value=httpx.Response(200, json=brouter_route_response)
    )
    adapter = BRouterAdapter(base_url=f"{BROUTER_BASE}/")

    await adapter.route(make_request())

    params = route.calls[0].request.url.params
    assert params["lonlats"] == "10.5267132,52.2689081|10.5450128,52.2201356"
    assert params["profile"] == "custom_gravel-v2"
    assert params["alternativeidx"] == "0"
    assert params["format"] == "geojson"


@respx.mock
async def test_route_keeps_via_order_in_lonlats(brouter_route_response: dict) -> None:
    route = respx.get(BROUTER_URL).mock(
        return_value=httpx.Response(200, json=brouter_route_response)
    )
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    request = RoutingRequest(
        origin=Coordinate(lon=1.0, lat=2.0),
        via=[Coordinate(lon=3.0, lat=4.0)],
        destination=Coordinate(lon=5.0, lat=6.0),
        constraints=RouteConstraints(bike_type="city"),
    )
    await adapter.route(request)

    assert (
        route.calls[0].request.url.params["lonlats"]
        == "1.0000000,2.0000000|3.0000000,4.0000000|5.0000000,6.0000000"
    )
    assert route.calls[0].request.url.params["profile"] == "trekking"


@respx.mock
async def test_accepts_bare_feature_without_feature_collection() -> None:
    payload = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[1.0, 2.0], [3.0, 4.0]]},
        "properties": {"track-length": "100", "total-time": "30"},
    }
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json=payload))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    candidate = await adapter.route(make_request())

    assert candidate.metrics.distance_m == 100.0
    assert candidate.metrics.duration_s == 30.0
    assert candidate.metrics.ascent_m is None


@respx.mock
async def test_unreachable_target_maps_400_text_to_no_route_error() -> None:
    respx.get(BROUTER_URL).mock(
        return_value=httpx.Response(
            400, text="20020001: routing failed - start not reachable from network"
        )
    )
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderNoRouteError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.provider == "brouter"
    assert "not reachable" in exc_info.value.detail["body"]


@respx.mock
async def test_unknown_profile_maps_400_text_to_bad_response_error() -> None:
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(400, text="cannot find profile 'nope'"))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.detail["status_code"] == 400
    assert "cannot find profile" in exc_info.value.detail["body"]


@respx.mock
async def test_server_error_maps_to_unavailable_after_retries() -> None:
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(500, text="java.lang.RuntimeException"))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderUnavailableError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.detail["status_code"] == 500
    assert "RuntimeException" in exc_info.value.detail["body"]


@respx.mock
async def test_retries_500_then_succeeds(brouter_route_response: dict) -> None:
    respx.get(BROUTER_URL).mock(
        side_effect=[
            httpx.Response(500, text="too busy"),
            httpx.Response(200, json=brouter_route_response),
        ]
    )
    adapter = BRouterAdapter(base_url=BROUTER_BASE, max_retries=1)

    candidate = await adapter.route(make_request())

    assert candidate.metrics.distance_m == 5761.0
    assert len(respx.calls) == 2


@respx.mock
async def test_timeout_maps_to_provider_timeout() -> None:
    respx.get(BROUTER_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    adapter = BRouterAdapter(base_url=BROUTER_BASE, timeout_s=0.1)

    with pytest.raises(ProviderTimeoutError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.detail["attempts"] == 1


@respx.mock
async def test_connection_error_maps_to_unavailable() -> None:
    respx.get(BROUTER_URL).mock(side_effect=httpx.ConnectError("refused"))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderUnavailableError) as exc_info:
        await adapter.route(make_request())

    assert "refused" in exc_info.value.detail["error"]


@respx.mock
async def test_invalid_json_maps_to_bad_response() -> None:
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, text="<html>not geojson</html>"))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert "invalid JSON" in str(exc_info.value)


@respx.mock
async def test_non_object_payload_maps_to_bad_response() -> None:
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json=["unexpected"]))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderBadResponseError):
        await adapter.route(make_request())


@respx.mock
async def test_no_linestring_feature_maps_to_no_route() -> None:
    respx.get(BROUTER_URL).mock(
        return_value=httpx.Response(
            200,
            json={"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}}]},
        )
    )
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderNoRouteError):
        await adapter.route(make_request())


@respx.mock
async def test_missing_track_length_maps_to_bad_response() -> None:
    respx.get(BROUTER_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[1.0, 2.0]]},
                "properties": {"total-time": "30"},
            },
        )
    )
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert "track-length" in str(exc_info.value)


@respx.mock
async def test_surface_preferences_produce_warning(brouter_route_response: dict) -> None:
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json=brouter_route_response))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    candidate = await adapter.route(make_request(prefer_surfaces=["gravel", "unpaved"]))

    surface_warnings = [w for w in candidate.warnings if "cannot be applied per request" in w]
    assert len(surface_warnings) == 1
    assert "gravel" in surface_warnings[0]


@respx.mock
async def test_touring_avoid_ferries_true_has_no_ferry_warning(
    brouter_route_response: dict,
) -> None:
    """custom_touring-v1 sets allow_ferries=false, so it genuinely enforces it."""
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json=brouter_route_response))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    request = make_request(bike_type="touring")
    candidate = await adapter.route(request)

    assert candidate.provider_profile == "custom_touring-v1"
    assert not any("ferry" in w or "ferries" in w for w in candidate.warnings)
    assert candidate.warnings == []  # traffic is applied per request: nothing left to declare


@respx.mock
async def test_stock_profiles_get_no_constraint_warnings(
    brouter_route_response: dict,
) -> None:
    """Stock profile behaviour is not inferred, so no warnings are emitted."""
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json=brouter_route_response))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    candidate = await adapter.route(make_request(bike_type="road"))

    assert candidate.provider_profile == "fastbike"
    assert candidate.warnings == []


@respx.mock
async def test_allow_ferries_warns_generically_not_enforcement(
    brouter_route_response: dict,
) -> None:
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json=brouter_route_response))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    candidate = await adapter.route(make_request(avoid_ferries=False))

    assert any("avoid_ferries=False is not supported per request" in w for w in candidate.warnings)
    assert not any("not enforced by custom_gravel-v2" in w for w in candidate.warnings)


@respx.mock
async def test_health_ok_on_robots_200() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /"))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    assert await adapter.health() == {"status": "ok"}


@respx.mock
async def test_health_degraded_on_non_200() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(503))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    assert await adapter.health() == {"status": "degraded", "status_code": 503}


@respx.mock
async def test_health_unavailable_on_connection_error() -> None:
    respx.get(ROBOTS_URL).mock(side_effect=httpx.ConnectError("refused"))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    result = await adapter.health()

    assert result["status"] == "unavailable"
    assert "refused" in result["error"]


@pytest.mark.parametrize("profile", ["custom_gravel-v1", "custom_gravel-v2"])
def test_every_gravel_profile_version_keeps_its_ferry_warning_but_traffic_is_applied(profile):
    adapter = BRouterAdapter(base_url="http://x")
    request = RoutingRequest(
        origin=Coordinate(lon=10.0, lat=52.0),
        destination=Coordinate(lon=10.1, lat=52.1),
        constraints=RouteConstraints(bike_type="gravel", avoid_high_traffic_roads=True),
    )
    warnings = adapter._build_warnings(request, profile)
    assert any(f"avoid_ferries=True is not enforced by {profile}" in w for w in warnings)
    assert not any("avoid_high_traffic_roads" in w for w in warnings)


def test_the_gravel_bike_type_uses_the_current_profile_version_and_the_file_exists():
    from pathlib import Path

    from bike_routing_agent.config import BROUTER_PROFILE_MAP

    name = BROUTER_PROFILE_MAP["gravel"]
    assert name == "custom_gravel-v2"
    profiles = Path(__file__).resolve().parents[2] / "docker" / "brouter" / "profiles"
    text = (profiles / f"{name.removeprefix('custom_')}.brf").read_text()
    assert "assign consider_elevation true" in text
    # v1 stays for reproducibility and differs only in that switch
    old = (profiles / "gravel-v1.brf").read_text()
    assert "assign consider_elevation false" in old


def test_the_commuter_profile_file_matches_what_its_header_says():
    from pathlib import Path

    from bike_routing_agent.config import BROUTER_PROFILE_MAP

    assert BROUTER_PROFILE_MAP["commuter"] == "custom_commuter-v1"
    path = (
        Path(__file__).resolve().parents[2] / "docker" / "brouter" / "profiles" / "commuter-v1.brf"
    )
    text = path.read_text()
    for line in ("allow_steps", "allow_ferries"):
        assert any(
            ln.startswith(f"assign   {line}") and "= false" in ln for ln in text.splitlines()
        ), line
    assert any(
        ln.startswith("assign   consider_traffic") and "= true" in ln for ln in text.splitlines()
    )
    assert "pyBikeRouter custom profile: commuter-v1" in text


def _payload_with_tags(rows: list[tuple[float, str]]) -> dict:
    header = [
        "Longitude", "Latitude", "Elevation", "Distance", "CostPerKm", "ElevCost",
        "TurnCost", "NodeCost", "InitialCost", "WayTags", "NodeTags", "Time", "Energy",
    ]  # fmt: skip
    messages = [header] + [
        ["0", "0", "0", str(length), "0", "0", "0", "0", "0", tags, "", "0", "0"]
        for length, tags in rows
    ]
    total = sum(length for length, _ in rows)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[10.0, 52.0], [10.1, 52.1]]},
                "properties": {
                    "track-length": str(total),
                    "total-time": "600",
                    "filtered ascend": "12",
                    "messages": messages,
                },
            }
        ],
    }


@respx.mock
async def test_main_road_share_comes_from_the_way_tags_and_ignores_bike_lanes() -> None:
    payload = _payload_with_tags(
        [
            (400, "highway=secondary surface=asphalt"),  # unprotected main road
            (200, "highway=tertiary cycleway=lane"),  # main road with a lane: protected
            (200, "highway=primary bicycle=designated"),  # designated for bikes: protected
            (200, "highway=residential"),
        ]
    )
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json=payload))
    candidate = await BRouterAdapter(base_url=BROUTER_BASE).route(make_request())
    assert candidate.metrics.main_road_share == 0.4


@respx.mock
async def test_without_way_tags_the_share_is_unknown_not_zero(brouter_route_response: dict) -> None:
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json=brouter_route_response))
    candidate = await BRouterAdapter(base_url=BROUTER_BASE).route(make_request())
    assert candidate.metrics.main_road_share is None


@respx.mock
async def test_alternatives_price_the_trip_with_the_other_profiles_and_are_marked() -> None:
    route = respx.get(BROUTER_URL).mock(
        return_value=httpx.Response(200, json=_payload_with_tags([(1000, "highway=cycleway")]))
    )
    adapter = BRouterAdapter(base_url=BROUTER_BASE, alternatives=True)
    found = await adapter.alternatives(make_request())  # gravel: touring + mtb
    assert [c.provider_profile for c in found] == ["custom_touring-v1", "mtb"]
    assert all(c.provenance["alternative_of"] == "custom_gravel-v2" for c in found)
    assert {call.request.url.params["profile"] for call in route.calls} == {
        "custom_touring-v1",
        "mtb",
    }


@respx.mock
async def test_alternatives_are_off_by_default_and_failures_are_dropped() -> None:
    route = respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json={}))
    assert await BRouterAdapter(base_url=BROUTER_BASE).alternatives(make_request()) == []
    assert not route.called
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(500))
    adapter = BRouterAdapter(base_url=BROUTER_BASE, alternatives=True, max_retries=0)
    assert await adapter.alternatives(make_request()) == []  # all failed: nothing, no raise


@respx.mock
async def test_surface_shares_come_from_the_surface_tags_and_unknown_stays_unknown() -> None:
    payload = _payload_with_tags(
        [
            (500, "highway=cycleway surface=asphalt"),
            (200, "highway=track surface=compacted"),
            (100, "highway=path surface=dirt"),  # natural soft
            (100, "highway=cycleway surface=paving_stones"),  # cobbles
            (100, "highway=track"),  # no surface tag: unknown, not paved, not unpaved
        ]
    )
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json=payload))
    candidate = await BRouterAdapter(base_url=BROUTER_BASE).route(make_request())
    assert candidate.metrics.engine_surface_shares == {
        "paved": 0.5,
        "unpaved": 0.3,
        "cobbles": 0.1,
        "unknown": 0.1,
    }
    # the Overpass-style enrichment fields are untouched: scoring never sees these
    assert candidate.metrics.surface_coverage == {}
    assert candidate.metrics.unknown_surface_fraction is None


@respx.mock
async def test_without_tags_there_are_no_surface_shares(brouter_route_response: dict) -> None:
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json=brouter_route_response))
    candidate = await BRouterAdapter(base_url=BROUTER_BASE).route(make_request())
    assert candidate.metrics.engine_surface_shares == {}


@respx.mock
async def test_a_slow_alternative_is_dropped_at_the_deadline_and_the_fast_one_is_kept() -> None:
    import asyncio
    import time

    ok = httpx.Response(200, json=_payload_with_tags([(1000, "highway=cycleway")]))

    async def by_profile(request: httpx.Request) -> httpx.Response:
        if request.url.params["profile"] == "mtb":
            await asyncio.sleep(30)  # an alternative profile that hangs
        return ok

    respx.get(BROUTER_URL).mock(side_effect=by_profile)
    adapter = BRouterAdapter(base_url=BROUTER_BASE, alternatives=True, alternatives_timeout_s=0.3)
    started = time.monotonic()
    found = await adapter.alternatives(make_request())  # gravel: touring + mtb
    assert time.monotonic() - started < 5  # not 30 s
    assert [c.provider_profile for c in found] == ["custom_touring-v1"]


def test_way_tags_missing_on_some_rows_do_not_pass_for_quiet_roads() -> None:
    from bike_routing_agent.providers.brouter_tags import main_road_share, surface_shares

    header = ["lon", "lat", "ele", "dist", "c", "e", "t", "n", "i", "WayTags"]

    def rows(*items):
        return [header] + [
            ["0", "0", "0", str(length), "0", "0", "0", "0", "0", tags] for length, tags in items
        ]

    # no tags anywhere, or empty/null tag cells: unknown, not a 0 % share
    assert main_road_share(rows((500, ""), (500, None))) is None
    assert main_road_share(rows((100, "highway=primary"), (900, ""))) is None  # 10 % tagged
    assert surface_shares(rows((500, ""), (500, None))) == {}
    # enough coverage: the share is of the tagged length, untagged rows are not "quiet"
    covered = rows((450, "highway=primary"), (450, "highway=residential"), (100, ""))
    assert main_road_share(covered) == 0.5


@pytest.mark.parametrize(
    ("bike_type", "profile", "variable"),
    [
        ("gravel", "custom_gravel-v2", "consider_traffic_estimate"),
        ("touring", "custom_touring-v1", "consider_traffic"),
        ("commuter", "custom_commuter-v1", "consider_traffic"),
        ("city", "trekking", "consider_traffic"),
        ("road", "fastbike", "consider_traffic"),
        ("ebike", "fastbike", "consider_traffic"),
    ],
)
@pytest.mark.parametrize("avoid", [True, False])
@respx.mock
async def test_avoid_high_traffic_roads_sets_the_profiles_traffic_switch_per_request(
    bike_type: str, profile: str, variable: str, avoid: bool, brouter_route_response: dict
) -> None:
    route = respx.get(BROUTER_URL).mock(
        return_value=httpx.Response(200, json=brouter_route_response)
    )
    request = make_request(bike_type=bike_type, avoid_high_traffic_roads=avoid)
    candidate = await BRouterAdapter(base_url=BROUTER_BASE).route(request)

    params = route.calls[0].request.url.params
    expected = 1 if avoid else 0
    assert params[f"profile:{variable}"] == str(expected)  # numbers only: BRouter rejects "true"
    assert candidate.provider_profile == profile
    assert candidate.provenance["profile_overrides"] == {variable: expected}
    assert not any("avoid_high_traffic_roads" in w for w in candidate.warnings)


@pytest.mark.parametrize(
    ("bike_type", "profile"), [("mountain", "mtb"), ("recumbent", "vm-forum-liegerad-schnell")]
)
@respx.mock
async def test_profiles_without_a_traffic_setting_say_so_and_send_nothing(
    bike_type: str, profile: str, brouter_route_response: dict
) -> None:
    route = respx.get(BROUTER_URL).mock(
        return_value=httpx.Response(200, json=brouter_route_response)
    )
    candidate = await BRouterAdapter(base_url=BROUTER_BASE).route(make_request(bike_type=bike_type))
    assert not any(k.startswith("profile:") for k in route.calls[0].request.url.params)
    assert "profile_overrides" not in candidate.provenance
    assert any(
        f"avoid_high_traffic_roads=True cannot be applied by {profile}" in w
        for w in candidate.warnings
    )
    # asking for traffic not to be avoided leaves nothing to declare
    off = await BRouterAdapter(base_url=BROUTER_BASE).route(
        make_request(bike_type=bike_type, avoid_high_traffic_roads=False)
    )
    assert not any("avoid_high_traffic_roads" in w for w in off.warnings)


@respx.mock
async def test_alternatives_get_their_own_profiles_traffic_switch() -> None:
    ok = httpx.Response(200, json=_payload_with_tags([(1000, "highway=cycleway")]))
    route = respx.get(BROUTER_URL).mock(return_value=ok)
    adapter = BRouterAdapter(base_url=BROUTER_BASE, alternatives=True)
    found = await adapter.alternatives(make_request())  # gravel: touring (switch) + mtb (none)
    by_profile = {c.provider_profile: c for c in found}
    assert by_profile["custom_touring-v1"].provenance["profile_overrides"] == {
        "consider_traffic": 1
    }
    assert "profile_overrides" not in by_profile["mtb"].provenance
    sent = {
        call.request.url.params["profile"]: dict(call.request.url.params) for call in route.calls
    }
    assert sent["custom_touring-v1"]["profile:consider_traffic"] == "1"
    assert not any(k.startswith("profile:") for k in sent["mtb"])
