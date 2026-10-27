"""Asking the chat to optimise: "less traffic but as much asphalt as possible"."""

from __future__ import annotations

from typing import Any

import pytest

from bike_routing_agent.chat import goals
from bike_routing_agent.chat.rules import classify, parse_one_line

from .test_chat_graph import FakePlanner, Talk, chat, ready_plan


@pytest.mark.parametrize(
    ("text", "metrics"),
    [
        ("with less traffic but as much asphalt as possible", ["traffic", "paved"]),
        ("optimize for the quietest route", ["traffic"]),
        ("make it quieter and more paved", ["traffic", "paved"]),
        ("the fastest and flattest", ["duration", "ascent"]),
        ("less traffic and more gravel", ["traffic", "unpaved"]),
        ("optimise for smooth roads, no cobbles", ["paved", "cobbles"]),
        ("the shortest", ["distance"]),
    ],
)
def test_goals_are_read_in_the_order_they_were_said(text, metrics):
    found = goals.detect(text)
    assert found is not None and found.metrics == metrics
    weights = [g["weight"] for g in found.spec["goals"]]
    assert weights == sorted(weights, reverse=True) and weights[0] == 1.0


@pytest.mark.parametrize(
    ("text", "metrics"),
    [
        ("optimise for little climbing", ["ascent"]),
        ("optimise for quiet roads and little climbing", ["traffic", "ascent"]),
        ("optimize for fewer hills and less traffic", ["ascent", "traffic"]),
        ("quiet and flat please", ["traffic", "ascent"]),
        ("the flattest route with no busy roads", ["ascent", "traffic"]),
        ("not too hilly and away from busy roads", ["ascent", "traffic"]),
        ("find the route with the fewest busy roads", ["traffic"]),
        ("wenig Verkehr und kaum Steigung", ["traffic", "ascent"]),
        ("ruhig und flach", ["traffic", "ascent"]),
    ],
)
def test_more_ways_of_saying_quiet_and_flat_are_read_as_goals(text, metrics):
    found = goals.detect(text)
    assert found is not None and found.metrics == metrics


@pytest.mark.parametrize(
    "text",
    ["a bit flatter", "make it quieter", "flatter and shorter", "I got a flat tyre"],
)
def test_the_new_words_do_not_turn_every_wish_into_an_optimisation(text):
    # a lone mild wish ("flatter") stays an ordinary change; "flat" alone names one goal only
    assert goals.detect(text) is None


def test_a_flat_tyre_is_not_a_flat_route():
    assert goals.detect("make it quieter because I have a flat tyre") is None
    assert goals.detect("quiet and flat, my flat tire is mended") is not None


def test_the_fewest_busy_roads_names_the_quietest_of_the_routes_already_found():
    from bike_routing_agent.chat.rules import pick_candidate

    shown = [
        {"rank": 1, "metrics": {"traffic_index": 0.30}},
        {"rank": 2, "metrics": {"traffic_index": 0.10}},
    ]
    assert pick_candidate("show the route with the fewest busy roads", shown)["rank"] == 2


async def test_what_else_the_goal_message_asks_for_is_kept_when_it_is_optimised():
    service, planner = chat(FakePlanner(lambda r: ready_plan()))
    t = Talk(service)
    await t.say("Braunschweig to Goslar")
    reply = await t.say("avoid ferries and fewer hills and less traffic")
    request = planner.requests[-1]
    assert [g["metric"] for g in request["optimize"]["goals"]] == ["ascent", "traffic"]
    assert request["constraints"]["avoid_ferries"] is True  # not dropped by the optimisation
    assert "ferr" in reply.reply.lower()


async def test_a_goal_message_with_words_nobody_reads_still_optimises():
    service, planner = chat(FakePlanner(lambda r: ready_plan()))
    t = Talk(service)
    await t.say("Braunschweig to Goslar")
    await t.say("please be kind, quiet and flat")
    assert [g["metric"] for g in planner.requests[-1]["optimize"]["goals"]] == ["traffic", "ascent"]


async def test_a_loop_with_two_goals_is_planned_and_optimised_in_one_line():
    line = parse_one_line("40 km loop from Goslar, quiet and flat")
    assert line.loop and line.origin == "Goslar" and line.distance_km == 40.0
    assert [g["metric"] for g in line.optimize["goals"]] == ["traffic", "ascent"]


@pytest.mark.parametrize(
    "text",
    [
        "quieter",
        "avoid busy roads",
        "paved only",
        "a bit flat",
        "Nice to Cannes",
        "shorter",
        "less traffic and more gravel and as much asphalt as possible",
    ],  # fmt: skip
)
def test_a_mild_wish_or_a_contradiction_is_not_an_optimisation(text):
    assert goals.detect(text) is None


def test_the_goal_words_leave_the_places_alone():
    cases = {
        "Braunschweig to Wernigerode with less traffic but as much asphalt as possible": (
            "Braunschweig", "Wernigerode"),
        "Braunschweig to Goslar, a quiet and smooth route": ("Braunschweig", "Goslar"),
        "the quietest route from Goslar to Hannover": ("Goslar", "Hannover"),
        "optimize Goslar to Hannover for the fastest": ("Goslar", "Hannover"),
    }  # fmt: skip
    for text, (origin, destination) in cases.items():
        line = parse_one_line(text)
        assert (line.origin, line.destination) == (origin, destination), text
        assert line.optimize and line.optimize["goals"], text


def test_classify_sends_goals_to_the_optimiser_only_with_a_route_to_work_on():
    assert classify("optimise for less traffic and more asphalt", has_plan=True) == "optimize"
    assert classify("optimise for less traffic and more asphalt", has_plan=False) is None
    assert classify("avoid busy roads", has_plan=True) == "refine"  # not an optimisation


def with_report(request: dict[str, Any]) -> dict[str, Any]:
    plan = ready_plan()
    plan["optimization"] = {
        "summary": "Compared 6 routes (5 distinct) for less traffic and more asphalt. "
        "Best fit: brouter/trekking -- traffic index 0.10 (the best was 0.06); 97 % paved "
        "(the best of the routes compared); 3.0 km (5 %) longer than the shortest route found.",
        "goals": [{"metric": "traffic"}, {"metric": "paved"}],
        "tradeoffs": ["brouter/custom_commuter-v1 has traffic index 0.06 (less traffic) but is "
                      "3.0 km shorter and lower on the other goals"],
        "table": [
            {"selected": True, "label": "brouter/trekking", "distance_km": 66.0,
             "shown": {"traffic": "traffic index 0.10", "paved": "97 % paved"}},
            {"selected": False, "label": "brouter/custom_commuter-v1", "distance_km": 63.0,
             "shown": {"traffic": "traffic index 0.06", "paved": "82 % paved"}},
        ],
    }  # fmt: skip
    return plan


async def test_a_one_line_request_with_goals_fetches_several_routes_and_reports_the_choice():
    service, planner = chat(FakePlanner(with_report))
    r = await Talk(service).say(
        "Braunschweig to Wernigerode by gravel bike with less traffic "
        "but as much asphalt as possible"
    )
    sent = planner.requests[0]
    assert sent["origin"] == "Braunschweig" and sent["destination"] == "Wernigerode"
    assert sent["optimize"] == {
        "goals": [{"metric": "traffic", "weight": 1.0}, {"metric": "paved", "weight": 0.7}]
    }
    assert "optimised for less traffic and more asphalt" in r.reply
    assert "fetching several routes" in r.reply
    assert (
        "Best fit: brouter/trekking" in r.reply
        and "Next best: brouter/custom_commuter-v1" in r.reply
    )
    assert "lower on the other goals" in r.reply


async def test_optimising_the_route_on_screen_keeps_its_request_and_adds_the_goals():
    service, planner = chat(FakePlanner(lambda request: with_report(request)))
    t = Talk(service)
    await t.say("Braunschweig to Wernigerode by gravel bike, no ferries")
    assert "optimize" not in planner.requests[0]
    r = await t.say("now optimise it for less traffic and as much asphalt as possible")
    again = planner.requests[1]
    assert r.intent == "optimize" and again["origin"] == "Braunschweig"
    assert again["constraints"]["bike_type"] == "gravel" and again["constraints"]["avoid_ferries"]
    assert [g["metric"] for g in again["optimize"]["goals"]] == ["traffic", "paved"]
    assert r.plan is not None and "Compared 6 routes" in r.reply


async def test_naming_one_of_the_routes_already_found_picks_it_instead_of_searching_again():
    def two(request):
        plan = with_report(request)
        plan["candidates"][0]["metrics"]["traffic_index"] = 0.30
        plan["candidates"][1]["metrics"]["traffic_index"] = 0.05
        plan["route"] = plan["candidates"][0]
        return plan

    service, planner = chat(FakePlanner(two))
    t = Talk(service)
    await t.say("Braunschweig to Wernigerode")
    r = await t.say("the quietest")
    assert r.intent == "alternatives" and r.focus_rank == 2 and len(planner.requests) == 1
    again = await t.say("optimize for the quietest")  # asked to search: it does
    assert again.intent == "optimize" and len(planner.requests) == 2


async def test_without_a_route_the_optimiser_says_what_to_do_first():
    service, planner = chat()
    r = await Talk(service).say("optimise for less traffic and more asphalt")
    assert planner.requests == [] and r.plan is None


# ------------------------------------------------------------------------------- sights


@pytest.mark.parametrize(
    ("text", "metrics"),
    [
        ("with as many sights as possible", ["pois"]),
        ("with less traffic and the most sights along the way", ["traffic", "pois"]),
        ("optimise for quiet roads and as many viewpoints as possible", ["traffic", "pois"]),
        ("more sights", ["pois"]),
        ("past as many castles as possible", ["pois"]),
    ],
)
def test_sights_along_the_track_are_a_goal(text, metrics):
    found = goals.detect(text)
    assert found is not None and found.metrics == metrics


@pytest.mark.parametrize(
    "text",
    [
        "Goslar to Hannover past the most famous sights",
        "Goslar to Hannover along the most famous sights",
        "Goslar to Hannover past 2 castles",
        "Goslar to Hannover via 3 castles",
    ],
)
def test_a_request_for_stops_stays_a_request_for_stops(text):
    assert goals.detect(text) is None
    line = parse_one_line(text)
    assert line.sight_stops and not line.optimize
    assert (line.origin, line.destination) == ("Goslar", "Hannover")


async def test_the_reply_names_the_places_the_chosen_route_passes():
    def sights(request: dict[str, Any]) -> dict[str, Any]:
        plan = with_report(request)
        plan["optimization"]["pois"] = ["Burg Dankwarderode", "Kloster Riddagshausen", "Okerturm"]
        plan["optimization"]["goals"] = [{"metric": "pois"}]
        return plan

    service, planner = chat(FakePlanner(sights))
    r = await Talk(service).say("Braunschweig to Wernigerode with as many sights as possible")
    assert planner.requests[0]["optimize"]["goals"] == [{"metric": "pois", "weight": 1.0}]
    assert "optimised for more sights along the route" in r.reply
    assert "It passes: Burg Dankwarderode, Kloster Riddagshausen, Okerturm." in r.reply


async def test_a_missing_sight_lookup_is_said():
    def none(request: dict[str, Any]) -> dict[str, Any]:
        plan = with_report(request)
        plan["optimization"]["poi_status"] = "unavailable"
        return plan

    service, _ = chat(FakePlanner(none))
    r = await Talk(service).say("Braunschweig to Wernigerode with as many sights as possible")
    assert "sight lookup was not available" in r.reply
