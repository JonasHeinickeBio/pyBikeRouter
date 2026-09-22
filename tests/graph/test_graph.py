from pathlib import Path

from bike_routing_agent.errors import (
    GeocodingNotFoundError,
    ProviderNoRouteError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.graph import build_graph
from bike_routing_agent.models import Coordinate, GeocodeCandidate, RouteCandidate, RouteMetrics


class FakeGeocoder:
    name = "fake"

    def __init__(self, *, by_query: dict[str, list[GeocodeCandidate]] | None = None) -> None:
        self._by_query = by_query or {}
        self.calls: list[str] = []

    async def geocode(self, query: str, *, limit: int = 5) -> list[GeocodeCandidate]:
        self.calls.append(query)
        if query not in self._by_query:
            raise GeocodingNotFoundError(f"no results for {query}", query=query)
        return self._by_query[query]


class FakeRouter:
    name = "fake"

    def __init__(
        self, *, candidate: RouteCandidate | None = None, error: Exception | None = None
    ) -> None:
        self._candidate = candidate
        self._error = error
        self.calls = 0

    async def route(self, request):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._candidate

    async def health(self) -> dict:
        return {"status": "ok"}


def unambiguous(label: str, lon: float, lat: float) -> list[GeocodeCandidate]:
    return [
        GeocodeCandidate(
            label=label, coordinate=Coordinate(lon=lon, lat=lat), confidence=0.9, source="fake"
        )
    ]


def sample_candidate(**overrides) -> RouteCandidate:
    defaults = dict(
        provider="fake",
        provider_profile="cycling-regular",
        geometry_geojson={"type": "LineString", "coordinates": [[10.5, 52.3], [10.6, 52.4]]},
        metrics=RouteMetrics(distance_m=12_000, duration_s=2000, ascent_m=100, descent_m=90),
    )
    defaults.update(overrides)
    return RouteCandidate(**defaults)


def base_input(origin="Braunschweig", destination="Wolfenbuettel", constraints=None):
    return {
        "raw_input": {
            "origin": origin,
            "destination": destination,
            "constraints": constraints or {"bike_type": "gravel", "max_ascent_m": 500},
        }
    }


async def test_known_request_returns_normalized_ready_route(tmp_path: Path):
    geocoder = FakeGeocoder(
        by_query={
            "Braunschweig": unambiguous("Braunschweig", 10.5267, 52.2689),
            "Wolfenbuettel": unambiguous("Wolfenbuettel", 10.5450, 52.2201),
        }
    )
    router = FakeRouter(candidate=sample_candidate())
    graph = build_graph(geocode_provider=geocoder, routing_providers=[router], export_dir=tmp_path)

    result = await graph.ainvoke(base_input())

    assert result["status"] == "ready"
    assert result["selected_candidate"]["provider"] == "fake"
    assert result["explanation"]
    assert (tmp_path / result["artifacts"]["geojson_file"]).exists()
    assert (tmp_path / result["artifacts"]["gpx_file"]).exists()


async def test_missing_destination_awaits_clarification_without_routing(tmp_path: Path):
    geocoder = FakeGeocoder()
    router = FakeRouter(candidate=sample_candidate())
    graph = build_graph(geocode_provider=geocoder, routing_providers=[router], export_dir=tmp_path)

    result = await graph.ainvoke({"raw_input": {"origin": "Braunschweig", "constraints": {}}})

    assert result["status"] == "awaiting_clarification"
    assert router.calls == 0
    assert geocoder.calls == []


async def test_ambiguous_geocoding_result_returns_candidate_choices_without_routing(tmp_path: Path):
    geocoder = FakeGeocoder(
        by_query={
            "Springfield": [
                GeocodeCandidate(
                    label="Springfield, IL",
                    coordinate=Coordinate(lon=-89.65, lat=39.78),
                    confidence=0.52,
                    source="fake",
                ),
                GeocodeCandidate(
                    label="Springfield, MO",
                    coordinate=Coordinate(lon=-93.29, lat=37.21),
                    confidence=0.50,
                    source="fake",
                ),
            ],
            "Chicago": unambiguous("Chicago", -87.6298, 41.8781),
        }
    )
    router = FakeRouter(candidate=sample_candidate())
    graph = build_graph(geocode_provider=geocoder, routing_providers=[router], export_dir=tmp_path)

    result = await graph.ainvoke(base_input(origin="Springfield", destination="Chicago"))

    assert result["status"] == "awaiting_clarification"
    assert len(result["clarification"][0]["candidates"]) == 2
    assert router.calls == 0


async def test_ors_timeout_produces_structured_provider_failure(tmp_path: Path):
    geocoder = FakeGeocoder(
        by_query={
            "Braunschweig": unambiguous("Braunschweig", 10.5267, 52.2689),
            "Wolfenbuettel": unambiguous("Wolfenbuettel", 10.5450, 52.2201),
        }
    )
    router = FakeRouter(error=ProviderTimeoutError("timed out", provider="ors"))
    graph = build_graph(geocode_provider=geocoder, routing_providers=[router], export_dir=tmp_path)

    result = await graph.ainvoke(base_input())

    assert result["status"] == "provider_failure"
    assert result["errors"][0]["code"] == "provider_timeout"


async def test_no_route_ends_in_informative_failure_state(tmp_path: Path):
    geocoder = FakeGeocoder(
        by_query={
            "Braunschweig": unambiguous("Braunschweig", 10.5267, 52.2689),
            "Wolfenbuettel": unambiguous("Wolfenbuettel", 10.5450, 52.2201),
        }
    )
    router = FakeRouter(error=ProviderNoRouteError("no path found", provider="ors"))
    graph = build_graph(geocode_provider=geocoder, routing_providers=[router], export_dir=tmp_path)

    result = await graph.ainvoke(base_input())

    assert result["status"] == "no_route"
    assert result["errors"][0]["code"] == "no_route"


async def test_invalid_constraints_rejected_before_geocoding_or_routing(tmp_path: Path):
    geocoder = FakeGeocoder()
    router = FakeRouter(candidate=sample_candidate())
    graph = build_graph(geocode_provider=geocoder, routing_providers=[router], export_dir=tmp_path)

    result = await graph.ainvoke(
        base_input(constraints={"bike_type": "gravel", "target_distance_km": -5})
    )

    assert result["status"] == "invalid"
    assert router.calls == 0
    assert geocoder.calls == []


async def test_multiple_providers_all_reach_scoring_and_selection(tmp_path: Path):
    geocoder = FakeGeocoder(
        by_query={
            "Braunschweig": unambiguous("Braunschweig", 10.5267, 52.2689),
            "Wolfenbuettel": unambiguous("Wolfenbuettel", 10.5450, 52.2201),
        }
    )
    first = FakeRouter(candidate=sample_candidate(provider="first"))
    second = FakeRouter(candidate=sample_candidate(provider="second"))
    graph = build_graph(
        geocode_provider=geocoder, routing_providers=[first, second], export_dir=tmp_path
    )

    result = await graph.ainvoke(base_input())

    assert result["status"] == "ready"
    assert first.calls == 1 and second.calls == 1
    assert {c["provider"] for c in result["candidates"]} == {"first", "second"}
    assert result["selected_candidate"]["provider"] in {"first", "second"}


async def test_one_provider_failing_still_completes_via_the_other(tmp_path: Path):
    geocoder = FakeGeocoder(
        by_query={
            "Braunschweig": unambiguous("Braunschweig", 10.5267, 52.2689),
            "Wolfenbuettel": unambiguous("Wolfenbuettel", 10.5450, 52.2201),
        }
    )
    ok = FakeRouter(candidate=sample_candidate(provider="ok"))
    down = FakeRouter(error=ProviderUnavailableError("down", provider="down"))
    graph = build_graph(
        geocode_provider=geocoder, routing_providers=[down, ok], export_dir=tmp_path
    )

    result = await graph.ainvoke(base_input())

    assert result["status"] == "ready"
    assert result["selected_candidate"]["provider"] == "ok"
    assert result["errors"][0]["provider"] == "down"
