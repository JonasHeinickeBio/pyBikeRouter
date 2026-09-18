"""Unit tests for the geocode_locations node: coordinate passthrough,
ambiguity policy, via handling, and error classification."""

import pytest

from bike_routing_agent.errors import GeocodingNotFoundError, ProviderRateLimitError
from bike_routing_agent.models import Coordinate, GeocodeCandidate
from bike_routing_agent.nodes.geocode import NO_MATCH_HINT, build_geocode_node


class RecordingGeocoder:
    name = "recording"

    def __init__(self, results: dict[str, list[GeocodeCandidate]] | None = None) -> None:
        self._results = results or {}
        self.calls: list[str] = []

    async def geocode(self, query: str, *, limit: int = 5) -> list[GeocodeCandidate]:
        self.calls.append(query)
        if query not in self._results:
            raise GeocodingNotFoundError(f"no results for {query}", query=query)
        return self._results[query]


class ExplodingGeocoder:
    name = "exploding"

    async def geocode(self, query: str, *, limit: int = 5) -> list[GeocodeCandidate]:
        raise ProviderRateLimitError("slow down", provider="fake")


def cand(label: str, lon: float, lat: float, confidence: float) -> GeocodeCandidate:
    return GeocodeCandidate(
        label=label,
        coordinate=Coordinate(lon=lon, lat=lat),
        confidence=confidence,
        source="fake",
    )


async def test_coordinate_inputs_bypass_the_geocoder_entirely():
    geocoder = RecordingGeocoder()
    node = build_geocode_node(geocode_provider=geocoder)

    update = await node(
        {
            "origin_input": {"coordinate": {"lon": 10.5, "lat": 52.3}},
            "destination_input": {"coordinate": {"lon": 10.6, "lat": 52.4}},
            "via_inputs": [],
        }
    )

    assert update["status"] == "in_progress"
    assert update["resolved_origin"] == {"lon": 10.5, "lat": 52.3}
    assert update["resolved_destination"] == {"lon": 10.6, "lat": 52.4}
    assert geocoder.calls == []


async def test_clear_top_candidate_is_resolved_without_clarification():
    geocoder = RecordingGeocoder(
        {
            "A": [cand("A wins", 1.0, 2.0, 0.9)],
            "B": [cand("B1", 3.0, 4.0, 0.9), cand("B2", 5.0, 6.0, 0.5)],
        }
    )
    node = build_geocode_node(geocode_provider=geocoder)

    update = await node(
        {"origin_input": {"query": "A"}, "destination_input": {"query": "B"}, "via_inputs": []}
    )

    assert update["status"] == "in_progress"
    assert update["resolved_origin"] == {"lon": 1.0, "lat": 2.0}
    assert update["resolved_destination"] == {"lon": 3.0, "lat": 4.0}


async def test_small_confidence_gap_below_margin_is_ambiguous():
    geocoder = RecordingGeocoder({"A": [cand("A1", 1.0, 2.0, 0.90), cand("A2", 3.0, 4.0, 0.87)]})
    node = build_geocode_node(geocode_provider=geocoder, ambiguity_margin=0.05)

    update = await node(
        {"origin_input": {"query": "A"}, "destination_input": None, "via_inputs": []}
    )

    assert update["status"] == "awaiting_clarification"
    assert update["clarification"][0]["field"] == "A"
    assert len(update["clarification"][0]["candidates"]) == 2


async def test_margin_gap_exactly_at_boundary_is_not_ambiguous():
    geocoder = RecordingGeocoder({"A": [cand("A1", 1.0, 2.0, 0.90), cand("A2", 3.0, 4.0, 0.85)]})
    node = build_geocode_node(geocode_provider=geocoder, ambiguity_margin=0.05)

    update = await node(
        {"origin_input": {"query": "A"}, "destination_input": None, "via_inputs": []}
    )

    assert update["status"] == "in_progress"


async def test_single_weak_candidate_below_min_confidence_is_ambiguous():
    geocoder = RecordingGeocoder({"A": [cand("A1", 1.0, 2.0, 0.2)]})
    node = build_geocode_node(geocode_provider=geocoder, min_confidence=0.3)

    update = await node(
        {"origin_input": {"query": "A"}, "destination_input": None, "via_inputs": []}
    )

    assert update["status"] == "awaiting_clarification"


async def test_unmatched_query_yields_clarification_and_not_found_error():
    geocoder = RecordingGeocoder()
    node = build_geocode_node(geocode_provider=geocoder)

    update = await node(
        {"origin_input": {"query": "nowhere"}, "destination_input": None, "via_inputs": []}
    )

    assert update["status"] == "awaiting_clarification"
    entry = update["clarification"][0]
    assert entry["field"] == "nowhere"
    assert entry["candidates"] == []
    assert entry["hint"] == NO_MATCH_HINT
    assert update["errors"][0]["code"] == "geocoding_not_found"


async def test_provider_error_becomes_structured_provider_failure():
    node = build_geocode_node(geocode_provider=ExplodingGeocoder())

    update = await node(
        {"origin_input": {"query": "A"}, "destination_input": None, "via_inputs": []}
    )

    assert update["status"] == "provider_failure"
    assert update["errors"][0]["code"] == "provider_rate_limited"
    assert update["errors"][0]["provider"] == "fake"


async def test_via_points_are_resolved_alongside_origin_and_destination():
    geocoder = RecordingGeocoder(
        {
            "A": [cand("A", 1.0, 2.0, 0.9)],
            "B": [cand("B", 3.0, 4.0, 0.9)],
            "V": [cand("V", 5.0, 6.0, 0.9)],
        }
    )
    node = build_geocode_node(geocode_provider=geocoder)

    update = await node(
        {
            "origin_input": {"query": "A"},
            "destination_input": {"query": "B"},
            "via_inputs": [{"query": "V"}],
        }
    )

    assert update["status"] == "in_progress"
    assert update["resolved_via"] == [{"lon": 5.0, "lat": 6.0}]
    assert geocoder.calls == ["A", "B", "V"]


async def test_ambiguous_via_point_blocks_routing_with_clarification():
    geocoder = RecordingGeocoder(
        {
            "A": [cand("A", 1.0, 2.0, 0.9)],
            "B": [cand("B", 3.0, 4.0, 0.9)],
            "V": [cand("V1", 5.0, 6.0, 0.5), cand("V2", 7.0, 8.0, 0.49)],
        }
    )
    node = build_geocode_node(geocode_provider=geocoder, ambiguity_margin=0.05)

    update = await node(
        {
            "origin_input": {"query": "A"},
            "destination_input": {"query": "B"},
            "via_inputs": [{"query": "V"}],
        }
    )

    assert update["status"] == "awaiting_clarification"
    assert update["clarification"][0]["field"] == "V"


@pytest.mark.parametrize("gap", [0.04, 0.0])
async def test_parametrized_ambiguity_gap_is_always_ambiguous(gap: float):
    geocoder = RecordingGeocoder(
        {"A": [cand("A1", 1.0, 2.0, 0.9), cand("A2", 3.0, 4.0, 0.9 - gap)]}
    )
    node = build_geocode_node(geocode_provider=geocoder, ambiguity_margin=0.05)

    update = await node(
        {"origin_input": {"query": "A"}, "destination_input": None, "via_inputs": []}
    )

    assert update["status"] == "awaiting_clarification"
