"""The optimiser's choice: deterministic, explained, robust to one absurd candidate."""

from __future__ import annotations

import pytest

from bike_routing_agent.models import (
    OptimizeGoal,
    OptimizeSpec,
    RouteCandidate,
    RouteConstraints,
    RouteMetrics,
)
from bike_routing_agent.optimizer.engine import optimize


def cand(
    name: str,
    km: float,
    *,
    traffic: float | None = None,
    paved: float | None = None,
    hours: float = 2.0,
    ascent: float = 100.0,
    source: str | None = None,
    duplicate_of: str | None = None,
) -> RouteCandidate:
    return RouteCandidate(
        provider="brouter",
        provider_profile=name,
        geometry_geojson={"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        metrics=RouteMetrics(
            distance_m=km * 1000,
            duration_s=hours * 3600,
            ascent_m=ascent,
            traffic_index=traffic,
            engine_surface_shares={"paved": paved, "unpaved": 1 - paved}
            if paved is not None
            else {},
        ),
        provenance={"optimizer_source": source} if source else {},
        duplicate_of=duplicate_of,
    )


def spec(*goals: tuple[str, float], detour: float = 30.0) -> OptimizeSpec:
    return OptimizeSpec(
        goals=[OptimizeGoal(metric=m, weight=w) for m, w in goals], max_detour_pct=detour
    )


def winner(result) -> str:
    return result.ordered[0].provider_profile


def test_less_traffic_but_as_much_asphalt_as_possible_picks_the_balanced_route():
    routes = [
        cand("shortest", 50, traffic=0.40, paved=0.30),
        cand("quiet_dirt", 56, traffic=0.04, paved=0.35),
        cand("balanced", 54, traffic=0.08, paved=0.80),
        cand("fast_main_road", 52, traffic=0.60, paved=0.98),
    ]
    got = optimize(routes, spec(("traffic", 1.0), ("paved", 0.7)))
    assert winner(got) == "balanced"
    assert [c.rank for c in got.ordered] == [1, 2, 3, 4]
    assert got.report["selected"] == "brouter/balanced"
    assert "less traffic and more asphalt" in got.report["summary"]
    assert got.ordered[0].rank_rationale.endswith("the best fit")


def test_the_weights_decide_between_traffic_and_asphalt():
    routes = [
        cand("quiet", 55, traffic=0.04, paved=0.40),
        cand("smooth", 55, traffic=0.30, paved=0.95),
    ]
    assert winner(optimize(routes, spec(("traffic", 1.0), ("paved", 0.2)))) == "quiet"
    assert winner(optimize(routes, spec(("traffic", 0.2), ("paved", 1.0)))) == "smooth"


def test_one_absurd_candidate_does_not_flatten_the_differences_between_the_others():
    # A profile with an 11.7 h estimate for a 58 km route must not make 2.7 h and 3.2 h look
    # alike: goodness is measured from the best, not from the worst.
    routes = [
        cand("fast", 65, hours=2.7),
        cand("slower", 61, hours=3.2),
        cand("absurd", 58, hours=11.7),
    ]
    got = optimize(routes, spec(("duration", 1.0)))
    assert winner(got) == "fast"
    goodness = {r["label"]: r["goodness"]["duration"] for r in got.report["table"]}
    assert goodness["brouter/fast"] == 1.0 and goodness["brouter/slower"] < 0.35
    assert goodness["brouter/absurd"] == 0.0


def test_a_route_beyond_the_detour_cap_cannot_win_while_others_are_within_it():
    routes = [
        cand("direct", 50, traffic=0.30, paved=0.5),
        cand("scenic_quiet", 80, traffic=0.01, paved=0.5),  # +60 %
    ]
    assert winner(optimize(routes, spec(("traffic", 1.0), detour=30))) == "direct"
    assert winner(optimize(routes, spec(("traffic", 1.0), detour=100))) == "scenic_quiet"
    result = optimize(routes, spec(("traffic", 1.0), detour=30))
    assert [r["feasible"] for r in result.report["table"]] == [True, False]


def test_the_requests_own_hard_limits_are_applied_unless_nothing_meets_them():
    routes = [cand("a", 50, traffic=0.3, ascent=500), cand("b", 52, traffic=0.35, ascent=150)]
    limits = RouteConstraints(max_ascent_m=200)
    assert winner(optimize(routes, spec(("traffic", 1.0)), limits)) == "b"
    impossible = RouteConstraints(max_ascent_m=10)
    got = optimize(routes, spec(("traffic", 1.0)), impossible)
    assert winner(got) == "a" and any("no route met them" in x for x in got.report["hard_limits"])


def test_a_candidate_without_data_never_gets_the_benefit_of_the_doubt():
    routes = [cand("tagged", 50, traffic=0.30), cand("untagged", 50, traffic=None)]
    got = optimize(routes, spec(("traffic", 1.0)))
    assert winner(got) == "tagged"
    row = {r["label"]: r for r in got.report["table"]}["brouter/untagged"]
    assert row["values"]["traffic"] is None and row["goodness"]["traffic"] == 0.0
    assert row["shown"]["traffic"] == "no data"


def test_a_goal_nobody_has_data_for_is_dropped_and_said():
    routes = [cand("a", 50, paved=0.5), cand("b", 52, paved=0.9)]  # no traffic index anywhere
    got = optimize(routes, spec(("traffic", 1.0), ("paved", 1.0)))
    assert winner(got) == "b"
    assert got.report["dropped_goals"][0]["metric"] == "traffic"
    assert [g["metric"] for g in got.report["goals"]] == ["paved"]
    assert "no data for traffic" in got.report["summary"]


def test_without_any_usable_goal_the_usual_order_stands():
    routes = [cand("first", 50), cand("second", 51)]
    got = optimize(routes, spec(("traffic", 1.0)))
    assert [c.provider_profile for c in got.ordered] == ["first", "second"]
    assert got.report["goals"] == []


def test_ties_go_to_the_shorter_route_and_near_copies_are_never_selected():
    routes = [
        cand("long", 60, traffic=0.1),
        cand("short", 55, traffic=0.1),
        cand("copy", 54, traffic=0.0, duplicate_of="brouter/short"),
    ]
    got = optimize(routes, spec(("traffic", 1.0)))
    assert [c.provider_profile for c in got.ordered] == ["short", "long", "copy"]
    assert got.ordered[2].rank == 3 and got.ordered[2].rank_rationale == (
        "near-identical to a better route"
    )


def test_pareto_optimal_routes_are_marked_and_dominated_ones_are_not():
    routes = [
        cand("quiet", 55, traffic=0.05, paved=0.5),
        cand("smooth", 55, traffic=0.3, paved=0.95),
        cand("worse_than_quiet", 55, traffic=0.10, paved=0.4),
    ]
    table = {
        r["label"]: r["pareto"]
        for r in optimize(routes, spec(("traffic", 1), ("paved", 1))).report["table"]
    }
    assert table == {
        "brouter/quiet": True,
        "brouter/smooth": True,
        "brouter/worse_than_quiet": False,
    }


def test_the_report_names_what_the_winner_costs_and_what_it_gives_up():
    routes = [
        cand("shortest", 50, traffic=0.40, paved=0.30),
        cand("balanced", 54, traffic=0.08, paved=0.80, source="2nd route"),
        cand("quiet_dirt", 58, traffic=0.02, paved=0.35),
    ]
    report = optimize(routes, spec(("traffic", 1.0), ("paved", 0.7))).report
    assert report["selected"] == "brouter/balanced (2nd route)"
    assert report["shortest_km"] == 50.0
    assert "4.0 km (8 %) longer than the shortest route found" in report["summary"]
    assert any("quiet_dirt" in t and "traffic index 0.02" in t for t in report["tradeoffs"])
    assert report["table"][0]["selected"] and report["table"][0]["detour_pct"] == 8.0


def test_the_spec_is_validated():
    with pytest.raises(ValueError):
        OptimizeSpec(goals=[])
    with pytest.raises(ValueError):
        spec(("traffic", 1.0), ("traffic", 0.5))  # a metric once
    with pytest.raises(ValueError):
        spec(("paved", 1.0), ("unpaved", 1.0))  # opposite goals
    with pytest.raises(ValueError):
        OptimizeGoal(metric="traffic", weight=0)


# --------------------------------------------------------------------------- the pois goal


def with_pois(c: RouteCandidate, n: int | None) -> RouteCandidate:
    return c.model_copy(update={"metrics": c.metrics.model_copy(update={"poi_count": n})})


def test_more_well_known_places_along_the_route_wins_within_the_detour_cap():
    routes = [
        with_pois(cand("direct", 50, traffic=0.1), 2),
        with_pois(cand("via_castle", 54, traffic=0.1), 7),
        with_pois(cand("grand_tour", 80, traffic=0.1), 12),  # +60 %: over the cap
    ]
    got = optimize(routes, spec(("pois", 1.0)))
    assert winner(got) == "via_castle"
    row = got.report["table"][0]
    assert row["values"] == {"pois": 7.0} and row["shown"]["pois"] == "7 well-known places nearby"
    assert "more sights along the route" in got.report["summary"]
    assert [r["feasible"] for r in got.report["table"]] == [True, True, False]


def test_sights_and_quiet_roads_are_traded_against_each_other_by_weight():
    routes = [
        with_pois(cand("quiet", 50, traffic=0.05), 1),
        with_pois(cand("sights", 52, traffic=0.30), 9),
    ]
    assert winner(optimize(routes, spec(("traffic", 1.0), ("pois", 0.3)))) == "quiet"
    assert winner(optimize(routes, spec(("traffic", 0.3), ("pois", 1.0)))) == "sights"


def test_without_a_poi_count_the_goal_is_dropped_with_its_own_reason():
    routes = [cand("a", 50, traffic=0.1), cand("b", 52, traffic=0.2)]
    got = optimize(routes, spec(("traffic", 1.0), ("pois", 1.0)))
    assert winner(got) == "a"
    [dropped] = got.report["dropped_goals"]
    assert dropped["metric"] == "pois" and "POI lookup was not available" in dropped["reason"]
