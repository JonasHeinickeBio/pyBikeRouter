"""What the web form holds is what the chat's "make it shorter" refers to."""

from __future__ import annotations

from typing import Any

from .test_chat_graph import FakePlanner, Talk, chat, ready_plan

FORM_LOOP = {
    "origin": "Goslar",
    "via": [],
    "constraints": {"target_distance_km": 40.0, "return_to_origin": True, "bike_type": "gravel"},
}


async def say(service, text: str, sid: str | None = None, **context: Any):
    return await service.send(text, session_id=sid, form_context=context or None)


async def test_a_route_made_in_the_form_can_be_changed_by_chat_in_a_new_conversation():
    service, planner = chat()
    reply = await say(service, "make it shorter", request=FORM_LOOP, plan=ready_plan())
    # the chat refined the FORM's request, not "no route to change yet"
    assert planner.requests[0]["origin"] == "Goslar"
    assert planner.requests[0]["constraints"]["target_distance_km"] == 30.0
    assert (
        planner.requests[0]["constraints"]["bike_type"] == "gravel"
    )  # the form's other choices kept
    assert reply.plan is not None and reply.reply.startswith("Okay: about 25 % shorter")


async def test_the_form_edited_after_a_chat_route_is_what_gets_changed():
    service, planner = chat()
    t = Talk(service)
    await t.say("from Braunschweig to Goslar by gravel")
    edited = {
        "origin": "Hannover",
        "destination": "Celle",
        "via": [],
        "constraints": {"bike_type": "road"},
    }
    await say(service, "avoid ferries", t.sid, request=edited)
    again = planner.requests[1]
    assert (again["origin"], again["destination"]) == ("Hannover", "Celle")
    assert again["constraints"]["bike_type"] == "road" and again["constraints"]["avoid_ferries"]


async def test_the_chats_own_route_stays_on_screen_when_only_the_request_is_sent():
    service, planner = chat()
    t = Talk(service)
    await t.say("from Braunschweig to Goslar")
    await say(service, "alternatives", t.sid, request={"origin": "A", "destination": "B"})
    assert len(planner.requests) == 1  # "alternatives" shows the ones already there; no new plan
    # ... and with a plan from the form, the form's route is the one on screen
    form_plan = ready_plan(explanation="from the form")
    reply = await say(service, "the mtb version", t.sid, plan=form_plan)
    assert reply.focus_rank == 2 and reply.plan["explanation"] == "from the form"


async def test_the_context_replaces_a_request_that_failed():
    planner = FakePlanner(lambda request: {"status": "no_route", "errors": []})
    service, _ = chat(planner)
    t = Talk(service)
    await t.say("from A to B")
    planner.respond = lambda request: ready_plan()
    await say(service, "on a road bike", t.sid, request=FORM_LOOP, plan=ready_plan())
    assert planner.requests[-1]["origin"] == "Goslar"  # not a retry of the failed A -> B


async def test_without_a_context_nothing_changes():
    service, planner = chat()
    reply = await service.send("make it shorter")
    # no route anywhere: the chat starts its dialogue, it does not invent one
    assert planner.requests == [] and "where do you want to start" in reply.reply.lower()


async def test_a_context_is_not_kept_in_the_conversation():
    service, planner = chat()
    reply = await say(service, "make it shorter", request=FORM_LOOP, plan=ready_plan())
    state = await service._graph.aget_state({"configurable": {"thread_id": reply.session_id}})
    assert state.values.get("form_context") is None
    # the next turn, without a context, goes on from the route the chat made
    await service.send("avoid ferries", session_id=reply.session_id)
    last = planner.requests[-1]
    assert last["origin"] == "Goslar" and last["constraints"]["target_distance_km"] == 30.0
