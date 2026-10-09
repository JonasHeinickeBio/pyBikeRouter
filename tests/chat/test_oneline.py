"""One-line route requests read into usable requests: many phrasings, one real validation.

Every case is turned into the request the chat would send and checked with the API's own model
(``RoutePlanAPIRequest``), so "parsed" means "the API accepts it" and the places are the words
a geocoder could look up -- not the whole sentence.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bike_routing_agent.chat.graph import request_from_line
from bike_routing_agent.chat.rules import parse_one_line
from bike_routing_agent.models import RoutePlanAPIRequest

# A Wednesday noon in Berlin: "tomorrow", "Saturday" and "at 8" have one right answer.
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))


def read(text: str):
    line = parse_one_line(text, now=NOW)
    request = request_from_line(line)
    RoutePlanAPIRequest.model_validate(request)  # the API would accept it
    return line, request


PLAIN = [
    ("from Braunschweig to Goslar", "Braunschweig", "Goslar"),
    ("Braunschweig to Goslar", "Braunschweig", "Goslar"),
    ("Braunschweig -> Goslar", "Braunschweig", "Goslar"),
    ("Braunschweig → Goslar", "Braunschweig", "Goslar"),
    ("Bremen - Hamburg", "Bremen", "Hamburg"),
    ("route from Berlin to Potsdam", "Berlin", "Potsdam"),
    ("route Berlin to Potsdam", "Berlin", "Potsdam"),
    ("I want to cycle from Hamburg to Lübeck", "Hamburg", "Lübeck"),
    ("can you plan a ride from Bremen to Hamburg?", "Bremen", "Hamburg"),
    ("please route me from Bremen to Hamburg", "Bremen", "Hamburg"),
    ("bike tour from Goslar to Quedlinburg", "Goslar", "Quedlinburg"),
    ("300 km from Goslar to Munich", "Goslar", "Munich"),
    ("Von Goslar nach Hannover", "Goslar", "Hannover"),
    ("bremen nach hamburg", "bremen", "hamburg"),
    # Places that contain words which are also clause words.
    ("New York to Boston", "New York", "Boston"),
    ("Long Beach to Santa Monica", "Long Beach", "Santa Monica"),
    ("Nice to Cannes", "Nice", "Cannes"),
    ("Mountain View to San Jose", "Mountain View", "San Jose"),
    ("City Park to Central Station", "City Park", "Central Station"),
    ("Stoke-on-Trent to Manchester", "Stoke-on-Trent", "Manchester"),
    ("Newcastle upon Tyne to Carlisle", "Newcastle upon Tyne", "Carlisle"),
    ("Weston-super-Mare to Bristol", "Weston-super-Mare", "Bristol"),
    ("Frankfurt am Main to Heidelberg", "Frankfurt am Main", "Heidelberg"),
    ("Rothenburg ob der Tauber to Würzburg", "Rothenburg ob der Tauber", "Würzburg"),
    ("Frankfurt (Oder) to Słubice", "Frankfurt (Oder)", "Słubice"),
    ("St. Andrews to Edinburgh", "St. Andrews", "Edinburgh"),
    ("The Hague to Amsterdam", "The Hague", "Amsterdam"),
    ("A to B", "A", "B"),
]


@pytest.mark.parametrize(("text", "origin", "destination"), PLAIN)
def test_the_places_are_the_places_and_nothing_else(text, origin, destination):
    _, request = read(text)
    assert (request["origin"], request["destination"]) == (origin, destination)
    assert request["via"] == [] and request["constraints"] == {}


def test_coordinates_stay_coordinates():
    _, request = read("52.2688, 10.5268 to 51.9054, 10.4281")
    assert request["origin"] == {"lon": 10.5268, "lat": 52.2688}
    assert request["destination"] == {"lon": 10.4281, "lat": 51.9054}
    _, request = read("from 52.27,10.52 to Goslar")
    assert request["origin"] == {"lon": 10.52, "lat": 52.27} and request["destination"] == "Goslar"


@pytest.mark.parametrize(
    ("text", "bike"),
    [
        ("ride from Hamburg to Lübeck on my road bike", "road"),
        ("Bremen to Hamburg on a city bike", "city"),
        ("Bad Harzburg to Braunschweig by e-bike", "ebike"),
        ("Hamburg to Berlin by touring bike", "touring"),
        ("Munich to Salzburg by trekking bike", "touring"),
        ("gravel from Füssen to Oberammergau", "gravel"),
        ("Goslar to Hannover with my mtb", "mountain"),
        ("Goslar to Hannover on a gravel bike", "gravel"),
        ("mountain bike ride from Goslar to Hannover", "mountain"),
    ],
)
def test_the_bike_is_taken_out_of_the_places(text, bike):
    _, request = read(text)
    assert request["constraints"]["bike_type"] == bike
    assert request["origin"] in (
        "Hamburg",
        "Bremen",
        "Bad Harzburg",
        "Hamburg",
        "Munich",
        "Füssen",
        "Goslar",
    )
    assert request["destination"] in (
        "Lübeck", "Hamburg", "Braunschweig", "Berlin", "Salzburg", "Oberammergau", "Hannover"
    )  # fmt: skip


def test_via_places_and_legs():
    _, request = read("bike tour from Goslar to Quedlinburg via Bad Harzburg and Ilsenburg")
    assert (
        request["via"] == ["Bad Harzburg", "Ilsenburg"] and request["destination"] == "Quedlinburg"
    )
    _, request = read("Goslar via Ilsenburg to Wernigerode")
    assert (request["origin"], request["via"], request["destination"]) == (
        "Goslar", ["Ilsenburg"], "Wernigerode"
    )  # fmt: skip
    _, request = read("Hildesheim → Goslar → Braunschweig")  # the middle place is a stop
    assert request["via"] == ["Goslar"] and request["destination"] == "Braunschweig"
    _, request = read("Munich to Salzburg über Rosenheim")
    assert request["via"] == ["Rosenheim"]


def test_out_and_back_is_a_round_trip_through_the_far_place():
    for text in ("Goslar to Hannover and back", "Goslar to Hannover, then back"):
        _, request = read(text)
        assert request["origin"] == "Goslar" and request["destination"] == "Goslar"
        assert request["via"] == ["Hannover"], text
        assert "return_to_origin" not in request["constraints"]  # it has a destination: no length


LOOPS = [
    ("50km round trip from Goslar", "Goslar", 50.0),
    ("40 km loop from Goslar", "Goslar", 40.0),
    ("a 40 km loop around Braunschweig", "Braunschweig", 40.0),
    ("30 km circular route from Wolfenbüttel", "Wolfenbüttel", 30.0),
    ("round trip Goslar 30 km", "Goslar", 30.0),
    ("loop 25 km from 52.2688, 10.5268", {"lon": 10.5268, "lat": 52.2688}, 25.0),
    ("10 km loop from Goslar", "Goslar", 10.0),
    ("I'd like a relaxed 50 km loop around Wolfenbüttel", "Wolfenbüttel", 50.0),
    ("loop of 80 km starting in Bad Harzburg", "Bad Harzburg", 80.0),
    ("30,5 km Rundtour von Goslar", "Goslar", 30.5),
]


@pytest.mark.parametrize(("text", "origin", "km"), LOOPS)
def test_loops_have_a_start_and_a_length(text, origin, km):
    line, request = read(text)
    assert line.loop and request["origin"] == origin and "destination" not in request
    assert request["constraints"]["return_to_origin"] is True
    assert request["constraints"]["target_distance_km"] == km


def test_a_loop_length_can_be_given_in_hours_and_the_assumption_is_said():
    line, request = read("round trip from Goslar for 2 hours on my gravel bike")
    assert request["constraints"]["target_distance_km"] == 40.0  # 2 h at gravel's 20 km/h
    assert request["constraints"]["bike_type"] == "gravel"
    assert "20 km/h" in line.assumed[0]
    _, request = read("90 minute loop from Goslar")
    assert request["constraints"]["target_distance_km"] == 27.0  # 1.5 h at the default 18 km/h
    _, request = read("a half an hour loop from Goslar on my road bike")
    assert request["constraints"]["target_distance_km"] == 12.5


def test_a_loop_with_the_start_only_in_a_word_order_is_still_a_loop():
    line = parse_one_line("Goslar to Goslar", now=NOW)
    assert line.loop and line.origin == "Goslar" and not line.complete()  # the length is asked


def test_constraints_become_constraints_not_part_of_a_place():
    cases = {
        "Goslar to Wernigerode avoiding main roads": {"avoid_high_traffic_roads": True},
        "Goslar to Wernigerode, no ferries": {"avoid_ferries": True},
        "Goslar to Wernigerode, paved only": {"prefer_surfaces": ["paved"], "avoid_surfaces": []},
        "Goslar to Wernigerode, avoid unpaved": {
            "avoid_surfaces": ["gravel", "sand"], "prefer_surfaces": ["paved"]
        },
        "Goslar to Hannover max 400 m climb": {"max_ascent_m": 400.0},
        "Goslar to Hannover with at most 300 m of climbing": {"max_ascent_m": 300.0},
        "Goslar to Hannover no more than 80 km": {"max_distance_km": 80.0},
        "Goslar to Hannover, 80 km max": {"max_distance_km": 80.0},
        "Goslar to Hannover under 90 km": {"max_distance_km": 90.0},
        "Hannover to Hamburg avoiding highways, quiet roads only": {
            "avoid_high_traffic_roads": True
        },
        "Hamburg to Lübeck, avoid unpaved and busy roads": {
            "avoid_surfaces": ["gravel", "sand"],
            "prefer_surfaces": ["paved"],
            "avoid_high_traffic_roads": True,
        },
    }  # fmt: skip
    for text, expected in cases.items():
        _, request = read(text)
        assert request["constraints"] == expected, text
        assert request["destination"] in ("Wernigerode", "Hannover", "Hamburg", "Lübeck"), text
        assert request["origin"] in ("Goslar", "Hannover", "Hamburg"), text


def test_loop_direction_and_the_engine():
    _, request = read("gravel loop 45 km near Goslar counterclockwise")
    assert request["constraints"]["loop_direction"] == "counterclockwise"
    assert request["origin"] == "Goslar"
    _, request = read("Bremen to Hamburg using openrouteservice")
    assert request["routing_engines"] == ["ors"] and request["destination"] == "Hamburg"


def test_sights_are_counted_and_not_part_of_the_places():
    for text, count in (
        ("Füssen to Oberammergau past 2 castles", 2),
        ("Oxford to Cambridge by road bike, 2 sights", 2),
        ("Oxford to Cambridge along the most famous sights", 2),
        ("Oxford to Cambridge past three museums", 3),
    ):
        _, request = read(text)
        assert request["poi_stops"] == {"count": count}, text
        assert request["destination"] in ("Oberammergau", "Cambridge"), text


def test_what_cannot_be_applied_is_reported_and_stays_out_of_the_places():
    cases = {
        "Quedlinburg to Wernigerode, flat": "flat",
        "Goslar to Hannover on a hilly route": "hilly",
        "From Cologne to Düsseldorf along the Rhine": "along the Rhine",
        "Hamburg to Berlin by touring bike with luggage": "with luggage",
        "Goslar to Hannover with my kids": "with my kids",
        "Goslar to Hannover for 3 days": "for 3 days",
        "Goslar to Hannover at 80 km/h": "80 km/h",
        "300 km from Goslar to Munich": "300 km",
        "Goslar to Hannover in 2 hours": "2 h",
    }
    for text, mention in cases.items():
        line, request = read(text)
        assert any(mention in note for note in line.ignored), (text, line.ignored)
        assert request["destination"] in (
            "Wernigerode",
            "Düsseldorf",
            "Berlin",
            "Hannover",
            "Munich",
        )


def test_flat_is_not_ignored_when_a_climb_limit_is_given():
    line, _ = read("Quedlinburg to Wernigerode, flat, max 200 m climb")
    assert not any("flat" in note for note in line.ignored)


def test_something_that_is_not_a_route_is_not_planned():
    for text in (
        "ride", "a nice ride", "avoid ferries", "40km", "30 km loop", "Goslar", "to Hannover",
        "from Goslar", "I would love a nice relaxing ride this weekend",
        "Berlin to Dresden, I have 6 hours and a headache",
    ):  # fmt: skip
        assert not parse_one_line(text, now=NOW).complete(), text


def test_a_sentence_is_never_taken_as_a_place():
    for text in (
        "what is the weather in Goslar to be expected tomorrow at all",
        "Goslar to Hannover on a road trip",
    ):
        line = parse_one_line(text, now=NOW)
        assert not line.complete() or (line.origin, line.destination) == ("Goslar", "Hannover")
