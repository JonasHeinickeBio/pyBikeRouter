"""Short factual pros and cons of each distinct route (scoring/pros_cons.py)."""

from datetime import UTC, datetime

from bike_routing_agent.models import RouteCandidate, RouteConstraints, RouteMetrics
from bike_routing_agent.nodes.compare import build_compare_node
from bike_routing_agent.scoring.pros_cons import annotate_pros_cons
from bike_routing_agent.weather.models import (
    RouteWeather,
    WeatherSummary,
    WetStretch,
)

LINE = {"type": "LineString", "coordinates": [[10.0, 52.0], [10.1, 52.1]]}
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def route(name, km, minutes, ascent, main=None, weather=None, duplicate_of=None) -> RouteCandidate:
    return RouteCandidate(
        provider="brouter",
        provider_profile=name,
        geometry_geojson=LINE,
        metrics=RouteMetrics(
            distance_m=km * 1000, duration_s=minutes * 60, ascent_m=ascent, main_road_share=main
        ),
        weather=weather,
        duplicate_of=duplicate_of,
    )


def weather(*, wet=False, headwind=None) -> RouteWeather:
    stretch = (
        WetStretch(from_fraction=0, to_fraction=1, from_time=NOW, to_time=NOW, whole_route=True)
        if wet
        else None
    )
    return RouteWeather(
        provider="p",
        attribution="a",
        departure=NOW,
        arrival=NOW,
        duration_source="provider",
        retrieved_at=NOW,
        samples=[],
        summary=WeatherSummary(wet_stretch=stretch, headwind_mean_kmh=headwind),
    )


CONSTRAINTS = RouteConstraints()


def run(candidates, constraints=CONSTRAINTS):
    return annotate_pros_cons(candidates, constraints)


def test_a_single_route_has_nothing_to_compare():
    [only] = run([route("a", 10, 30, 50)])
    assert only.pros == [] and only.cons == []


def test_the_leader_on_each_dimension_gets_a_pro_and_the_others_a_con():
    quick = route("quick", 10.0, 30, 120)
    calm = route("calm", 12.0, 40, 40)
    a, b = run([quick, calm])
    assert "Shortest (10.0 km)" in a.pros and "Fastest (30 min)" in a.pros
    assert "2.0 km longer than the shortest" in b.cons
    assert "10 min slower than the fastest" in b.cons
    assert "Least climbing (40 m)" in b.pros
    assert "80 m more climbing than the flattest" in a.cons


def test_small_differences_are_not_worth_a_line():
    a, b = run([route("a", 10.0, 30, 100), route("b", 10.2, 31, 105)])
    assert a.pros == b.pros == [] and a.cons == b.cons == []


def test_a_tie_is_not_a_win():
    a, b, c = run([route("a", 10, 30, 100), route("b", 10, 30, 100), route("c", 13, 40, 100)])
    assert "Shortest" not in " ".join(a.pros + b.pros)  # a and b tie, so neither leads clearly
    assert any("longer than the shortest" in x for x in c.cons)


def test_main_road_share_is_compared_only_where_known():
    calm = route("calm", 11, 33, 50, main=0.05)
    busy = route("busy", 10, 30, 50, main=0.60)
    unknown = route("unknown", 10.5, 31, 50)
    calm_a, busy_a, unknown_a = run([calm, busy, unknown])
    assert "Least on main roads without a bike lane (5%)" in calm_a.pros
    assert "60% on main roads without a bike lane" in busy_a.cons
    # no tags for this one: neither a pro nor a con, and it is not treated as 0 %
    assert not any("main roads" in x for x in unknown_a.pros + unknown_a.cons)


def test_a_main_road_gap_under_ten_points_is_ignored():
    a, b = run([route("a", 10, 30, 50, main=0.20), route("b", 10, 30, 50, main=0.25)])
    assert not any("main roads" in x for x in a.pros + b.pros + a.cons + b.cons)


def test_the_riders_own_limits_come_first():
    flat = route("flat", 12, 40, 700)
    steep = route("steep", 10, 35, 900)
    a, b = run([flat, steep], RouteConstraints(max_ascent_m=800, max_distance_km=11))
    assert b.cons[0] == "Over your climbing limit (900 m > 800 m)"
    assert a.cons[0] == "Over your distance limit (12.0 km > 11 km)"


def test_the_route_nearest_a_target_distance_says_so():
    near = route("near", 49.0, 120, 100)
    far = route("far", 60.0, 140, 100)
    a, _ = run([near, far], RouteConstraints(target_distance_km=50))
    assert "Closest to your 50 km target (49.0 km)" in a.pros


def test_precipitation_and_wind_are_compared_when_forecast_for_both():
    dry = route("dry", 10, 30, 50, weather=weather(wet=False, headwind=2))
    wet = route("wet", 10, 30, 50, weather=weather(wet=True, headwind=12))
    dry_a, wet_a = run([dry, wet])
    assert "Dry along the route" in dry_a.pros and "Least headwind" in dry_a.pros
    assert "Precipitation forecast along it" in wet_a.cons
    assert "10 km/h more headwind on average" in wet_a.cons
    same = run(
        [
            route("a", 10, 30, 50, weather=weather(wet=True)),
            route("b", 10, 30, 50, weather=weather(wet=True)),
        ]
    )
    assert all("recipitation" not in x and "Dry" not in x for r in same for x in r.pros + r.cons)


def test_near_copies_are_left_out_of_the_comparison():
    quick = route("quick", 10, 30, 50)
    copy = route("copy", 15, 60, 500, duplicate_of="brouter/quick")
    other = route("other", 12, 36, 50)
    q, c, o = run([quick, copy, other])
    assert c.pros == [] and c.cons == []
    assert "2.0 km longer than the shortest" in o.cons  # judged against `quick`, not the copy


def test_each_list_is_capped_and_wording_is_factual():
    a, b = run([route("a", 10, 20, 10, main=0.0), route("b", 20, 40, 200, main=0.9)])
    assert len(a.pros) <= 3 and len(b.cons) <= 3
    banned = ("safe", "safer", "danger", "best for you")
    assert not any(w in line.lower() for line in a.pros + a.cons + b.pros + b.cons for w in banned)


def test_the_node_annotates_state_and_keeps_the_selection_in_sync():
    node = build_compare_node()
    quick, calm = route("quick", 10, 30, 120), route("calm", 12, 40, 40)
    state = {
        "candidates": [quick.model_dump(mode="json"), calm.model_dump(mode="json")],
        "selected_candidate": quick.model_dump(mode="json"),
        "constraints": {},
    }
    update = node(state)
    assert "Shortest (10.0 km)" in update["candidates"][0]["pros"]
    assert update["selected_candidate"] == update["candidates"][0]
    assert node({"candidates": [quick.model_dump(mode="json")]}) == {}
