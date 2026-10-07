"""Measure the free-text parser against ``benchmarks/parser-v1.json``.

The harness is deliberately separate from the parser so the same cases can run
offline (a scripted fake model, to test the *checks*) and live (the real model,
to measure the *parser*). Checks are about what the text states: unstated
fields must be absent, places must be words, and instructions inside the text
must not be followed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from bike_routing_agent.llm.parser import LLMParseError

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "benchmarks" / "parser-v1.json"
_COORDINATE = re.compile(r"-?\d{1,3}\.\d+\s*[,;\s]\s*-?\d{1,3}\.\d+")


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    error_code: str | None = None


@dataclass
class Report:
    results: list[CaseResult]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def accuracy(self) -> float:
        return self.passed / len(self.results) if self.results else 0.0

    @property
    def failed(self) -> list[CaseResult]:
        return [r for r in self.results if not r.passed]


def load_benchmark(path: Path | str = DEFAULT_PATH) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not data.get("cases"):
        raise ValueError(f"{path} is not a parser benchmark (schema_version 1 with cases)")
    ids = [c["id"] for c in data["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids in the parser benchmark")
    return data


def _same_instant(a: str, b: str) -> bool:
    try:
        return datetime.fromisoformat(a.replace("Z", "+00:00")) == datetime.fromisoformat(
            b.replace("Z", "+00:00")
        )
    except ValueError:
        return False


def _contains(actual: Any, wanted: str) -> bool:
    return isinstance(actual, str) and wanted.lower() in actual.lower()


def check_case(case: dict[str, Any], result: dict[str, Any] | None, error: str | None) -> list[str]:
    """Why ``result`` does not meet the case's expectations (empty = it does)."""
    expect = case["expect"]
    failures: list[str] = []

    if "error" in expect:
        if error != expect["error"]:
            failures.append(f"expected error {expect['error']!r}, got {error or 'a result'!r}")
        return failures
    if result is None:
        return [f"parse failed with {error!r}"]

    places = [result.get("origin"), result.get("destination"), *result.get("via", [])]
    if "origin" in expect and not _contains(result.get("origin"), expect["origin"]):
        failures.append(f"origin {result.get('origin')!r} does not contain {expect['origin']!r}")
    if "destination" in expect:
        wanted = expect["destination"]
        actual = result.get("destination")
        if wanted is None and actual is not None:
            failures.append(f"destination should be empty, got {actual!r}")
        if wanted is not None and not _contains(actual, wanted):
            failures.append(f"destination {actual!r} does not contain {wanted!r}")
    for wanted in expect.get("via_contains", []):
        if not any(_contains(v, wanted) for v in result.get("via", [])):
            failures.append(f"via does not contain {wanted!r}")
    if expect.get("places_are_words") and any(
        isinstance(p, str) and _COORDINATE.search(p) for p in places if p
    ):
        failures.append(f"a place is a coordinate, not a name: {places}")

    constraints = result.get("constraints", {})
    if "constraints" in expect and constraints != expect["constraints"]:
        failures.append(f"constraints {constraints} != expected {expect['constraints']}")
    for key, options in expect.get("constraints_any_of", {}).items():
        values = [str(v).lower() for v in constraints.get(key, [])]
        if not any(o.lower() in values for o in options):
            failures.append(f"{key} {constraints.get(key)} has none of {options}")

    if "departure_time" in expect:
        wanted_time = expect["departure_time"]
        actual_time = result.get("departure_time")
        if wanted_time is None and actual_time is not None:
            failures.append(f"departure_time should be empty, got {actual_time!r}")
        if wanted_time is not None and not (
            actual_time and _same_instant(actual_time, wanted_time)
        ):
            failures.append(f"departure_time {actual_time!r} is not {wanted_time!r}")

    notes = result.get("notes", [])
    if expect.get("notes") is True and not notes:
        failures.append("expected a note about something unclear or not expressible")
    joined = " ".join(notes).lower()
    for forbidden in expect.get("notes_must_not_contain", []):
        if forbidden.lower() in joined:
            failures.append(f"notes leak {forbidden!r}")
    return failures


def run_benchmark(
    parser: Callable[..., dict[str, Any]],
    benchmark: dict[str, Any] | None = None,
) -> Report:
    benchmark = benchmark or load_benchmark()
    results: list[CaseResult] = []
    for case in benchmark["cases"]:
        result: dict[str, Any] | None = None
        error: str | None = None
        try:
            kwargs = {"timezone": case["timezone"]} if case.get("timezone") else {}
            result = parser(case["text"], **kwargs)
        except LLMParseError as exc:
            error = exc.code
        failures = check_case(case, result, error)
        results.append(CaseResult(case["id"], not failures, failures, error))
    return Report(results)
