"""The chat as a LangGraph: one turn is a walk through the graph, the conversation is its state.

```
START -> ingest -> (router by intent)
   plan            -> plan_one_line ----\
   plan_text       -> plan_text ---------\
   guided          -> guided ------------>-> plan_route -> (clarify <-> plan_route) -> finish
   refine          -> refine ------------/
   alternatives / sights / explain / export / reset / help -------------------------> finish
```

Several ways to get a route share the planning step: a one-line request, free text for the LLM
parser, a step-by-step dialogue, or a change to the last route. An ambiguous place pauses the
graph (``interrupt``) until the person answers; the checkpointer keeps the state meanwhile, which
is also how a conversation continues over many requests (docs/chat.md).
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from bike_routing_agent.chat import describe, rules
from bike_routing_agent.chat.models import (
    ChatState,
    PlanError,
    Planner,
    Sights,
    SightsUnavailableError,
    TextUnavailableError,
)

MAX_KEPT_MESSAGES = 40
MAX_CLARIFICATION_ROUNDS = 3
ENGINE_REQUEST = re.compile(
    r"\b(?:use|try|with|using)\s+(?:ors|openrouteservice|brouter|valhalla)\b", re.I
)
GREETINGS = re.compile(
    r"^\s*(?:hi|hello|hey|hallo|moin|good (?:morning|evening)|yo)\b[\s!.]*$", re.I
)

HELP_TEXT = (
    "I can plan bike routes with you. Ask in any of these ways:\n"
    "- one line: 'from Braunschweig to Goslar by gravel bike', '40 km loop from Goslar', "
    "'Füssen to Oberammergau past 2 castles'\n"
    "- in your own words (when a language model is configured)\n"
    "- or just say 'plan a route' and I will ask step by step\n"
    "Then change it ('shorter', 'flatter', 'avoid ferries', 'on a road bike', 'add a stop at "
    "Bicester'), compare ('alternatives', 'the mtb one'), ask ('weather?', 'how steep?'), "
    "look for sights ('what sights are along the way?'), or get the files ('send me the GPX'). "
    "'New route' starts over."
)

BIKE_CHOICES = ["Gravel", "Road", "Touring", "Mountain", "City", "E-bike", "Skip"]


def _has_ready_plan(state: ChatState) -> bool:
    plan = state.get("last_plan")
    return bool(plan and plan.get("status") == "ready")


def decide_intent(text: str, state: ChatState, *, text_enabled: bool) -> str:
    """Which way of getting (or working with) a route this message is."""
    has_plan = _has_ready_plan(state)
    followup = rules.classify(text, has_plan=has_plan)
    if followup in ("reset", "help"):
        return followup
    line = rules.parse_one_line(text)
    if line.complete() and not _reads_as_followup(line, has_plan):
        return "plan"
    if state.get("asking"):
        return "guided"
    if ENGINE_REQUEST.search(text) and (has_plan or state.get("failed_request")):
        return "refine"
    if followup:
        return followup
    if text_enabled and len(text) >= 8:
        return "plan_text"
    return "guided"


def _reads_as_followup(line: rules.OneLine, has_plan: bool) -> bool:
    """ "make it shorter to 20 km" looks like "A to B" but is a change to the last route."""
    if not has_plan:
        return False
    parts = [p for p in (line.origin, line.destination) if p]
    return any(rules.classify(p, has_plan=True) for p in parts) or bool(
        line.destination and re.match(r"^\d", line.destination)
    )


def request_from_line(line: rules.OneLine) -> dict[str, Any]:
    constraints: dict[str, Any] = {}
    if line.bike_type:
        constraints["bike_type"] = line.bike_type
    request: dict[str, Any] = {
        "origin": rules.place_value(line.origin or ""),
        "via": [rules.place_value(v) for v in line.via],
        "constraints": constraints,
    }
    if line.loop:
        constraints["return_to_origin"] = True
        constraints["target_distance_km"] = line.distance_km
    else:
        request["destination"] = rules.place_value(line.destination or "")
    if line.sight_stops:
        request["poi_stops"] = {"count": line.sight_stops}
    return request


def _clean_slot(value: str) -> str:
    return value.strip().strip(".,;!?\"'")


def build_chat_graph(
    *,
    planner: Planner,
    sights: Sights | None = None,
    text_enabled: bool = False,
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    """The compiled chat graph. ``checkpointer`` keeps a conversation per ``thread_id``."""

    # ------------------------------------------------------------------ entry and routing

    async def ingest(state: ChatState) -> dict[str, Any]:
        text = (state.get("user_text") or "").strip()
        return {
            "messages": [HumanMessage(content=text)],
            "intent": decide_intent(text, state, text_enabled=text_enabled),
            "reply": "",
            "failed": False,
            "suggestions": [],
            "plan": None,
            "focus_rank": None,
            "pending_request": None,
            "change_note": "",
            "clarify_result": None,
        }

    def route_intent(state: ChatState) -> str:
        return {
            "plan": "plan_one_line",
            "plan_text": "plan_text",
            "guided": "guided",
            "refine": "refine",
            "alternatives": "alternatives",
            "sights": "sights",
            "explain": "explain",
            "export": "export",
            "reset": "reset",
            "help": "help",
        }.get(state.get("intent") or "", "help")

    # --------------------------------------------------------- ways of getting a request

    async def plan_one_line(state: ChatState) -> dict[str, Any]:
        line = rules.parse_one_line(state["user_text"])
        return {"pending_request": request_from_line(line), "asking": None, "draft": {}}

    async def plan_text(state: ChatState) -> dict[str, Any]:
        try:
            plan = await planner.plan_text(state["user_text"], state.get("timezone"))
        except TextUnavailableError:
            return {
                "reply": "Free-text planning is not configured here. " + HELP_TEXT,
                "suggestions": ["Plan a route", "Help"],
                "failed": True,
            }
        except PlanError as exc:
            return {"reply": str(exc), "suggestions": ["Help", "Plan a route"], "failed": True}
        interpretation = plan.get("interpretation") or {}
        request = interpretation.get("request") or {}
        if interpretation.get("departure_time"):
            request = {**request, "departure_time": interpretation["departure_time"]}
        update = _after_plan(state, plan, request, prefix="")
        notes = interpretation.get("notes") or []
        if notes and update.get("reply"):
            update["reply"] += " (I noted: " + "; ".join(notes[:3]) + ")"
        return update

    async def guided(state: ChatState) -> dict[str, Any]:
        draft = dict(state.get("draft") or {})
        raw = state["user_text"]
        text = _clean_slot(raw)
        asking = state.get("asking")
        if asking is None and text and not GREETINGS.match(text) and len(text.split()) <= 5:
            # Short bare text is taken as the start place -- unless it is a question or a
            # follow-up ("how steep is it?") that only makes sense with a route.
            if not re.search(r"\b(?:plan|route|ride|trip|cycle|bike)\b|\?", raw, re.I) and not (
                rules.classify(raw, has_plan=True)
            ):
                draft["origin"], asking = rules.place_value(text), "origin_done"
        if asking and asking != "origin_done":
            problem = _fill_slot(draft, asking, text)
            if problem:
                return {"draft": draft, "asking": asking, **_question(asking, problem)}
        slot = _next_slot(draft)
        if slot is None:
            return {"pending_request": _request_from_draft(draft), "asking": None, "draft": {}}
        return {"draft": draft, "asking": slot, **_question(slot)}

    async def refine(state: ChatState) -> dict[str, Any]:
        # A request that just failed is what "use openrouteservice" should retry.
        previous = state.get("failed_request") or state.get("last_request")
        if not previous:
            return {"reply": "There is no route to change yet. " + HELP_TEXT}
        route = (state.get("last_plan") or {}).get("route") or {}
        metrics = route.get("metrics") or {}
        change = rules.refine(
            state["user_text"],
            previous,
            last_ascent_m=metrics.get("ascent_m"),
            last_distance_m=metrics.get("distance_m"),
        )
        if change.unknown or change.empty():
            said = " ".join(change.notes) or "I did not catch what to change."
            return {
                "reply": said + " Try 'shorter', 'flatter', 'avoid ferries', 'on a road bike', "
                "'add a stop at ...' or 'past 2 castles'.",
                "suggestions": [
                    "Make it shorter",
                    "Avoid ferries",
                    "On a road bike",
                    "Alternatives",
                ],
            }
        return {
            "pending_request": rules.apply_refinement(previous, change),
            "change_note": "Okay: " + ", ".join(change.notes) + ". ",
        }

    # ------------------------------------------------------------------------- planning

    async def plan_route(state: ChatState) -> dict[str, Any]:
        request = state.get("pending_request") or {}
        try:
            plan = await planner.plan(request)
        except PlanError as exc:
            return {
                "reply": f"{state.get('change_note') or ''}That did not work: {exc}",
                "suggestions": ["New route", "Help"],
                "pending_request": None,
                "failed": True,
            }
        return _after_plan(state, plan, request, prefix=state.get("change_note") or "")

    def _after_plan(
        state: ChatState, plan: dict[str, Any], request: dict[str, Any], *, prefix: str
    ) -> dict[str, Any]:
        status = plan.get("status")
        base: dict[str, Any] = {
            "pending_request": None,
            "awaiting_request": None,
            "asking": None,
            "draft": {},
        }
        if status == "ready":
            geometry = ((plan.get("route") or {}).get("geometry_geojson")) or {}
            can_sights = sights is not None and bool(geometry.get("coordinates"))
            return {
                **base,
                "last_request": request,
                "last_plan": plan,
                "failed_request": None,
                "plan": plan,
                "clarification": [],
                "reply": prefix + describe.describe_plan(plan),
                "suggestions": describe.suggestions_after_plan(plan, sights=can_sights),
            }
        if status == "awaiting_clarification":
            # The request waiting for a choice stays apart from last_request, which belongs to
            # last_plan (the route on screen) -- a follow-up must change that route.
            return {
                **base,
                "awaiting_request": request,
                "clarification": plan.get("clarification") or [],
                "change_note": prefix,
            }
        errors = plan.get("errors") or []
        gap = any(e.get("code") == "routing_area_not_covered" for e in errors)
        reasons = {
            "no_route": "I could not find a route between those places.",
            "provider_failure": "The routing service failed for this request.",
            "invalid": "That request was not valid.",
        }
        text = reasons.get(str(status), f"Planning ended with status {status}.")
        if gap:
            text = (
                "The routing engine has no map data for part of this trip. In the web form you can "
                "download the tile or plan it with openrouteservice."
            )
        detail = "; ".join(
            str(e.get("message")) for e in errors[:2] if e.get("message") and not gap
        )
        return {
            **base,
            "failed": True,
            "failed_request": request,
            "reply": prefix + text + (f" ({detail})" if detail else ""),
            "suggestions": ["Use openrouteservice", "New route"] if gap else ["New route", "Help"],
        }

    def after_plan(state: ChatState) -> str:
        return "clarify" if state.get("clarification") and not state.get("reply") else "finish"

    async def clarify(state: ChatState) -> dict[str, Any]:
        groups = [g for g in state.get("clarification") or []]
        group = next((g for g in groups if g.get("candidates")), None)
        if group is None:  # nothing to choose from: the place was not found at all
            names = ", ".join(f"'{g.get('field')}'" for g in groups) or "a place"
            return {
                "clarification": [],
                "clarify_result": "failed",
                "failed": True,
                "reply": f"{state.get('change_note') or ''}I could not find {names}. Try a more "
                "precise name (with the town or country) or coordinates like 52.27, 10.52.",
                "suggestions": ["New route", "Help"],
            }
        candidates = group["candidates"][:5]
        lines = [f'Which "{group.get("field")}" do you mean?']
        lines += [f"{i}. {c.get('label')}" for i, c in enumerate(candidates, start=1)]
        answer = interrupt(
            {
                "reply": "\n".join(lines),
                "suggestions": [str(i) for i in range(1, len(candidates) + 1)],
                "awaiting": "place_choice",
            }
        )
        text = str(answer or "").strip()
        chosen = _choose(text, candidates)
        if chosen is None:
            followup = decide_intent(text, {**state, "asking": None}, text_enabled=text_enabled)
            if followup in ("reset", "help", "plan"):
                return {
                    "user_text": text,
                    "intent": followup,
                    "clarification": [],
                    "clarify_result": "restart",
                }
            return {"clarify_result": "retry"}
        request = _replace_place(
            state.get("awaiting_request") or state.get("last_request") or {},
            str(group.get("field")),
            chosen,
        )
        rest = [g for g in groups if g is not group]
        return {
            "pending_request": request,
            "clarification": rest,
            "clarify_result": "resolved",
            "messages": [HumanMessage(content=text)],
        }

    def after_clarify(state: ChatState) -> str:
        result = state.get("clarify_result")
        if result == "resolved":
            return "plan_route"
        if result == "restart":
            return "router"
        if result == "retry":
            return "clarify"
        return "finish"

    # ---------------------------------------------------------------- working with a plan

    async def alternatives(state: ChatState) -> dict[str, Any]:
        plan = state.get("last_plan")
        if not plan or plan.get("status") != "ready":
            return {"reply": "There is no route yet. " + HELP_TEXT}
        candidates = plan.get("candidates") or []
        picked = rules.pick_candidate(state["user_text"], candidates)
        asked_for_one = re.search(
            r"\b(?:show|use|take|pick|choose|switch|the)\b.*\b(?:one|version|route|option|alternative)"
            r"\b|\b(?:first|second|third|fastest|shortest|flattest|#\d)\b",
            state["user_text"],
            re.I,
        )
        if picked is not None:
            return {
                "reply": describe.describe_candidate(picked),
                "plan": plan,
                "focus_rank": picked.get("rank"),
                "suggestions": ["Alternatives", "What's the weather?", "Send me the GPX"],
            }
        if asked_for_one and len(candidates) > 1:
            return {
                "reply": "I could not tell which one you mean.\n"
                + describe.describe_alternatives(plan),
                "suggestions": [f"Show alternative {c.get('rank')}" for c in candidates[:5]],
            }
        return {
            "reply": describe.describe_alternatives(plan),
            "plan": plan,
            "suggestions": [f"Show alternative {c.get('rank')}" for c in candidates[:5]],
        }

    async def sights_node(state: ChatState) -> dict[str, Any]:
        plan = state.get("last_plan")
        route = (plan or {}).get("route") or {}
        line = ((route.get("geometry_geojson")) or {}).get("coordinates") or []
        if sights is None or len(line) < 2:
            return {"reply": "Sights along a route are not available here."}
        try:
            found = await sights.along([[p[0], p[1]] for p in line], 5)
        except SightsUnavailableError:
            return {"reply": "The sight lookup is not available right now. Try again in a moment."}
        if not found:
            return {"reply": "I found no well-known sights along this route."}
        rows = [
            f"- {p.get('name') or p.get('category')}"
            + (f" (described in {p['fame']} languages)" if p.get("fame") is not None else "")
            + (
                f", {round(p['distance_from_route_m'] / 10) * 10:.0f} m off the route"
                if p.get("distance_from_route_m") is not None
                else ""
            )
            for p in found
        ]
        is_loop = bool(
            ((state.get("last_request") or {}).get("constraints") or {}).get("return_to_origin")
        )
        return {
            "reply": "Well-known places near your route:\n" + "\n".join(rows),
            "suggestions": [] if is_loop else ["Route past 2 sights"],
        }

    async def explain(state: ChatState) -> dict[str, Any]:
        plan = state.get("last_plan")
        if not plan or plan.get("status") != "ready":
            return {"reply": "There is no route to talk about yet. " + HELP_TEXT}
        return {
            "reply": describe.explain(state["user_text"], plan),
            "suggestions": ["Alternatives", "Make it shorter", "Send me the GPX"],
        }

    async def export(state: ChatState) -> dict[str, Any]:
        plan = state.get("last_plan")
        artifacts = (plan or {}).get("artifacts") or {}
        if not artifacts:
            return {"reply": "There is no route file yet. Plan a route first."}
        links = [f"{name.replace('_url', '').upper()}: {url}" for name, url in artifacts.items()]
        return {
            "reply": "Files for the selected route:\n" + "\n".join(links),
            "suggestions": ["Alternatives", "New route"],
        }

    async def reset(state: ChatState) -> dict[str, Any]:
        return {
            "draft": {},
            "asking": "origin",  # the next message answers the first question
            "last_request": None,
            "last_plan": None,
            "failed_request": None,
            "awaiting_request": None,
            "clarification": [],
            "reply": "Okay, starting over. " + _question("origin")["reply"],
            "suggestions": [],
        }

    async def help_node(state: ChatState) -> dict[str, Any]:
        return {"reply": HELP_TEXT, "suggestions": ["Plan a route", "40 km loop from Goslar"]}

    async def finish(state: ChatState) -> dict[str, Any]:
        update: dict[str, Any] = {"messages": [AIMessage(content=state.get("reply") or "")]}
        messages = state.get("messages") or []
        excess = len(messages) + 1 - MAX_KEPT_MESSAGES
        if excess > 0:
            update["messages"] = [
                *(RemoveMessage(id=m.id) for m in messages[:excess] if m.id),
                *update["messages"],
            ]
        return update

    graph = StateGraph(ChatState)
    nodes = {
        "ingest": ingest,
        "plan_one_line": plan_one_line,
        "plan_text": plan_text,
        "guided": guided,
        "refine": refine,
        "plan_route": plan_route,
        "clarify": clarify,
        "alternatives": alternatives,
        "sights": sights_node,
        "explain": explain,
        "export": export,
        "reset": reset,
        "help": help_node,
        "finish": finish,
    }
    for name, fn in nodes.items():
        graph.add_node(name, fn)

    def router(state: ChatState) -> dict[str, Any]:  # a no-op node the clarify step can return to
        return {}

    graph.add_node("router", router)
    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "router")
    intents = [
        "plan_one_line", "plan_text", "guided", "refine", "alternatives",
        "sights", "explain", "export", "reset", "help",
    ]  # fmt: skip
    graph.add_conditional_edges("router", route_intent, intents)

    def after_request_step(state: ChatState) -> str:
        return "plan_route" if state.get("pending_request") else "finish"

    for step in ("plan_one_line", "guided", "refine"):
        graph.add_conditional_edges(step, after_request_step, ["plan_route", "finish"])
    graph.add_conditional_edges("plan_text", after_plan, ["clarify", "finish"])
    graph.add_conditional_edges("plan_route", after_plan, ["clarify", "finish"])
    graph.add_conditional_edges(
        "clarify", after_clarify, ["plan_route", "router", "clarify", "finish"]
    )
    for end_step in ("alternatives", "sights", "explain", "export", "reset", "help"):
        graph.add_edge(end_step, "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)


# ------------------------------------------------------------------------------ helpers


def _question(slot: str, problem: str | None = None) -> dict[str, Any]:
    """The reply and quick answers for the open question of the step-by-step dialogue."""
    prefix = f"{problem} " if problem else ""
    if slot == "origin":
        return {
            "reply": prefix + "Where do you want to start? (a place name or lat, lon)",
            "suggestions": [],
        }
    if slot == "mode":
        return {
            "reply": prefix + "Is it a loop back to the start, or from here to a destination?",
            "suggestions": ["A loop", "To a destination"],
        }
    if slot == "destination":
        return {"reply": prefix + "Where do you want to go?", "suggestions": []}
    if slot == "distance":
        return {
            "reply": prefix + "How long should the loop be? (in km)",
            "suggestions": ["20 km", "40 km", "60 km", "100 km"],
        }
    return {"reply": prefix + "Which bike? (or skip)", "suggestions": BIKE_CHOICES}


def _fill_slot(draft: dict[str, Any], slot: str, text: str) -> str | None:
    """Store an answer; return a problem message when it is not usable."""
    t = text.strip()
    if not t:
        return "I did not get that."
    if slot == "origin":
        draft["origin"] = rules.place_value(t)
    elif slot == "mode":
        if re.search(r"\bloop|round|circuit|back\b", t, re.I):
            draft["loop"] = True
        elif re.search(r"destination|point|\bto\b|a to b|somewhere", t, re.I):
            draft["loop"] = False
        else:
            return "Please choose: a loop, or to a destination."
    elif slot == "destination":
        draft["destination"] = rules.place_value(t)
    elif slot == "distance":
        match = re.search(r"(\d+(?:[.,]\d+)?)", t)
        value = float(match.group(1).replace(",", ".")) if match else None
        if value is None or not 1 <= value <= 1000:
            return "Please give a distance between 1 and 1000 km."
        draft["distance_km"] = value
    elif slot == "bike":
        word = re.search(rf"\b({'|'.join(re.escape(w) for w in rules.BIKE_WORDS)})\b", t, re.I)
        if word:
            draft["bike_type"] = rules.BIKE_WORDS[word.group(1).lower()]
        elif re.search(r"\b(?:skip|any|default|no|none|whatever)\b", t, re.I):
            draft["bike_type"] = "skip"
        else:
            return "I know road, gravel, touring, mountain, city, e-bike, commuter and recumbent."
    return None


def _next_slot(draft: dict[str, Any]) -> str | None:
    if "origin" not in draft:
        return "origin"
    if "loop" not in draft:
        return "mode"
    if draft["loop"] and "distance_km" not in draft:
        return "distance"
    if not draft["loop"] and "destination" not in draft:
        return "destination"
    if "bike_type" not in draft:
        return "bike"
    return None


def _request_from_draft(draft: dict[str, Any]) -> dict[str, Any]:
    constraints: dict[str, Any] = {}
    if draft.get("bike_type") not in (None, "skip"):
        constraints["bike_type"] = draft["bike_type"]
    request: dict[str, Any] = {"origin": draft["origin"], "via": [], "constraints": constraints}
    if draft.get("loop"):
        constraints["return_to_origin"] = True
        constraints["target_distance_km"] = draft["distance_km"]
    else:
        request["destination"] = draft["destination"]
    return request


def _choose(text: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The candidate a reply names: its number, its label, or "first"/"second" ..."""
    t = text.strip().lower()
    number = re.fullmatch(r"(?:no\.?\s*|#|number\s*)?(\d)\.?", t)
    if number and 1 <= int(number.group(1)) <= len(candidates):
        return candidates[int(number.group(1)) - 1]
    for word, rank in ((w, r) for w, r in rules._ORDINALS.items()):
        if re.fullmatch(rf"(?:the\s+)?{word}(?:\s+one)?", t) and rank <= len(candidates):
            return candidates[rank - 1]
    if len(t) >= 3:
        hits = [c for c in candidates if t in str(c.get("label", "")).lower()]
        if len(hits) == 1:
            return hits[0]
    return None


def _replace_place(
    request: dict[str, Any], field: str, candidate: dict[str, Any]
) -> dict[str, Any]:
    """The request with the place typed as ``field`` replaced by the chosen candidate's position."""
    coordinate = candidate.get("coordinate") or {}
    place = {"lon": coordinate.get("lon"), "lat": coordinate.get("lat")}
    wanted = field.strip().lower()

    def same(value: Any) -> bool:
        return isinstance(value, str) and value.strip().lower() == wanted

    new = {k: (list(v) if isinstance(v, list) else v) for k, v in request.items()}
    if same(new.get("origin")):
        new["origin"] = place
    if same(new.get("destination")):
        new["destination"] = place
    new["via"] = [place if same(v) else v for v in new.get("via") or []]
    return new
