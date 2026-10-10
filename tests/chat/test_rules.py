import pytest

from bike_routing_agent.chat.rules import (
    OneLine,
    apply_refinement,
    classify,
    parse_one_line,
    pick_candidate,
    place_value,
    refine,
)

# ------------------------------------------------------------------------- one-line requests


@pytest.mark.parametrize(
    ("text", "origin", "destination"),
    [
        ("from Braunschweig to Wolfenbüttel", "Braunschweig", "Wolfenbüttel"),
        ("Braunschweig to Goslar", "Braunschweig", "Goslar"),
        ("Braunschweig -> Goslar", "Braunschweig", "Goslar"),
        ("Stoke on Trent to Leeds", "Stoke on Trent", "Leeds"),
        ("plan a route from Oxford to Cambridge", "Oxford", "Cambridge"),
        ("please give me a route from Bremen to Hamburg.", "Bremen", "Hamburg"),
        ("52.26,10.52 to 52.49,10.55", "52.26,10.52", "52.49,10.55"),
    ],
)
def test_a_start_and_a_destination_are_read_from_one_line(text, origin, destination):
    line = parse_one_line(text)
    assert (line.origin, line.destination, line.loop) == (origin, destination, False)
    assert line.complete()


def test_the_bike_a_distance_vias_and_sight_stops_are_taken_out_of_the_places():
    line = parse_one_line(
        "plan a route from Oxford to Cambridge on my road bike via Bicester and Bedford"
    )
    assert (line.origin, line.destination) == ("Oxford", "Cambridge")
    assert line.bike_type == "road" and line.via == ["Bicester", "Bedford"]

    line = parse_one_line("gravel from Füssen to Oberammergau past 2 castles")
    assert (line.origin, line.destination) == ("Füssen", "Oberammergau")
    assert line.bike_type == "gravel" and line.sight_stops == 2

    line = parse_one_line("Goslar to Wernigerode by e-bike, the most famous sights")
    assert line.bike_type == "ebike" and line.sight_stops is None  # no "past/along": not asked
    assert (line.origin, line.destination) == ("Goslar", "Wernigerode")


@pytest.mark.parametrize(
    ("text", "origin", "km", "bike"),
    [
        ("40 km loop from Goslar", "Goslar", 40.0, None),
        ("loop from Goslar, 25 km", "Goslar", 25.0, None),
        (
            "round trip around Braunschweig, 30 km on a mountain bike",
            "Braunschweig",
            30.0,
            "mountain",
        ),
        ("a 50,5 km circular route starting in Bad Harzburg", "Bad Harzburg", 50.5, None),
        ("loop 25 km from 52.26,10.52", "52.26,10.52", 25.0, None),
    ],
)
def test_loops_need_a_start_and_a_distance(text, origin, km, bike):
    line = parse_one_line(text)
    assert line.loop and (line.origin, line.distance_km, line.bike_type) == (origin, km, bike)
    assert line.complete()


def test_a_loop_without_a_distance_is_not_complete_enough_to_plan():
    line = parse_one_line("a loop from Goslar")
    assert line.loop and line.origin == "Goslar" and not line.complete()


@pytest.mark.parametrize(
    "text", ["hello", "what can you do", "gravel route to the lake", "to Goslar"]
)
def test_things_that_are_not_a_route_request_are_not_read_as_one(text):
    assert not parse_one_line(text).complete()


def test_sight_counts():
    for text, count in [
        ("A to B past 3 castles", 3),
        ("A to B along the most famous sights", 2),
        ("A to B past two viewpoints", 2),
        ("A to B passing one castle", 1),
        ("A to B past 9 sights", 5),  # capped like the API
        ("A to B", None),
    ]:
        assert parse_one_line(text).sight_stops == count, text


def test_coordinates_become_coordinates_and_words_stay_words():
    assert place_value("52.26, 10.52") == {"lon": 10.52, "lat": 52.26}
    assert place_value(" Braunschweig. ") == "Braunschweig"
    assert place_value("95.0, 10.0") == "95.0, 10.0"  # not a latitude: left for the geocoder
    assert OneLine(origin="A", destination="B").complete()


# ----------------------------------------------------------------------------- intents


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("new route", "reset"),
        ("start over", "reset"),
        ("help", "help"),
        ("what can you do?", "help"),
        ("send me the gpx", "export"),
        ("can I download it?", "export"),
        ("show me the alternatives", "alternatives"),
        ("any other options?", "alternatives"),
        ("take the second one", "alternatives"),
        ("show the mtb version", "alternatives"),
        ("what sights are along the way?", "sights"),
        ("any castles near the route", "sights"),
        ("what's the weather like?", "explain"),
        ("how steep is it", "explain"),
        ("why this route?", "explain"),
        ("make it shorter", "refine"),
        ("avoid ferries", "refine"),
        ("on a road bike please", "refine"),
        ("add a stop at Bicester", "refine"),
        ("flatter", "refine"),
    ],
)
def test_follow_ups_are_recognised_once_there_is_a_route(text, intent):
    assert classify(text, has_plan=True) == intent


def test_without_a_route_only_help_and_reset_apply():
    assert classify("new route", has_plan=False) == "reset"
    assert classify("help", has_plan=False) == "help"
    for text in ("make it shorter", "show the alternatives", "weather", "send me the gpx"):
        assert classify(text, has_plan=False) is None
    assert classify("from A to B", has_plan=True) is None  # a new request, not a follow-up


# ---------------------------------------------------------------------------- refinements

PREVIOUS = {
    "origin": "Goslar",
    "destination": None,
    "via": [],
    "constraints": {
        "bike_type": "gravel",
        "target_distance_km": 40.0,
        "return_to_origin": True,
        "loop_direction": "clockwise",
    },
}


def test_shorter_and_longer_scale_the_distance_that_was_asked_for():
    change = refine("make it shorter", PREVIOUS)
    assert change.constraints == {"target_distance_km": 30.0} and not change.unknown
    new = apply_refinement(PREVIOUS, change)
    assert new["constraints"]["target_distance_km"] == 30.0
    assert PREVIOUS["constraints"]["target_distance_km"] == 40.0  # the old request is untouched
    assert refine("longer please", PREVIOUS).constraints == {"target_distance_km": 50.0}


def test_shorter_without_any_distance_uses_the_current_route_or_asks_for_one():
    plain = {"origin": "A", "destination": "B", "constraints": {"bike_type": "road"}}
    change = refine("shorter", plain)
    assert change.unknown and change.constraints == {} and "needs one" in change.notes[0]
    # With the current route's length the cap is explicit: 25 % under what it is now.
    change = refine("shorter", plain, last_distance_m=56_700)
    assert change.constraints == {"max_distance_km": 42.5} and not change.unknown
    assert "25 % shorter than the current route" in change.notes[0]


def test_flatter_uses_the_climb_of_the_last_route_and_says_so_when_it_does_not_know():
    change = refine("a bit flatter", PREVIOUS, last_ascent_m=200)
    assert change.constraints == {"max_ascent_m": 140.0}
    assert refine("flatter", PREVIOUS).unknown
    assert refine("max 80 m of climbing", PREVIOUS).constraints == {"max_ascent_m": 80.0}


def test_bike_ferries_traffic_and_surfaces():
    assert refine("use a road bike", PREVIOUS).constraints["bike_type"] == "road"
    assert refine("on my mountain bike instead", PREVIOUS).constraints["bike_type"] == "mountain"
    assert refine("avoid ferries", PREVIOUS).constraints["avoid_ferries"] is True
    assert refine("allow ferries", PREVIOUS).constraints["avoid_ferries"] is False
    assert refine("avoid busy traffic", PREVIOUS).constraints["avoid_high_traffic_roads"] is True
    assert refine("paved only", PREVIOUS).constraints["prefer_surfaces"] == ["paved"]
    assert refine("more gravel", PREVIOUS).constraints["prefer_surfaces"] == ["gravel"]
    no_gravel = refine("avoid gravel", PREVIOUS).constraints
    assert no_gravel["avoid_surfaces"] == ["gravel", "sand"]


def test_a_new_distance_with_a_loop_changes_the_target_a_cap_changes_the_maximum():
    assert refine("make it 25 km", PREVIOUS).constraints == {"target_distance_km": 25.0}
    assert refine("at most 20 km", PREVIOUS).constraints == {"max_distance_km": 20.0}


def test_loop_direction_flips_or_is_named():
    assert refine("other direction", PREVIOUS).constraints["loop_direction"] == "counterclockwise"
    assert (
        refine("counterclockwise please", PREVIOUS).constraints["loop_direction"]
        == "counterclockwise"
    )
    assert refine("clockwise", PREVIOUS).constraints["loop_direction"] == "clockwise"


def test_vias_sight_stops_and_the_engine():
    new = apply_refinement(PREVIOUS, refine("add a stop at Bicester", PREVIOUS))
    assert new["via"] == ["Bicester"]
    new = apply_refinement(new, refine("also via 52.2, 10.5", new))
    assert new["via"] == ["Bicester", {"lon": 10.5, "lat": 52.2}]
    assert apply_refinement(new, refine("remove the via points", new))["via"] == []
    stops = apply_refinement(PREVIOUS, refine("route past 2 castles", PREVIOUS))
    assert stops["poi_stops"] == {"count": 2}
    assert "poi_stops" not in apply_refinement(stops, refine("no sights", stops))
    engine = refine("use openrouteservice", PREVIOUS)
    assert engine.routing_engines == ["ors"]
    assert apply_refinement(PREVIOUS, engine)["routing_engines"] == ["ors"]


def test_a_message_that_changes_nothing_is_reported_as_not_understood():
    change = refine("make it so", PREVIOUS)
    assert change.empty() and change.unknown


# ----------------------------------------------------------------- picking an alternative

CANDIDATES = [
    {"rank": 1, "provider": "brouter", "provider_profile": "custom_gravel-v2",
     "metrics": {"distance_m": 30600, "duration_s": 4200, "ascent_m": 16}},
    {"rank": 2, "provider": "brouter", "provider_profile": "mtb",
     "metrics": {"distance_m": 28200, "duration_s": 4740, "ascent_m": 18}},
    {"rank": 3, "provider": "ors", "provider_profile": "cycling-regular",
     "metrics": {"distance_m": 29000, "duration_s": 4300, "ascent_m": None}},
]  # fmt: skip


@pytest.mark.parametrize(
    ("text", "rank"),
    [
        ("the second one", 2),
        ("take the 3rd option", 3),
        ("option 2", 2),
        ("show alternative #1", 1),
        ("the fastest", 1),
        ("the shortest", 2),
        ("the flattest", 1),  # missing ascent is ignored, never treated as 0
        ("the mtb version", 2),
        ("use the mountain route", 2),
        ("the ors one", 3),
    ],
)
def test_an_alternative_is_found_by_rank_profile_or_what_it_is_best_at(text, rank):
    assert pick_candidate(text, CANDIDATES)["rank"] == rank  # type: ignore[index]


def test_a_name_that_matches_nothing_picks_nothing():
    assert pick_candidate("the purple one", CANDIDATES) is None
    assert pick_candidate("the fifth one", CANDIDATES) is None
    assert pick_candidate("the fastest", []) is None
