"""The chat graph end to end with a scripted planner: every way of getting a route, changing it,
asking about it, and the pause-and-resume of an ambiguous place."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from bike_routing_agent.chat.models import PlanError, TextUnavailableError
from bike_routing_agent.chat.service import FAILURE_REPLY, ChatService


def candidate(rank: int, profile: str = "custom_gravel-v2", **kw: Any) -> dict[str, Any]:
    return {
        "provider": "brouter",
        "provider_profile": profile,
        "rank": rank,
        "score": 0.9,
        "geometry_geojson": {
            "type": "LineString",
            "coordinates": [[10.5, 52.2], [10.55, 52.25], [10.6, 52.3]],
        },
        "metrics": {
            "distance_m": 30_600.0 - rank * 500,
            "duration_s": 4200.0 + rank * 300,
            "ascent_m": 160.0 + rank,
            "descent_m": 150.0,
            "engine_surface_shares": {"paved": 0.6, "unpaved": 0.4},
        },
        "pros": [f"pro {rank}"],
        "cons": [f"con {rank}"],
        "warnings": [],
        **kw,
    }


def ready_plan(**extra: Any) -> dict[str, Any]:
    first, second = candidate(1), candidate(2, "mtb")
    return {
        "status": "ready",
        "route": first,
        "candidates": [first, second],
        "explanation": "It is the best fit.",
        "artifacts": {"gpx_url": "/v1/routes/a.gpx", "geojson_url": "/v1/routes/a.geojson"},
        "errors": [],
        **extra,
    }


class FakePlanner:
    def __init__(self, respond: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> None:
        self.respond = respond or (lambda request: ready_plan())
        self.requests: list[dict[str, Any]] = []
        self.texts: list[tuple[str, str | None]] = []
        self.text_response: dict[str, Any] | Exception | None = None

    async def plan(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        result = self.respond(request)
        if isinstance(result, Exception):
            raise result
        return result

    async def plan_text(self, text: str, timezone: str | None) -> dict[str, Any]:
        self.texts.append((text, timezone))
        if isinstance(self.text_response, Exception):
            raise self.text_response
        return self.text_response or ready_plan()


class FakeSights:
    def __init__(self, found: list[dict[str, Any]] | None = None) -> None:
        self.found = found if found is not None else []
        self.lines: list[list[list[float]]] = []

    async def along(self, line: list[list[float]], limit: int) -> list[dict[str, Any]]:
        self.lines.append(line)
        return self.found


def chat(planner: FakePlanner | None = None, **kw: Any) -> tuple[ChatService, FakePlanner]:
    planner = planner or FakePlanner()
    return ChatService(planner, **kw), planner


class Talk:
    """One conversation: remembers the session id."""

    def __init__(self, service: ChatService, timezone: str | None = None) -> None:
        self.service, self.sid, self.timezone = service, None, timezone

    async def say(self, text: str):
        reply = await self.service.send(text, session_id=self.sid, timezone=self.timezone)
        self.sid = reply.session_id
        return reply


# ---------------------------------------------------------------------- one-line requests


async def test_a_one_line_request_is_planned_and_answered_with_the_facts():
    service, planner = chat()
    reply = await Talk(service).say("from Braunschweig to Goslar by gravel bike via Wolfenbüttel")
    assert planner.requests == [
        {
            "origin": "Braunschweig",
            "via": ["Wolfenbüttel"],
            "constraints": {"bike_type": "gravel"},
            "destination": "Goslar",
        }
    ]
    assert reply.intent == "plan" and reply.plan is not None and reply.plan["status"] == "ready"
    assert (
        "Here is your route: 30.1 km, 1 h 15 min, 161 m up, gravel (brouter) profile."
        in reply.reply
    )
    assert "2 alternatives" in reply.reply
    assert reply.suggestions[0] == "Alternatives" and "New route" in reply.suggestions


async def test_a_loop_with_a_bike_and_sight_stops_becomes_the_matching_request():
    service, planner = chat()
    await Talk(service).say("40 km loop from 52.26,10.52 on a road bike")
    assert planner.requests[0] == {
        "origin": {"lon": 10.52, "lat": 52.26},
        "via": [],
        "constraints": {"bike_type": "road", "return_to_origin": True, "target_distance_km": 40.0},
    }
    await Talk(service).say("Füssen to Oberammergau past 2 castles")
    assert planner.requests[1]["poi_stops"] == {"count": 2}


async def test_the_chat_reports_what_the_sight_stops_did():
    stops = [{"name": "Burg Hohenschwangau", "category": "historic", "fame": 30}]
    service, _ = chat(FakePlanner(lambda r: ready_plan(poi_stops=stops, poi_stops_status="ok")))
    reply = await Talk(service).say("Füssen to Oberammergau past 1 castle")
    assert "It passes Burg Hohenschwangau." in reply.reply
    service, _ = chat(
        FakePlanner(lambda r: ready_plan(poi_stops=[], poi_stops_status="none_found"))
    )
    reply = await Talk(service).say("A to B past 2 castles")
    assert "no well-known sights" in reply.reply


async def test_a_request_the_api_would_reject_is_explained_not_crashed():
    def reject(request: dict[str, Any]) -> Any:
        return PlanError("poi_stops is not supported with return_to_origin")

    service, _ = chat(FakePlanner(reject))
    reply = await Talk(service).say("30 km loop from Goslar")
    assert "That did not work: poi_stops is not supported" in reply.reply
    assert reply.plan is None and "New route" in reply.suggestions


# ------------------------------------------------------------------------ guided dialogue


async def test_the_guided_dialogue_asks_step_by_step_with_quick_answers():
    service, planner = chat()
    t = Talk(service)
    r = await t.say("plan a route")
    assert (
        "Where do you want to start?" in r.reply and r.awaiting == "guided" and r.intent == "guided"
    )
    r = await t.say("Goslar")
    assert "loop back" in r.reply and r.suggestions == ["A loop", "To a destination"]
    r = await t.say("A loop")
    assert "How long" in r.reply and "40 km" in r.suggestions
    r = await t.say("nonsense")
    assert "between 1 and 1000" in r.reply  # asked again, not guessed
    r = await t.say("40 km")
    assert "Which bike?" in r.reply and "Skip" in r.suggestions
    r = await t.say("mountain bike")
    assert planner.requests == [
        {
            "origin": "Goslar",
            "via": [],
            "constraints": {
                "bike_type": "mountain",
                "return_to_origin": True,
                "target_distance_km": 40.0,
            },
        }
    ]
    assert r.plan is not None and r.awaiting is None


async def test_the_guided_dialogue_to_a_destination_and_the_default_bike():
    service, planner = chat()
    t = Talk(service)
    await t.say("hello")
    await t.say("52.26, 10.52")
    await t.say("To a destination")
    await t.say("Goslar")
    r = await t.say("skip")
    assert planner.requests[0] == {
        "origin": {"lon": 10.52, "lat": 52.26},
        "via": [],
        "constraints": {},
        "destination": "Goslar",
    }
    assert r.plan is not None


async def test_a_short_message_without_a_question_is_taken_as_the_start_place():
    service, _ = chat()
    r = await Talk(service).say("Goslar")
    assert "loop back" in r.reply  # origin taken, next question asked


async def test_a_complete_request_in_the_middle_of_the_dialogue_wins():
    service, planner = chat()
    t = Talk(service)
    await t.say("plan a route")
    r = await t.say("Bremen to Hamburg")
    assert planner.requests[0]["destination"] == "Hamburg" and r.plan is not None


async def test_bad_answers_to_the_mode_and_bike_questions_are_asked_again():
    service, _ = chat()
    t = Talk(service)
    await t.say("Goslar")
    r = await t.say("purple")
    assert "choose: a loop" in r.reply
    await t.say("a loop")
    await t.say("30")
    r = await t.say("unicycle")
    assert "I know road, gravel" in r.reply


# ------------------------------------------------------------------------- changing a route


async def test_a_change_replans_the_last_request_and_says_what_changed():
    service, planner = chat()
    t = Talk(service)
    await t.say("40 km loop from Goslar")
    r = await t.say("make it shorter")
    assert planner.requests[1]["constraints"]["target_distance_km"] == 30.0
    assert r.reply.startswith("Okay: about 25 % shorter (30 km).") and r.plan is not None
    r = await t.say("avoid ferries and use a road bike")
    last = planner.requests[2]
    assert (
        last["constraints"]["avoid_ferries"] is True and last["constraints"]["bike_type"] == "road"
    )
    assert last["constraints"]["target_distance_km"] == 30.0  # earlier changes are kept


async def test_shorter_without_a_distance_caps_the_distance_of_the_current_route():
    service, planner = chat()
    t = Talk(service)
    await t.say("Braunschweig to Goslar")
    r = await t.say("make it shorter")
    assert planner.requests[1]["constraints"]["max_distance_km"] == 22.6  # 75 % of 30.1 km
    assert "at most 22.6 km" in r.reply


async def test_flatter_uses_the_climb_of_the_last_route():
    service, planner = chat()
    t = Talk(service)
    await t.say("Braunschweig to Goslar")
    await t.say("flatter please")
    assert planner.requests[1]["constraints"]["max_ascent_m"] == 113.0  # 70 % of 161 m


async def test_a_change_that_cannot_be_applied_is_explained_and_nothing_is_replanned():
    service, planner = chat()
    t = Talk(service)
    await t.say("Braunschweig to Goslar")
    r = await t.say("longer please")  # a longer A-to-B route needs a distance to aim for
    assert len(planner.requests) == 1 and "needs one" in r.reply
    r = await t.say("make it so")
    assert len(planner.requests) == 1 and "Try 'shorter'" in r.reply


async def test_stops_and_vias_can_be_added_later():
    service, planner = chat()
    t = Talk(service)
    await t.say("Braunschweig to Goslar")
    await t.say("add a stop at Wolfenbüttel")
    assert planner.requests[1]["via"] == ["Wolfenbüttel"]
    await t.say("route past 2 castles")
    assert planner.requests[2]["poi_stops"] == {"count": 2}
    assert planner.requests[2]["via"] == ["Wolfenbüttel"]


async def test_a_failed_change_keeps_the_last_good_route_for_follow_ups():
    def respond(request: dict[str, Any]) -> dict[str, Any]:
        return (
            {"status": "no_route", "errors": []}
            if "via" in request and request["via"]
            else ready_plan()
        )

    service, planner = chat(FakePlanner(respond))
    t = Talk(service)
    await t.say("Braunschweig to Goslar")
    r = await t.say("add a stop at Nowhere")
    assert "could not find a route" in r.reply and r.plan is None
    r = await t.say("how long is it?")  # still about the first route
    assert "30.1 km" in r.reply


# ---------------------------------------------------------------- alternatives, questions


async def test_alternatives_are_listed_with_pros_and_cons_and_one_can_be_picked():
    service, _ = chat()
    t = Talk(service)
    await t.say("Braunschweig to Goslar")
    r = await t.say("show me the alternatives")
    assert "1. gravel (brouter)" in r.reply and "+ pro 2" in r.reply and "- con 1" in r.reply
    assert r.plan is not None and r.focus_rank is None
    assert r.suggestions[:2] == ["Show alternative 1", "Show alternative 2"]
    r = await t.say("the mtb version")
    assert r.focus_rank == 2 and "mountain bike (brouter) route" in r.reply and r.plan is not None
    r = await t.say("show the purple one")
    assert "could not tell which one" in r.reply and r.focus_rank is None


async def test_questions_are_answered_from_the_plan_without_replanning():
    weather = {
        "summary": {
            "temperature_min_c": 4.0,
            "temperature_max_c": 11.0,
            "headwind_mean_kmh": 7.0,
            "precipitation_probability_max": 60.0,
        }
    }
    plan = ready_plan()
    plan["route"]["weather"] = weather
    service, planner = chat(FakePlanner(lambda r: plan))
    t = Talk(service)
    await t.say("Braunschweig to Goslar")
    r = await t.say("what's the weather?")
    assert (
        "4 to 11 °C" in r.reply
        and "7 km/h headwind" in r.reply
        and "rain chance up to 60 %" in r.reply
    )
    assert "Weather: 4 to 11" in (await t.say("Braunschweig to Goslar")).reply
    r = await t.say("how steep is it?")
    assert "climbs 161 m and descends 150 m" in r.reply
    r = await t.say("what surface is it?")
    assert "60 % paved" in r.reply
    r = await t.say("why this route?")
    assert "It is the best fit." in r.reply and "+ pro 1" in r.reply
    assert len(planner.requests) == 2  # the two plans only


async def test_no_forecast_is_said_not_made_up():
    service, _ = chat()
    t = Talk(service)
    await t.say("Braunschweig to Goslar")
    assert "no forecast" in (await t.say("will it rain?")).reply


async def test_sights_along_the_route_are_listed_and_a_stop_can_be_offered():
    sights = FakeSights(
        [
            {
                "name": "Rammelsberg",
                "category": "museum",
                "fame": 36,
                "distance_from_route_m": 1213.0,
            },
            {"category": "viewpoint", "fame": None, "distance_from_route_m": None},
        ]
    )
    service, _ = chat(sights=sights)
    t = Talk(service)
    r = await t.say("Braunschweig to Goslar")
    assert "What sights are along the way?" in r.suggestions
    r = await t.say("what sights are along the way?")
    assert "- Rammelsberg (described in 36 languages), 1210 m off the route" in r.reply
    assert "- viewpoint" in r.reply and r.suggestions == ["Route past 2 sights"]
    assert sights.lines[0][0] == [10.5, 52.2]
    # For a loop no stop is offered (the API does not allow it).
    await t.say("30 km loop from Goslar")
    assert (await t.say("any castles near the route")).suggestions == []


async def test_sights_are_reported_when_none_or_unavailable():
    service, _ = chat(sights=FakeSights([]))
    t = Talk(service)
    await t.say("A to B")
    assert "no well-known sights" in (await t.say("what sights are there?")).reply
    service, _ = chat(sights=None)
    t = Talk(service)
    r = await t.say("A to B")
    assert "What sights are along the way?" not in r.suggestions
    assert "not available" in (await t.say("what sights are there?")).reply


async def test_the_files_of_the_route_are_offered():
    service, _ = chat()
    t = Talk(service)
    assert (await t.say("send me the gpx")).plan is None  # nothing planned yet: no files
    await t.say("A to B")
    r = await t.say("send me the GPX")
    assert "GPX: /v1/routes/a.gpx" in r.reply and "GEOJSON: /v1/routes/a.geojson" in r.reply


# ---------------------------------------------------------------------------- clarification

SPRINGFIELD = {
    "status": "awaiting_clarification",
    "clarification": [
        {
            "field": "Springfield",
            "candidates": [
                {"label": "Springfield, Illinois", "coordinate": {"lon": -89.6, "lat": 39.8}},
                {"label": "Springfield, Massachusetts", "coordinate": {"lon": -72.6, "lat": 42.1}},
            ],
        }
    ],
    "errors": [],
}


def ambiguous_once(request: dict[str, Any]) -> dict[str, Any]:
    return SPRINGFIELD if request.get("origin") == "Springfield" else ready_plan()


async def test_an_ambiguous_place_pauses_the_chat_until_the_person_chooses():
    service, planner = chat(FakePlanner(ambiguous_once))
    t = Talk(service)
    r = await t.say("Springfield to Boston")
    assert r.awaiting == "place_choice" and r.plan is None
    assert (
        'Which "Springfield" do you mean?' in r.reply and "2. Springfield, Massachusetts" in r.reply
    )
    assert r.suggestions == ["1", "2"]
    r = await t.say("2")
    assert planner.requests[1]["origin"] == {"lon": -72.6, "lat": 42.1}
    assert planner.requests[1]["destination"] == "Boston"
    assert r.plan is not None and r.awaiting is None and "Here is your route" in r.reply


@pytest.mark.parametrize("answer", ["1", "the first one", "illinois", "No. 1"])
async def test_the_choice_can_be_a_number_an_ordinal_or_part_of_the_name(answer):
    service, planner = chat(FakePlanner(ambiguous_once))
    t = Talk(service)
    await t.say("Springfield to Boston")
    await t.say(answer)
    assert planner.requests[1]["origin"] == {"lon": -89.6, "lat": 39.8}


async def test_an_unclear_answer_asks_again_and_a_new_request_ends_the_question():
    service, planner = chat(FakePlanner(ambiguous_once))
    t = Talk(service)
    await t.say("Springfield to Boston")
    r = await t.say("hmm")
    assert r.awaiting == "place_choice" and "2. Springfield, Massachusetts" in r.reply
    assert len(planner.requests) == 1
    r = await t.say("Bremen to Hamburg")  # a whole new request instead of an answer
    assert planner.requests[1]["destination"] == "Hamburg" and r.plan is not None
    # ... and the question is gone: the next message is an ordinary one.
    assert (await t.say("how long is it?")).awaiting is None


async def test_new_route_ends_the_question():
    service, _ = chat(FakePlanner(ambiguous_once))
    t = Talk(service)
    await t.say("Springfield to Boston")
    r = await t.say("new route")
    assert "starting over" in r.reply and r.awaiting == "guided"


async def test_a_place_that_was_not_found_at_all_is_reported_with_advice():
    nothing = {
        "status": "awaiting_clarification",
        "clarification": [{"field": "Qwxzy", "candidates": [], "hint": "no matches found"}],
        "errors": [],
    }
    service, _ = chat(FakePlanner(lambda r: nothing))
    r = await Talk(service).say("Qwxzy to Goslar")
    assert "could not find 'Qwxzy'" in r.reply and r.awaiting is None


# ------------------------------------------------------------------------- free text (LLM)


async def test_free_text_goes_to_the_parser_and_its_request_is_kept_for_changes():
    service, planner = chat(text_enabled=True)
    planner.text_response = ready_plan(
        interpretation={
            "request": {
                "origin": "Goslar",
                "destination": None,
                "via": [],
                "constraints": {"return_to_origin": True, "target_distance_km": 50.0},
            },
            "departure_time": "2026-10-10T08:00:00+02:00",
            "notes": ["wants a scenic route, which cannot be requested"],
        }
    )
    t = Talk(service, timezone="Europe/Berlin")
    r = await t.say("a relaxed 50 km ride around Goslar tomorrow morning")
    assert planner.texts == [
        ("a relaxed 50 km ride around Goslar tomorrow morning", "Europe/Berlin")
    ]
    assert r.intent == "plan_text" and "(I noted: wants a scenic route" in r.reply
    await t.say("make it shorter")
    assert planner.requests[0]["constraints"]["target_distance_km"] == 37.5
    assert planner.requests[0]["departure_time"] == "2026-10-10T08:00:00+02:00"


async def test_without_a_language_model_unknown_text_starts_the_dialogue_instead():
    service, planner = chat(text_enabled=False)
    r = await Talk(service).say("I would love a nice relaxing ride this weekend")
    assert r.intent == "guided" and planner.texts == [] and "Where do you want to start?" in r.reply


async def test_a_parser_that_is_not_configured_or_refuses_is_explained():
    service, planner = chat(text_enabled=True)
    planner.text_response = TextUnavailableError()
    r = await Talk(service).say("a relaxing weekend ride around Goslar")
    assert "not configured" in r.reply and "Help" in r.suggestions and r.failed is True
    planner.text_response = PlanError("the language model could not read that")
    r = await Talk(service).say("something very strange here")
    assert "could not read that" in r.reply and r.failed is True


# ---------------------------------------------------------------------------- failures


@pytest.mark.parametrize(
    ("plan", "fragment"),
    [
        ({"status": "no_route", "errors": []}, "could not find a route"),
        ({"status": "provider_failure", "errors": [{"message": "ORS timed out"}]}, "ORS timed out"),
        ({"status": "invalid", "errors": []}, "not valid"),
        (
            {"status": "provider_failure", "errors": [{"code": "routing_area_not_covered"}]},
            "no map data",
        ),
    ],
)
async def test_failed_plans_are_explained(plan, fragment):
    service, _ = chat(FakePlanner(lambda r: plan))
    r = await Talk(service).say("A to B")
    assert fragment in r.reply and r.plan is None and "New route" in r.suggestions
    if "map data" in fragment:
        assert "Use openrouteservice" in r.suggestions


async def test_a_coverage_gap_next_to_a_route_from_another_engine_is_mentioned():
    plan = ready_plan(errors=[{"code": "routing_area_not_covered"}])
    service, _ = chat(FakePlanner(lambda r: plan))
    assert "BRouter has no map data" in (await Talk(service).say("A to B")).reply


async def test_an_unexpected_error_in_a_turn_does_not_break_the_conversation():
    def boom(request: dict[str, Any]) -> Any:
        raise RuntimeError("database exploded")

    service, planner = chat(FakePlanner(boom))
    t = Talk(service)
    r = await t.say("A to B")
    assert r.reply == FAILURE_REPLY
    planner.respond = lambda request: ready_plan()
    assert (await t.say("A to B")).plan is not None  # the session still works


# ------------------------------------------------------------------- reset, help, sessions


async def test_help_and_reset():
    service, _ = chat()
    t = Talk(service)
    r = await t.say("help")
    assert "one line:" in r.reply and r.intent == "help"
    await t.say("A to B")
    r = await t.say("new route")
    assert "starting over" in r.reply and r.awaiting == "guided"
    r = await t.say("show alternatives")  # the old route is gone; the answer is the origin
    assert "loop back" in r.reply


async def test_a_follow_up_gets_through_an_open_dialogue_question_when_a_route_exists():
    service, planner = chat()
    t = Talk(service)
    await t.say("Braunschweig to Goslar")
    await t.say("plan a route")  # a new dialogue starts ... and asks for the start
    r = await t.say("alternatives")  # ... but the person is still looking at the first route
    assert r.intent == "alternatives" and r.awaiting is None and "Alternatives" in r.reply
    r = await t.say("the fastest")
    assert r.intent == "alternatives" and r.focus_rank is not None and r.plan is not None
    assert (await t.say("how steep is it?")).intent == "explain"  # the question is gone


@pytest.mark.parametrize(
    "text", ["the fastest", "fastest", "show me the shortest", "the flattest one"]
)
async def test_a_superlative_picks_an_alternative(text):
    service, planner = chat()
    t = Talk(service)
    await t.say("Braunschweig to Goslar")
    r = await t.say(text)
    assert r.intent == "alternatives" and r.focus_rank in (1, 2) and len(planner.requests) == 1


async def test_follow_ups_without_a_route_point_at_the_help():
    service, _ = chat()
    for text in ("make it shorter", "what's the weather?"):
        r = await Talk(service).say(text)
        assert r.plan is None and r.intent == "guided"


@pytest.mark.parametrize("text", ["how steep is it?", "what's the weather?", "make it shorter"])
async def test_a_question_before_any_route_is_not_taken_as_the_start_place(text):
    service, planner = chat()
    t = Talk(service)
    r = await t.say(text)
    assert r.intent == "guided" and r.awaiting == "guided" and planner.requests == []
    assert "Where do you want to start" in r.reply  # the dialogue starts from the beginning
    # ... and a bare place name is still the start place.
    assert "a loop back to the start" in (await t.say("Goslar")).reply


async def test_a_pending_place_choice_does_not_replace_the_request_of_the_route_on_screen():
    def respond(request):
        return SPRINGFIELD if request.get("origin") == "Springfield" else ready_plan()

    service, planner = chat(FakePlanner(respond))
    t = Talk(service)
    await t.say("Bremen to Hamburg by gravel")
    await t.say("Springfield to Boston")  # asks which Springfield
    await t.say("new route")  # ... and is abandoned
    await t.say("Bremen to Hamburg by gravel")
    await t.say("Qwxzy to Goslar")  # asks again; then the question is dropped
    await t.say("Bremen to Hamburg by gravel")
    r = await t.say("make it shorter")
    assert planner.requests[-1]["origin"] == "Bremen" and r.plan is not None


@pytest.mark.parametrize("abandon", ["help", "Qwxzy to Goslar"])
async def test_a_follow_up_after_an_unresolved_place_changes_the_route_on_screen(abandon):
    nothing = {
        "status": "awaiting_clarification",
        "clarification": [{"field": "Qwxzy", "candidates": []}],
        "errors": [],
    }

    def respond(request):
        if request.get("origin") == "Springfield":
            return SPRINGFIELD
        return nothing if request.get("origin") == "Qwxzy" else ready_plan()

    service, planner = chat(FakePlanner(respond))
    t = Talk(service)
    await t.say("Bremen to Hamburg")
    await t.say("Springfield to Boston")  # a question is open ...
    await t.say(abandon)  # ... and ends without an answer (help; or a place that is not found)
    r = await t.say("avoid ferries")
    assert r.plan is not None
    changed = planner.requests[-1]
    assert changed["origin"] == "Bremen" and changed["constraints"]["avoid_ferries"] is True


async def test_failed_turns_are_flagged_and_questions_back_are_not():
    nothing = {
        "status": "awaiting_clarification",
        "clarification": [{"field": "Qwxzy", "candidates": []}],
        "errors": [],
    }
    service, _ = chat(FakePlanner(lambda r: nothing if r["origin"] == "Qwxzy" else ready_plan()))
    t = Talk(service)
    assert (await t.say("A to B")).failed is False
    assert (await t.say("Qwxzy to Goslar")).failed is True  # not found at all
    broken, _ = chat(FakePlanner(lambda r: PlanError("boom")))
    assert (await Talk(broken).say("A to B")).failed is True
    ambiguous, _ = chat(FakePlanner(ambiguous_once))
    assert (await Talk(ambiguous).say("Springfield to Boston")).failed is False  # a question
    assert (await t.say("how long is it?")).failed is False  # the flag does not stick


async def test_only_the_latest_checkpoint_of_a_conversation_is_kept():
    service, _ = chat(FakePlanner(ambiguous_once))
    t = Talk(service)
    for text in ("A to B", "make it shorter", "alternatives", "Springfield to Boston"):
        await t.say(text)  # the last one leaves a question open (a paused graph)
    storage = service._checkpointer.storage[t.sid]
    assert all(len(checkpoints) == 1 for checkpoints in storage.values())
    assert {key[0] for key in service._checkpointer.blobs} == {t.sid}
    assert len(service._checkpointer.blobs) <= 40  # one per channel, not one per step
    # The paused question can still be answered after pruning.
    assert (await t.say("2")).plan is not None


async def test_sessions_are_independent_and_unknown_ids_start_fresh():
    service, planner = chat()
    a, b = Talk(service), Talk(service)
    await a.say("40 km loop from Goslar")
    await b.say("Bremen to Hamburg")
    await a.say("make it shorter")
    assert planner.requests[2]["origin"] == "Goslar"  # a's request, not b's
    reply = await service.send("hello", session_id="f" * 32)
    assert reply.session_id != "f" * 32 and service.sessions == 3


async def test_the_least_recently_used_sessions_are_dropped():
    service, planner = chat(max_sessions=2)
    a, b, c = Talk(service), Talk(service), Talk(service)
    await a.say("40 km loop from Goslar")
    await b.say("Bremen to Hamburg")
    await c.say("Oxford to Cambridge")
    assert service.sessions == 2
    reply = await service.send("make it shorter", session_id=a.sid)
    assert reply.session_id != a.sid and reply.plan is None  # a was forgotten


async def test_long_conversations_are_trimmed_and_messages_clipped():
    service, planner = chat()
    t = Talk(service)
    for _ in range(30):
        await t.say("A to B")
    snapshot = await service._graph.aget_state({"configurable": {"thread_id": t.sid}})
    assert len(snapshot.values["messages"]) <= 40
    await t.say("A to B " + "x" * 2000)
    snapshot = await service._graph.aget_state({"configurable": {"thread_id": t.sid}})
    assert len(snapshot.values["user_text"]) <= 500


async def test_use_openrouteservice_retries_the_request_that_just_failed():
    gap = {"status": "provider_failure", "errors": [{"code": "routing_area_not_covered"}]}

    def respond(request: dict[str, Any]) -> dict[str, Any]:
        return (
            ready_plan()
            if request.get("routing_engines")
            else (gap if request["origin"] == "Neustadt" else ready_plan())
        )

    service, planner = chat(FakePlanner(respond))
    t = Talk(service)
    await t.say("Oxford to Cambridge")  # an earlier, good route
    r = await t.say("Neustadt to Goslar")
    assert "no map data" in r.reply and "Use openrouteservice" in r.suggestions
    r = await t.say("Use openrouteservice")
    retried = planner.requests[-1]
    assert retried["origin"] == "Neustadt" and retried["routing_engines"] == ["ors"]  # not Oxford
    assert r.plan is not None and "routing engine: ors" in r.reply
    # A successful plan clears the failure: the next change applies to the new route.
    await t.say("avoid ferries")
    assert planner.requests[-1]["origin"] == "Neustadt"


async def test_naming_an_engine_without_any_request_starts_the_dialogue():
    service, planner = chat()
    r = await Talk(service).say("use openrouteservice")
    assert r.intent == "guided" and planner.requests == []


# ------------------------------------------------- reading, limits, retries, departures


async def test_the_reply_says_how_the_line_was_read_so_a_misreading_is_visible():
    service, planner = chat()
    r = await Talk(service, "Europe/Berlin").say(
        "Goslar to Hannover by road bike via Hildesheim, max 80 km, no ferries, past 2 castles, "
        "along the Rhine"
    )
    head = r.reply.split(" Here is your route")[0]
    assert head.startswith("Planning Goslar to Hannover via Hildesheim (road bike, at most 80 km")
    assert "no ferries" in head and "past 2 well-known sights" in head
    assert "Not applied: 'the Rhine' -- I cannot route along a feature" in head or (
        "Rhine" in head and "Not applied" in head
    )


async def test_a_departure_in_the_line_reaches_the_request_in_the_users_zone():
    service, planner = chat()
    await Talk(service, "Europe/Berlin").say("Goslar to Hannover tomorrow at 8")
    sent = planner.requests[0]["departure_time"]
    assert sent.endswith(("+01:00", "+02:00")) and "T08:00:00" in sent


async def test_the_limits_that_were_missed_are_said():
    def respond(request):
        plan = ready_plan()
        plan["route"]["metrics"].update(ascent_m=335.0, distance_m=50_100.0)
        return plan

    service, _ = chat(FakePlanner(respond))
    t = Talk(service)
    r = await t.say("Goslar to Hannover max 200 m climb, max 40 km")
    assert "still climbs 335 m, more than the 200 m" in r.reply
    assert "50.1 km, longer than the 40 km" in r.reply
    ok, _ = chat()
    assert "Note:" not in (await Talk(ok).say("Goslar to Hannover max 400 m climb")).reply


async def test_a_loop_far_from_the_asked_length_is_said():
    service, _ = chat()  # the fake route is about 30 km
    r = await Talk(service).say("60 km loop from Goslar")
    assert "not the 60 km asked for" in r.reply
    assert "Note" not in (await Talk(service).say("30 km loop from Goslar")).reply


async def test_a_service_that_timed_out_is_retried_with_try_again():
    busy = {
        "status": "provider_failure",
        "errors": [{"code": "provider_timeout", "message": "geocoder request timed out"}],
    }
    answers = iter([busy])
    service, planner = chat(FakePlanner(lambda request: next(answers, None) or ready_plan()))
    t = Talk(service)
    r = await t.say("Goslar to Hannover by gravel")
    assert "did not answer in time" in r.reply and "Try again" in r.suggestions and r.failed
    r = await t.say("Try again")
    assert r.plan is not None and r.reply.startswith("Trying again. ")
    assert planner.requests[0] == planner.requests[1]  # exactly the request that failed


async def test_try_again_without_a_failure_is_not_a_command():
    service, planner = chat()
    r = await Talk(service).say("try again")
    assert r.plan is None and planner.requests == []


async def test_a_duration_loop_says_which_speed_it_assumed():
    service, planner = chat()
    r = await Talk(service).say("2 hour loop from Goslar on my road bike")
    assert planner.requests[0]["constraints"]["target_distance_km"] == 50.0
    assert "2 h taken as 50 km at 25 km/h (my assumption)" in r.reply


async def test_a_partial_line_keeps_what_it_said_in_the_dialogue():
    service, planner = chat()
    t = Talk(service, "Europe/Berlin")
    r = await t.say("gravel loop tomorrow at 9, no ferries, past 2 sights")
    assert "Where do you want to start?" in r.reply  # no start yet; the rest is remembered
    r = await t.say("Goslar")
    assert "How long should the loop be?" in r.reply
    done = await t.say("35")
    assert done.reply.startswith("Planning a 35 km loop from Goslar (gravel bike, no ferries")
    assert "past 2 well-known sights" in done.reply and "leaving" in done.reply
    sent = planner.requests[0]
    assert sent["origin"] == "Goslar" and sent["constraints"]["target_distance_km"] == 35.0
    assert sent["constraints"]["bike_type"] == "gravel" and sent["constraints"]["avoid_ferries"]
    assert sent["poi_stops"] == {"count": 2} and "T09:00:00" in sent["departure_time"]


def _candidates(*offsets_km: float) -> dict[str, Any]:
    """An ambiguous Ilsenburg: candidates this far (km, east) from the first."""
    items = [
        {"label": f"Ilsenburg {i}", "coordinate": {"lon": 10.67 + km / 68.0, "lat": 51.87}}
        for i, km in enumerate(offsets_km, start=1)
    ]
    return {
        "status": "awaiting_clarification",
        "clarification": [{"field": "Ilsenburg", "candidates": items}],
        "errors": [],
    }


async def test_one_town_listed_several_times_is_not_asked_about():
    def respond(request):
        return _candidates(0, 0.3, 1.1) if request.get("origin") == "Ilsenburg" else ready_plan()

    service, planner = chat(FakePlanner(respond))
    r = await Talk(service).say("Ilsenburg to Goslar")
    assert r.awaiting is None and r.plan is not None  # no question
    first = _candidates(0)["clarification"][0]["candidates"][0]["coordinate"]
    assert planner.requests[1]["origin"] == {"lon": first["lon"], "lat": first["lat"]}


async def test_a_county_named_after_the_town_is_not_a_second_town():
    two = {
        "status": "awaiting_clarification",
        "clarification": [
            {
                "field": "Wolfenbüttel",
                "candidates": [
                    {
                        "label": "Wolfenbüttel, Niedersachsen",
                        "coordinate": {"lon": 10.53, "lat": 52.16},
                    },
                    {
                        "label": "Landkreis Wolfenbüttel, Niedersachsen",
                        "coordinate": {"lon": 10.6, "lat": 52.1},
                    },
                ],
            }
        ],
        "errors": [],
    }
    service, planner = chat(
        FakePlanner(lambda r: two if r["origin"] == "Wolfenbüttel" else ready_plan())
    )
    r = await Talk(service).say("Wolfenbüttel to Goslar")
    assert r.awaiting is None and planner.requests[1]["origin"] == {"lon": 10.53, "lat": 52.16}


async def test_places_far_apart_are_still_asked_about():
    def respond(request):
        return _candidates(0, 40) if request.get("origin") == "Ilsenburg" else ready_plan()

    service, _ = chat(FakePlanner(respond))
    r = await Talk(service).say("Ilsenburg to Goslar")
    assert r.awaiting == "place_choice" and "Which" in r.reply


@pytest.mark.parametrize(
    "text", ["leave tomorrow at 8", "tomorrow at 8 instead", "start at 7pm", "on Saturday morning"]
)
async def test_the_departure_can_be_changed_after_the_route(text):
    service, planner = chat()
    t = Talk(service, "Europe/Berlin")
    await t.say("Braunschweig to Goslar")
    r = await t.say(text)
    assert r.intent == "refine" and r.plan is not None and r.reply.startswith("Okay: leaving ")
    assert "departure_time" in planner.requests[1]
    assert planner.requests[1]["origin"] == "Braunschweig"  # the rest of the request is kept
