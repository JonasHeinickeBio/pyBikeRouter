"""The optimisation workflow end to end: several routes fetched, one chosen by the goals."""

from bike_routing_agent.graph import build_graph
from bike_routing_agent.models import OptimizeSpec, RouteCandidate, RouteMetrics

from .test_graph import FakeGeocoder, FakeRouter, sample_candidate, unambiguous


def route(profile: str, km: float, *, traffic: float, paved: float, source: str | None = None):
    return RouteCandidate(
        provider="brouter",
        provider_profile=profile,
        geometry_geojson={
            "type": "LineString",
            "coordinates": [[10.40, 52.30 + km / 1000], [10.5, 52.25], [10.62, 52.20]],
        },
        metrics=RouteMetrics(
            distance_m=km * 1000,
            duration_s=km * 200,
            ascent_m=100.0,
            traffic_index=traffic,
            engine_surface_shares={"paved": paved, "unpaved": round(1 - paved, 4)},
        ),
        provenance={"optimizer_source": source} if source else {},
    )


class OptimizingRouter(FakeRouter):
    """A BRouter-like engine: one main route, many more when asked to optimise."""

    name = "brouter"

    def __init__(self):
        super().__init__(candidate=route("custom_gravel-v2", 60, traffic=0.20, paved=0.50))
        self.optimize_calls: list[OptimizeSpec] = []
        self.alternatives_calls = 0

    def optimization_deadline_s(self, request):
        return 5.0

    async def optimization_candidates(self, request, spec):
        self.optimize_calls.append(spec)
        return [
            route("shortest", 55, traffic=0.45, paved=0.20, source="profile"),
            route("custom_commuter-v1", 63, traffic=0.06, paved=0.82, source="profile"),
            route("trekking", 66, traffic=0.10, paved=0.97, source="profile"),
            route("custom_touring-v1", 95, traffic=0.01, paved=0.60, source="profile"),  # +73 %
        ]

    async def alternatives(self, request):
        self.alternatives_calls += 1
        return []


def build(router, tmp_path):
    return build_graph(
        geocode_provider=FakeGeocoder(
            by_query={"A": unambiguous("A", 10.40, 52.30), "B": unambiguous("B", 10.62, 52.20)}
        ),
        routing_providers=[router],
        export_dir=tmp_path,
    )


def request(**extra):
    return {
        "raw_input": {
            "origin": "A",
            "destination": "B",
            "constraints": {"bike_type": "gravel"},
            **extra,
        }
    }


GOALS = {"goals": [{"metric": "traffic", "weight": 1.0}, {"metric": "paved", "weight": 0.7}]}


async def test_the_best_fit_for_the_goals_is_selected_and_explained(tmp_path):
    router = OptimizingRouter()
    result = await build(router, tmp_path).ainvoke(request(optimize=GOALS))
    assert result["status"] == "ready"
    assert router.optimize_calls and router.alternatives_calls == 0  # many routes, not the usual
    selected = result["selected_candidate"]
    # trekking: nearly as quiet as the commuter route (0.10 vs 0.06) and 15 points more paved
    assert selected["provider_profile"] == "trekking" and selected["rank"] == 1
    second = next(c for c in result["candidates"] if c["rank"] == 2)
    assert second["provider_profile"] == "custom_commuter-v1"
    # too long a detour (+73 %) although it is the quietest
    table = {r["profile"]: r for r in result["optimization"]["table"]}
    assert table["custom_touring-v1"]["feasible"] is False
    assert [c["rank"] for c in result["candidates"]] == list(range(1, 6))
    assert result["explanation"].startswith("Compared 5 routes (5 distinct) for less traffic")
    assert "Best fit: brouter/trekking" in result["explanation"]


async def test_without_optimize_nothing_changes(tmp_path):
    router = OptimizingRouter()
    result = await build(router, tmp_path).ainvoke(request())
    assert result["status"] == "ready" and not router.optimize_calls
    assert router.alternatives_calls == 1
    assert result.get("optimization") is None
    assert result["selected_candidate"]["provider_profile"] == "custom_gravel-v2"


async def test_an_engine_that_cannot_optimise_still_plans(tmp_path):
    router = FakeRouter(candidate=sample_candidate())  # no optimization_candidates
    result = await build(router, tmp_path).ainvoke(request(optimize=GOALS))
    assert result["status"] == "ready"
    # one candidate, no data for the goals: said, not an error
    report = result["optimization"]
    assert report["considered"] == 1
    assert {d["metric"] for d in report["dropped_goals"]} == {"traffic", "paved"}


async def test_a_broken_optimisation_never_fails_the_plan(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr("bike_routing_agent.nodes.optimize.optimize", boom)
    result = await build(OptimizingRouter(), tmp_path).ainvoke(request(optimize=GOALS))
    assert result["status"] == "ready"
    assert "could not be done" in result["optimization"]["summary"]
