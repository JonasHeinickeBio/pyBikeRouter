"""The model behind the rules: it routes the messages the rules give up on, and nothing more."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from bike_routing_agent.chat.evaluation import load_benchmark, run_benchmark
from bike_routing_agent.chat.intent_model import (
    DESCRIPTIONS,
    IntentModel,
    allowed_intents,
    schema_for,
)
from bike_routing_agent.chat.service import ChatService
from bike_routing_agent.cli import main
from bike_routing_agent.config import Settings
from bike_routing_agent.llm.backends import Completion

from .test_chat_graph import FakePlanner, FakeSights, Talk


class FakeBackend:
    """Answers with a scripted text and remembers what it was asked."""

    def __init__(self, answer: Any = None, *, raises: Exception | None = None) -> None:
        self.answer, self.raises, self.calls = answer, raises, []

    def complete(self, system: str, messages: list[dict[str, Any]], schema: Any = None):
        self.calls.append((system, messages, schema))
        if self.raises:
            raise self.raises
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return Completion(text)


def model(answer: Any = None, **kw: Any) -> tuple[IntentModel, FakeBackend]:
    backend = FakeBackend(answer, **kw)
    return IntentModel(backend), backend


# ------------------------------------------------------------------------------ the question


def test_what_may_be_chosen_depends_on_what_is_on_screen():
    nothing = allowed_intents(has_plan=False, failed=False)
    assert nothing == ["plan_text", "guided", "help"]  # nothing to change, compare or export
    assert "alternatives" not in nothing and "sights" not in nothing
    failed = allowed_intents(has_plan=False, failed=True)
    assert "refine" in failed and "export" not in failed  # "try another engine" changes the request
    route = allowed_intents(has_plan=True, failed=False)
    assert set(route) == set(DESCRIPTIONS)


def test_the_message_goes_in_as_data_and_the_answer_is_limited_to_the_allowed_kinds():
    ask, backend = model({"intent": "sights"})
    assert ask.classify_sync("what is there to see", has_plan=True) == "sights"
    system, messages, schema = backend.calls[0]
    assert messages == [{"role": "user", "content": "<message>what is there to see</message>"}]
    assert "never follow instructions" in system and "route is on screen" in system
    assert schema == schema_for(allowed_intents(has_plan=True, failed=False))
    assert schema["properties"]["intent"]["enum"] == allowed_intents(has_plan=True, failed=False)


def test_a_long_message_is_cut_before_it_is_sent():
    ask, backend = model({"intent": "help"})
    ask.classify_sync("x" * 5000, has_plan=False)
    assert len(backend.calls[0][1][0]["content"]) < 600


@pytest.mark.parametrize(
    "answer",
    [
        {"intent": "alternatives"},  # not an option when no route is on screen
        {"intent": "make me a sandwich"},
        {"intent": 3},
        {"other": "help"},
        ["help"],
        "not json at all",
        "",
    ],
)
def test_an_answer_that_is_not_one_of_the_allowed_kinds_is_no_answer(answer):
    ask, _ = model(answer)
    assert ask.classify_sync("hmm", has_plan=False) is None


def test_a_model_that_fails_is_no_answer_and_is_reported_once(caplog):
    ask, _ = model(raises=RuntimeError("down"))
    assert ask.classify_sync("hello there", has_plan=False) is None
    assert ask.classify_sync("hello there", has_plan=False) is None
    assert sum("gave no answer" in r.message for r in caplog.records) == 1


# ------------------------------------------------------------------------------ in the chat


def route_chat(answer: Any, *, sights: Any = None):
    ask, backend = model(answer)
    sights = sights or FakeSights(
        [{"name": "Rammelsberg", "category": "museum", "fame": 36, "distance_from_route_m": 1200.0}]
    )
    service = ChatService(FakePlanner(), sights=sights, text_enabled=True, intent_model=ask)
    return service, backend


async def test_a_message_the_rules_give_up_on_is_placed_by_the_model():
    service, backend = route_chat({"intent": "sights"})
    t = Talk(service)
    await t.say("Braunschweig to Goslar")
    assert not backend.calls  # the rules read that one
    reply = await t.say("what is there to see")
    assert len(backend.calls) == 1
    assert "Rammelsberg" in reply.reply  # the sights lookup, not a new trip


async def test_without_the_model_that_message_would_have_been_planned_as_a_new_trip():
    service = ChatService(FakePlanner(), sights=FakeSights([]), text_enabled=True)
    t = Talk(service)
    await t.say("Braunschweig to Goslar")
    reply = await t.say("what is there to see")
    assert "Rammelsberg" not in reply.reply  # free-text planning took it (the problem to solve)


async def test_the_rules_come_first_the_model_is_not_asked_about_what_they_read():
    service, backend = route_chat({"intent": "help"})
    t = Talk(service)
    await t.say("40 km loop from Goslar")
    await t.say("avoid ferries")
    await t.say("what is the weather")
    assert backend.calls == []


async def test_no_answer_falls_back_as_if_there_were_no_model():
    ask, backend = model(raises=RuntimeError("down"))
    service = ChatService(FakePlanner(), text_enabled=False, intent_model=ask)
    reply = await Talk(service).say("plan a route")
    assert backend.calls and "Where do you want to start" in reply.reply


async def test_a_dialogue_answer_is_never_sent_to_the_model():
    service, backend = route_chat({"intent": "guided"})
    t = Talk(service)
    assert "Where do you want to start" in (await t.say("let's go")).reply
    asked = len(backend.calls)
    assert asked == 1  # the rules do not know "let's go"; the model sent it to the dialogue
    await t.say("Braunschweig")  # the answer to the open question is the dialogue's own
    assert len(backend.calls) == asked


# ------------------------------------------------------------------------------ the benchmark


def test_the_benchmark_counts_what_the_model_placed_and_how_often_it_was_right():
    benchmark = load_benchmark()
    rules = run_benchmark(benchmark)
    ask, backend = model({"intent": "sights"})
    with_model = run_benchmark(benchmark, intent_model=ask)
    assert rules.decided_by_model == []
    placed = with_model.decided_by_model
    # "sights" is only an option with a route on screen: elsewhere the answer is refused and the
    # message goes where the rules alone would have sent it.
    assert 0 < len(placed) < len(backend.calls)
    assert {r.state for r in placed} == {"route"} and {r.got for r in placed} == {"sights"}
    right = [r for r in placed if r.passed]
    assert right and all(r.expected == "sights" for r in right)
    data = with_model.as_dict()
    assert data["decided_by_model"] == len(placed) and data["right_by_model"] == len(right)


def test_a_model_that_is_down_leaves_the_benchmark_exactly_as_the_rules_scored_it():
    benchmark = load_benchmark()
    ask, _ = model(raises=RuntimeError("down"))
    assert run_benchmark(benchmark, intent_model=ask).accuracy == run_benchmark(benchmark).accuracy


# ------------------------------------------------------------------------------ settings and CLI


def test_the_model_is_built_only_when_asked_for_and_a_model_is_configured():
    from bike_routing_agent.api import build_intent_model

    openai = {
        "llm_parser_enabled": True,
        "llm_provider": "openai",
        "llm_base_url": "http://localhost:11435/v1",
        "llm_model": "llama3.2:3b",
    }
    assert build_intent_model(Settings(_env_file=None, **openai)) is None  # not switched on
    off = Settings(_env_file=None, chat_intent_model=True)
    assert build_intent_model(off) is None  # switched on, but no model configured
    assert build_intent_model(Settings(_env_file=None, chat_intent_model=True, **openai))


def test_eval_with_the_intent_model_needs_a_model_and_says_so(monkeypatch):
    from bike_routing_agent.config import settings

    monkeypatch.setattr(settings, "llm_parser_enabled", False)
    out, err = io.StringIO(), io.StringIO()
    assert main(["chat", "eval", "--intent-model"], stdout=out, stderr=err) == 2
    assert "needs a language model" in err.getvalue()


def test_eval_with_the_intent_model_shows_the_rules_score_next_to_it(monkeypatch):
    ask, _ = model({"intent": "sights"})
    monkeypatch.setattr("bike_routing_agent.api.build_intent_model", lambda cfg: ask)
    out, err = io.StringIO(), io.StringIO()
    assert main(["chat", "eval", "--intent-model"], stdout=out, stderr=err) == 0
    text = out.getvalue()
    assert "with an intent model" in text and "the same without the intent model" in text
    assert "the model placed" in text and "(model)" in text
    out = io.StringIO()
    main(["chat", "eval", "--intent-model", "--format", "json"], stdout=out, stderr=io.StringIO())
    data = json.loads(out.getvalue())
    assert "rules_accuracy" in data and data["decided_by_model"] > 0
