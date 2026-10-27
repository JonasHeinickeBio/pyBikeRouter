"""Measure which way the chat reads a message against the shipped ``data/chat-v1.json``.

Each case is a message, the situation it arrives in and the intent a sensible person would say it
is. The rules decide (``graph.decide_intent``), nothing is planned and no model is called, so it
costs nothing and can run in CI. The point is a *number* to improve and to beat: the cases the rules
get wrong are exactly where a model fallback would have to do better, and a rule change that fixes
one phrasing must not break the others.

Situations (``state``): ``none`` (nothing planned yet), ``route`` (a route with two alternatives is
on screen), ``asking`` (the dialogue asked a question and waits for the answer), ``failed`` (the
last request found no route).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from bike_routing_agent.chat.graph import fallback_intent, rule_intent
from bike_routing_agent.chat.intent_model import IntentModel
from bike_routing_agent.chat.models import ChatState

DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "chat-v1.json"
INTENTS = (
    "plan",
    "plan_text",
    "guided",
    "refine",
    "alternatives",
    "sights",
    "explain",
    "export",
    "optimize",
    "reset",
    "help",
)
STATES = ("none", "route", "asking", "failed")


def _candidate(rank: int, profile: str, distance_m: float) -> dict[str, Any]:
    return {
        "provider": "brouter",
        "provider_profile": profile,
        "rank": rank,
        "score": 0.9,
        "geometry_geojson": {"type": "LineString", "coordinates": [[10.5, 52.2], [10.6, 52.3]]},
        "metrics": {
            "distance_m": distance_m,
            "duration_s": distance_m / 4.5,
            "ascent_m": 160.0,
            "descent_m": 150.0,
        },
        "pros": [],
        "cons": [],
        "warnings": [],
    }


def build_state(name: str) -> ChatState:
    """The conversation a case starts from."""
    if name == "none":
        return cast(ChatState, {})
    if name == "route":
        first, second = _candidate(1, "custom_gravel-v2", 30_600.0), _candidate(2, "mtb", 29_900.0)
        return cast(
            ChatState,
            {
                "last_request": {"origin": "Braunschweig", "destination": "Goslar", "via": []},
                "last_plan": {
                    "status": "ready",
                    "route": first,
                    "candidates": [first, second],
                    "artifacts": {"gpx_url": "/v1/routes/a.gpx"},
                },
            },
        )
    if name == "asking":
        return cast(ChatState, {"asking": "origin", "draft": {}})
    if name == "failed":
        return cast(
            ChatState, {"failed_request": {"origin": "Braunschweig", "destination": "Goslar"}}
        )
    raise ValueError(f"unknown state {name!r} (one of {', '.join(STATES)})")


@dataclass
class CaseResult:
    case_id: str
    text: str
    state: str
    expected: str
    got: str
    by_model: bool = False  # the rules gave up and the intent model placed the message

    @property
    def passed(self) -> bool:
        return self.expected == self.got


@dataclass
class Report:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def accuracy(self) -> float:
        return self.passed / len(self.results) if self.results else 0.0

    @property
    def decided_by_model(self) -> list[CaseResult]:
        return [r for r in self.results if r.by_model]

    @property
    def failed(self) -> list[CaseResult]:
        return [r for r in self.results if not r.passed]

    def by_intent(self) -> dict[str, dict[str, float | int]]:
        """Per intent: how many cases expect it and how many of those were read right (recall),
        and how many times it was chosen and how many of those were right (precision)."""
        expected = Counter(r.expected for r in self.results)
        chosen = Counter(r.got for r in self.results)
        right = Counter(r.expected for r in self.results if r.passed)
        table: dict[str, dict[str, float | int]] = {}
        for intent in INTENTS:
            if not expected[intent] and not chosen[intent]:
                continue
            table[intent] = {
                "cases": expected[intent],
                "right": right[intent],
                "recall": right[intent] / expected[intent] if expected[intent] else 0.0,
                "chosen": chosen[intent],
                "precision": right[intent] / chosen[intent] if chosen[intent] else 0.0,
            }
        return table

    def as_dict(self) -> dict[str, Any]:
        return {
            "cases": len(self.results),
            "passed": self.passed,
            "accuracy": round(self.accuracy, 4),
            "decided_by_model": len(self.decided_by_model),
            "right_by_model": sum(1 for r in self.decided_by_model if r.passed),
            "by_intent": self.by_intent(),
            "failures": [
                {
                    "id": r.case_id,
                    "text": r.text,
                    "state": r.state,
                    "expected": r.expected,
                    "got": r.got,
                }
                for r in self.failed
            ],
        }


def load_benchmark(path: Path | str = DEFAULT_PATH) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not data.get("cases"):
        raise ValueError(f"{path} is not a chat benchmark (schema_version 1 with cases)")
    for case in data["cases"]:
        if not isinstance(case, dict):
            raise ValueError(f"{path} contains a case that is not an object")
        if not isinstance(case.get("id"), str) or not isinstance(case.get("text"), str):
            raise ValueError(f"{path}: every case needs a text and an id (both strings)")
    ids = [c["id"] for c in data["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids in the chat benchmark")
    for case in data["cases"]:
        if case.get("state") not in STATES:
            raise ValueError(f"case {case['id']}: state must be one of {', '.join(STATES)}")
        if case.get("expected") not in INTENTS:
            where = "is still unlabelled" if case.get("expected") is None else "is not an intent"
            raise ValueError(f"case {case['id']}: 'expected' {where} (one of {', '.join(INTENTS)})")
    return data


def run_benchmark(
    benchmark: dict[str, Any],
    *,
    text_enabled: bool = True,
    intent_model: IntentModel | None = None,
) -> Report:
    """Read every case with the rules. ``text_enabled`` stands for a configured language-model
    parser: with it, a message no rule claims is routed to free-text planning (``plan_text``).
    With an ``intent_model`` that message is first put to the model (a real call per message the
    rules give up on), exactly where the chat would ask it."""
    report = Report()
    for case in benchmark["cases"]:
        text, state = case["text"], build_state(case["state"])
        got = rule_intent(text, state)
        by_model = False
        if got is None and intent_model is not None:
            got = intent_model.classify_sync(
                text,
                has_plan=bool(state.get("last_plan")),
                failed=bool(state.get("failed_request")),
            )
            by_model = got is not None
        got = got or fallback_intent(text, text_enabled=text_enabled)
        report.results.append(
            CaseResult(case["id"], text, case["state"], case["expected"], got, by_model)
        )
    return report
