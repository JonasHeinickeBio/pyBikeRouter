"""The chat benchmark: its file, its runner, and the floor the rules must not fall below."""

from __future__ import annotations

import json

import pytest

from bike_routing_agent.chat.evaluation import (
    INTENTS,
    STATES,
    CaseResult,
    Report,
    build_state,
    load_benchmark,
    run_benchmark,
)


def write(tmp_path, cases, **top):
    path = tmp_path / "bench.json"
    path.write_text(json.dumps({"schema_version": 1, "cases": cases, **top}), encoding="utf-8")
    return path


def case(cid, text, state, expected):
    return {"id": cid, "text": text, "state": state, "expected": expected}


# ------------------------------------------------------------------------------ the shipped file


def test_the_shipped_benchmark_is_well_formed_and_covers_every_intent_and_state():
    data = load_benchmark()
    cases = data["cases"]
    assert len(cases) >= 100
    assert {c["expected"] for c in cases} == set(INTENTS)
    assert {c["state"] for c in cases} == set(STATES)
    assert all(c["text"].strip() for c in cases)
    texts = [(c["text"].lower(), c["state"]) for c in cases]
    assert len(texts) == len(set(texts))  # a phrase is judged once per situation


def test_the_rules_keep_a_floor_so_a_change_that_makes_them_worse_is_seen():
    report = run_benchmark(load_benchmark())
    assert report.accuracy >= 0.85, [(r.text, r.expected, r.got) for r in report.failed]
    table = report.by_intent()
    # what the rules read reliably stays reliable
    for intent in ("alternatives", "export", "reset", "plan"):
        assert table[intent]["recall"] == 1.0, intent
    assert report.accuracy < 1.0  # an honest benchmark: it still has things to teach
    rules_only = run_benchmark(load_benchmark(), text_enabled=False)
    assert rules_only.accuracy <= report.accuracy  # free text needs the parser to be read at all


def test_known_gaps_are_recorded_here_so_fixing_one_is_a_visible_change():
    """These phrasings are read wrongly today (see the benchmark). When a rule starts to read one
    right, move it out of this list: the accuracy went up and the test says where."""
    wrong = {r.text for r in run_benchmark(load_benchmark()).failed}
    assert {"points of interest", "no ferries please", "with a stop in Celle"} <= wrong


# ------------------------------------------------------------------------------ the runner


def test_cases_are_read_in_their_situation(tmp_path):
    path = write(
        tmp_path,
        [
            case("a", "make it shorter", "route", "refine"),
            case("b", "make it shorter", "none", "guided"),  # nothing to shorten
            case("c", "Braunschweig", "asking", "guided"),
            case("d", "use openrouteservice", "failed", "refine"),
            case("e", "from A to B", "none", "plan"),
        ],
    )
    report = run_benchmark(load_benchmark(path), text_enabled=False)
    assert [r.got for r in report.results] == ["refine", "guided", "guided", "refine", "plan"]
    assert report.accuracy == 1.0 and report.failed == []


def test_the_report_counts_recall_and_precision_per_intent():
    report = Report(
        [
            CaseResult("1", "a", "none", "plan", "plan"),
            CaseResult("2", "b", "none", "plan", "guided"),
            CaseResult("3", "c", "none", "guided", "guided"),
            CaseResult("4", "d", "none", "refine", "guided"),
        ]
    )
    table = report.by_intent()
    assert table["plan"] == {"cases": 2, "right": 1, "recall": 0.5, "chosen": 1, "precision": 1.0}
    assert table["guided"]["chosen"] == 3 and table["guided"]["precision"] == pytest.approx(1 / 3)
    assert table["refine"]["recall"] == 0.0 and table["refine"]["precision"] == 0.0
    assert "alternatives" not in table  # intents nobody expected or chose are left out
    as_dict = report.as_dict()
    assert as_dict["cases"] == 4 and as_dict["passed"] == 2 and as_dict["accuracy"] == 0.5
    assert [f["id"] for f in as_dict["failures"]] == ["2", "4"]
    assert Report().accuracy == 0.0


def test_a_broken_benchmark_is_refused_with_the_reason(tmp_path):
    cases = [case("a", "x", "route", "refine")]
    with pytest.raises(ValueError, match="schema_version"):
        load_benchmark(write(tmp_path, [], schema_version=1))
    with pytest.raises(ValueError, match="duplicate"):
        load_benchmark(write(tmp_path, [*cases, *cases]))
    with pytest.raises(ValueError, match="state must be"):
        load_benchmark(write(tmp_path, [case("a", "x", "nowhere", "refine")]))
    with pytest.raises(ValueError, match="unlabelled"):
        load_benchmark(write(tmp_path, [case("a", "x", "route", None)]))
    with pytest.raises(ValueError, match="not an intent"):
        load_benchmark(write(tmp_path, [case("a", "x", "route", "dance")]))
    with pytest.raises(ValueError, match="unknown state"):
        build_state("nowhere")


def test_a_case_that_is_not_an_object_or_lacks_its_text_is_refused_not_a_traceback(tmp_path):
    for bad in (None, "text", 5, [], {"id": "a"}, {"text": "x"}, {"id": 1, "text": "x"}):
        with pytest.raises(ValueError, match="not an object|text and an id"):
            load_benchmark(write(tmp_path, [bad]))
