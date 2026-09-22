"""Unit tests for the route_with_provider node (request shaping, parallel
multi-provider fan-out, and error classification at node level)."""

import asyncio

import pytest

from bike_routing_agent.errors import (
    ProviderNoRouteError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from bike_routing_agent.models import RouteCandidate, RouteMetrics
from bike_routing_agent.nodes.route import build_route_node


class StartBarrier:
    """Releases only once ``size`` routings have started concurrently.

    A sequential poller would deadlock on this (the first router waits for a
    second that never starts), so combined with ``asyncio.wait_for`` it is a
    race-free parallelism check instead of a wall-clock timing assertion.
    """

    def __init__(self, size: int) -> None:
        self._size = size
        self._arrived = 0
        self._event = asyncio.Event()

    async def wait_if_full(self) -> None:
        self._arrived += 1
        if self._arrived == self._size:
            self._event.set()
        await self._event.wait()


class CapturingRouter:
    def __init__(
        self,
        *,
        name: str = "capturing",
        provider: str | None = None,
        error: Exception | None = None,
        delay_s: float = 0.0,
        barrier: StartBarrier | None = None,
    ) -> None:
        self.name = name
        self._provider = provider or name
        self._error = error
        self._delay_s = delay_s
        self._barrier = barrier
        self.last_request = None

    async def route(self, request):
        self.last_request = request
        if self._barrier is not None:
            await self._barrier.wait_if_full()
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._error is not None:
            raise self._error
        return RouteCandidate(
            provider=self._provider,
            provider_profile="cycling-regular",
            geometry_geojson={"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            metrics=RouteMetrics(distance_m=1000, duration_s=200),
        )

    async def health(self) -> dict:
        return {"status": "ok"}


def base_state():
    return {
        "resolved_origin": {"lon": 10.5, "lat": 52.3},
        "resolved_destination": {"lon": 10.6, "lat": 52.4},
        "resolved_via": [{"lon": 10.55, "lat": 52.35}],
        "constraints": {"bike_type": "road", "target_distance_km": 20},
    }


def test_build_route_node_requires_at_least_one_provider():
    with pytest.raises(ValueError, match="at least one routing provider"):
        build_route_node(routing_providers=[])


async def test_state_is_shaped_into_neutral_routing_request():
    router = CapturingRouter()
    node = build_route_node(routing_providers=[router])

    update = await node(base_state())

    request = router.last_request
    assert request.origin.lon == 10.5
    assert request.via[0].lat == 52.35
    assert request.constraints.bike_type.value == "road"
    assert request.constraints.target_distance_km == 20
    assert update["status"] == "in_progress"
    assert update["candidates"][0]["provider"] == "capturing"
    assert update["errors"] == []


async def test_no_route_error_maps_to_no_route_status():
    router = CapturingRouter(error=ProviderNoRouteError("no path", provider="capturing"))
    node = build_route_node(routing_providers=[router])

    update = await node(base_state())

    assert update["status"] == "no_route"
    assert update["errors"][0]["code"] == "no_route"


async def test_generic_provider_error_maps_to_provider_failure():
    router = CapturingRouter(
        error=ProviderUnavailableError("down for maintenance", provider="capturing")
    )
    node = build_route_node(routing_providers=[router])

    update = await node(base_state())

    assert update["status"] == "provider_failure"
    assert update["errors"][0]["code"] == "provider_unavailable"
    assert update["errors"][0]["message"] == "down for maintenance"


async def test_empty_via_list_is_accepted():
    router = CapturingRouter()
    node = build_route_node(routing_providers=[router])

    state = base_state()
    state["resolved_via"] = []
    await node(state)

    assert router.last_request.via == []


async def test_all_providers_are_asked_and_all_candidates_forwarded():
    slow = CapturingRouter(name="slow", provider="slow", delay_s=0.05)
    fast = CapturingRouter(name="fast", provider="fast")
    node = build_route_node(routing_providers=[slow, fast])

    update = await node(base_state())

    assert slow.last_request is not None and fast.last_request is not None
    assert update["status"] == "in_progress"
    assert {c["provider"] for c in update["candidates"]} == {"slow", "fast"}


async def test_providers_are_polled_in_parallel_not_sequentially():
    barrier = StartBarrier(size=2)
    node = build_route_node(
        routing_providers=[
            CapturingRouter(name="a", barrier=barrier),
            CapturingRouter(name="b", barrier=barrier),
        ]
    )

    # A sequential poller deadlocks on the barrier, which wait_for turns
    # into a fast test failure instead of a flaky sleep-based assertion.
    update = await asyncio.wait_for(node(base_state()), timeout=5.0)

    assert update["status"] == "in_progress"


async def test_one_failing_provider_does_not_sink_the_other_candidates():
    ok = CapturingRouter(name="ok", provider="ok")
    node = build_route_node(
        routing_providers=[
            ok,
            CapturingRouter(
                name="limited", error=ProviderRateLimitError("429", provider="limited")
            ),
            CapturingRouter(name="void", error=ProviderNoRouteError("none", provider="void")),
        ]
    )

    update = await node(base_state())

    assert update["status"] == "in_progress"
    assert [c["provider"] for c in update["candidates"]] == ["ok"]
    codes = {e["provider"]: e["code"] for e in update["errors"]}
    assert codes == {"limited": "provider_rate_limited", "void": "no_route"}


async def test_unexpected_exception_becomes_structured_error_for_that_provider():
    class ExplodingRouter:
        name = "exploding"

        async def route(self, request):
            raise RuntimeError("boom")

        async def health(self) -> dict:
            return {"status": "ok"}

    ok = CapturingRouter(name="ok", provider="ok")
    node = build_route_node(routing_providers=[ExplodingRouter(), ok])

    update = await node(base_state())

    assert update["status"] == "in_progress"
    assert [c["provider"] for c in update["candidates"]] == ["ok"]
    assert update["errors"][0]["provider"] == "exploding"
    assert "RuntimeError" in update["errors"][0]["message"]


async def test_mixed_no_route_and_failure_without_candidates_is_provider_failure():
    node = build_route_node(
        routing_providers=[
            CapturingRouter(name="void", error=ProviderNoRouteError("none", provider="void")),
            CapturingRouter(
                name="down", error=ProviderUnavailableError("down", provider="down")
            ),
        ]
    )

    update = await node(base_state())

    assert update["status"] == "provider_failure"
    assert len(update["errors"]) == 2


async def test_every_provider_answering_no_route_is_no_route_status():
    node = build_route_node(
        routing_providers=[
            CapturingRouter(name="void-a", error=ProviderNoRouteError("none", provider="void-a")),
            CapturingRouter(name="void-b", error=ProviderNoRouteError("none", provider="void-b")),
        ]
    )

    update = await node(base_state())

    assert update["status"] == "no_route"
    assert {e["provider"] for e in update["errors"]} == {"void-a", "void-b"}


async def test_unexpected_exception_without_candidates_is_provider_failure():
    class ExplodingRouter:
        name = "exploding"

        async def route(self, request):
            raise RuntimeError("boom")

        async def health(self) -> dict:
            return {"status": "ok"}

    node = build_route_node(routing_providers=[ExplodingRouter()])

    update = await node(base_state())

    # An unexpected exception is an infrastructure failure, not a no-route
    # answer, so the run must not be reported as "no route exists".
    assert update["status"] == "provider_failure"
    assert update["errors"][0]["code"] == "provider_error"


async def test_cancellation_propagates_instead_of_becoming_a_structured_error():
    class CancellingRouter:
        name = "cancelling"

        async def route(self, request):
            raise asyncio.CancelledError()

        async def health(self) -> dict:
            return {"status": "ok"}

    node = build_route_node(routing_providers=[CancellingRouter()])

    # Cancellation is control flow, not provider failure: it must surface.
    with pytest.raises(asyncio.CancelledError):
        await node(base_state())


async def test_all_providers_share_one_engine_neutral_request():
    barrier = StartBarrier(size=2)
    first = CapturingRouter(name="a", barrier=barrier)
    second = CapturingRouter(name="b", barrier=barrier)
    node = build_route_node(routing_providers=[first, second])

    await asyncio.wait_for(node(base_state()), timeout=5.0)

    # The same immutable request object is handed to every engine (it is
    # never mutated per provider).
    assert first.last_request is second.last_request


async def test_candidate_order_follows_provider_order_not_completion_order():
    slow = CapturingRouter(name="slow", provider="slow", delay_s=0.05)
    fast = CapturingRouter(name="fast", provider="fast")
    node = build_route_node(routing_providers=[slow, fast])

    update = await node(base_state())

    # Scoring expects stable, provider-declared order for reproducibility.
    assert [c["provider"] for c in update["candidates"]] == ["slow", "fast"]


async def test_minimal_state_falls_back_to_default_constraints():
    router = CapturingRouter()
    node = build_route_node(routing_providers=[router])

    state = {
        "resolved_origin": {"lon": 10.5, "lat": 52.3},
        "resolved_destination": {"lon": 10.6, "lat": 52.4},
    }
    await node(state)

    request = router.last_request
    assert request.via == []
    assert request.constraints.bike_type.value == "gravel"


async def test_candidates_carry_full_serialized_candidate_payload():
    router = CapturingRouter()
    node = build_route_node(routing_providers=[router])

    update = await node(base_state())

    candidate = update["candidates"][0]
    assert set(candidate) >= {
        "provider",
        "provider_profile",
        "geometry_geojson",
        "metrics",
        "provenance",
        "warnings",
    }
    assert candidate["metrics"]["distance_m"] == 1000


# ---------------------------------------------------------------- loops (issue #5)


def loop_state():
    return {
        "resolved_origin": {"lon": 10.5, "lat": 52.3},
        "resolved_destination": {"lon": 10.5, "lat": 52.3},  # snapped by geocode
        "resolved_via": [],
        "constraints": {
            "return_to_origin": True,
            "target_distance_km": 15,
            "loop_direction": "counterclockwise",
        },
    }


async def test_loop_request_receives_synthesized_waypoints_around_the_origin():
    router = CapturingRouter()
    node = build_route_node(routing_providers=[router])

    update = await node(loop_state())

    request = router.last_request
    assert request.destination == request.origin
    assert len(request.via) == 2
    assert all((v.lon, v.lat) != (10.5, 52.3) for v in request.via)
    assert update["loop_plan"]["direction"] == "counterclockwise"
    assert update["loop_plan"]["reach_m"] > 0


async def test_loop_with_caller_supplied_vias_keeps_the_callers_shape():
    router = CapturingRouter()
    node = build_route_node(routing_providers=[router])

    state = loop_state()
    state["resolved_via"] = [{"lon": 10.7, "lat": 52.35}]
    update = await node(state)

    assert [(v.lon, v.lat) for v in router.last_request.via] == [(10.7, 52.35)]
    assert update["loop_plan"] is None


async def test_ordinary_request_records_no_loop_plan():
    router = CapturingRouter()
    node = build_route_node(routing_providers=[router])

    update = await node(base_state())

    assert update["loop_plan"] is None
    assert len(router.last_request.via) == 1
