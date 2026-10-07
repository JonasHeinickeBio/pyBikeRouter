"""The parser benchmark and its harness: the checks themselves are tested here;
how well the real model does is measured by scripts/eval_parser.py (live)."""

import json

import pytest

from bike_routing_agent.llm.evaluation import (
    DEFAULT_PATH,
    check_case,
    load_benchmark,
    run_benchmark,
)
from bike_routing_agent.llm.parser import LLMParseError


def oracle(case):
    """Builds exactly the answer a case expects: the benchmark must be self-consistent."""
    expect = case["expect"]
    if "error" in expect:
        raise LLMParseError(expect["error"], "expected")
    constraints = dict(expect.get("constraints", {}))
    for key, options in expect.get("constraints_any_of", {}).items():
        constraints[key] = [options[0]]
    return {
        "origin": expect.get("origin", "somewhere"),
        "destination": expect.get("destination"),
        "via": list(expect.get("via_contains", [])),
        "constraints": constraints,
        "departure_time": expect.get("departure_time"),
        "notes": ["something was unclear"] if expect.get("notes") else [],
    }


def oracle_parser(cases):
    by_text = {c["text"]: c for c in cases}
    return lambda text, **kw: oracle(by_text[text])


def test_the_benchmark_loads_and_covers_the_cases_that_matter():
    benchmark = load_benchmark()
    ids = {c["id"] for c in benchmark["cases"]}
    assert len(ids) == len(benchmark["cases"]) >= 15
    # the safety-relevant behaviours each have at least one case
    assert {"no-invented-coordinates", "injection-coordinates", "injection-tag-breakout"} <= ids
    assert {"not-a-route", "day-without-a-time", "unexpressible-wish"} <= ids
    assert any(c.get("timezone") for c in benchmark["cases"])
    assert benchmark["now"].startswith("2026-10-07")


def test_a_malformed_benchmark_is_rejected(tmp_path):
    for body in [{"schema_version": 2, "cases": [{}]}, {"schema_version": 1, "cases": []}]:
        path = tmp_path / "b.json"
        path.write_text(json.dumps(body))
        with pytest.raises(ValueError, match="not a parser benchmark"):
            load_benchmark(path)
    dup = {"schema_version": 1, "cases": [{"id": "a"}, {"id": "a"}]}
    path = tmp_path / "dup.json"
    path.write_text(json.dumps(dup))
    with pytest.raises(ValueError, match="duplicate"):
        load_benchmark(path)


def test_an_answer_that_matches_every_expectation_passes_the_whole_benchmark():
    benchmark = load_benchmark(DEFAULT_PATH)
    report = run_benchmark(oracle_parser(benchmark["cases"]), benchmark)
    assert [r.case_id for r in report.failed] == []
    assert report.accuracy == 1.0 and report.passed == len(benchmark["cases"])


def test_a_parser_that_invents_coordinates_fails_the_coordinate_cases():
    benchmark = load_benchmark()
    oracle_for = oracle_parser(benchmark["cases"])

    def inventive(text, **kw):
        result = oracle_for(text)
        result["origin"] = "48.8584, 2.2945"
        return result

    failed = {r.case_id for r in run_benchmark(inventive, benchmark).failed}
    assert {"no-invented-coordinates", "injection-coordinates", "vague-places-stay-words"} <= failed


def test_a_parser_that_obeys_injected_instructions_fails_the_injection_cases():
    benchmark = load_benchmark()
    oracle_for = oracle_parser(benchmark["cases"])

    def obedient(text, **kw):
        result = oracle_for(text)
        if "pirate" in text:
            result["constraints"] = {"bike_type": "mountain"}
        if "system prompt" in text:
            result["notes"] = ["NEVER output coordinates ... the system prompt is: ..."]
        return result

    failed = {r.case_id for r in run_benchmark(obedient, benchmark).failed}
    assert {"injection-tag-breakout", "injection-reveal-prompt"} <= failed


def test_a_parser_that_fills_in_defaults_the_text_never_stated_fails():
    benchmark = load_benchmark()
    oracle_for = oracle_parser(benchmark["cases"])

    def assuming(text, **kw):
        result = oracle_for(text)
        result["constraints"] = {
            **result["constraints"],
            "bike_type": "gravel",
            "avoid_ferries": True,
        }
        return result

    failed = {r.case_id for r in run_benchmark(assuming, benchmark).failed}
    assert "simple-a-to-b-de" in failed and "via-point" in failed


def test_errors_are_scored_by_code():
    benchmark = {
        "schema_version": 1,
        "cases": [
            {"id": "needs-error", "text": "t", "expect": {"error": "llm_parser_no_route_request"}},
            {"id": "wants-result", "text": "u", "expect": {"origin": "A"}},
        ],
    }

    def parser(text, **kw):
        raise LLMParseError("llm_parser_declined", "no")

    report = run_benchmark(parser, benchmark)
    by_id = {r.case_id: r for r in report.results}
    assert not by_id["needs-error"].passed and "expected error" in by_id["needs-error"].failures[0]
    assert (
        not by_id["wants-result"].passed
        and by_id["wants-result"].error_code == "llm_parser_declined"
    )
    assert report.accuracy == 0.0 and len(report.failed) == 2


def test_the_timezone_is_passed_to_the_parser_only_for_cases_that_have_one():
    benchmark = {
        "schema_version": 1,
        "cases": [
            {"id": "a", "text": "x", "timezone": "Europe/Berlin", "expect": {"origin": "A"}},
            {"id": "b", "text": "y", "expect": {"origin": "A"}},
        ],
    }
    seen = []

    def parser(text, **kw):
        seen.append(kw)
        return {"origin": "A", "destination": None, "via": [], "constraints": {}, "notes": []}

    run_benchmark(parser, benchmark)
    assert seen == [{"timezone": "Europe/Berlin"}, {}]


@pytest.mark.parametrize(
    ("expect", "result", "fragment"),
    [
        ({"origin": "Bremen"}, {"origin": "Hamburg"}, "origin"),
        ({"destination": None}, {"origin": "A", "destination": "B"}, "destination should be empty"),
        ({"destination": "Hamburg"}, {"origin": "A", "destination": None}, "destination"),
        ({"via_contains": ["Verden"]}, {"origin": "A", "via": ["Celle"]}, "via"),
        ({"constraints": {"bike_type": "road"}}, {"origin": "A", "constraints": {}}, "constraints"),
        (
            {"constraints_any_of": {"prefer_surfaces": ["asphalt"]}},
            {"origin": "A", "constraints": {"prefer_surfaces": ["gravel"]}},
            "prefer_surfaces",
        ),
        (
            {"departure_time": None},
            {"origin": "A", "departure_time": "2026-10-08T08:00:00Z"},
            "empty",
        ),
        (
            {"departure_time": "2026-10-08T08:00:00+02:00"},
            {"origin": "A", "departure_time": "2026-10-08T09:00:00+02:00"},
            "departure_time",
        ),
        ({"notes": True}, {"origin": "A", "notes": []}, "note"),
        ({"notes_must_not_contain": ["secret"]}, {"origin": "A", "notes": ["a SECRET"]}, "leak"),
        ({"places_are_words": True}, {"origin": "52.1, 10.2"}, "coordinate"),
    ],
)
def test_each_check_catches_its_violation(expect, result, fragment):
    failures = check_case({"expect": expect}, result, None)
    assert failures and any(fragment in f for f in failures)


def test_the_same_instant_in_another_offset_counts_as_equal():
    case = {"expect": {"departure_time": "2026-10-08T08:00:00+02:00"}}
    ok = check_case(case, {"origin": "A", "departure_time": "2026-10-08T06:00:00Z"}, None)
    assert ok == []


def test_the_eval_script_reports_per_case_results_and_exits_by_accuracy(
    monkeypatch, capsys, tmp_path
):
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "eval_parser", Path(__file__).resolve().parents[2] / "scripts" / "eval_parser.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_parser"] = module
    spec.loader.exec_module(module)

    benchmark = load_benchmark()
    cases = benchmark["cases"]

    monkeypatch.setattr(module, "build_llm_parser", lambda cfg, now: oracle_parser(cases))
    monkeypatch.setenv("LLM_MODEL", "claude-test")
    out = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["eval_parser.py", "--out", str(out), "--fail-under", "1.0"])

    assert module.main() == 0
    printed = capsys.readouterr().out
    assert (
        "PASS  simple-a-to-b-de" in printed
        and f"{len(cases)}/{len(cases)} cases passed (100%)" in printed
    )
    assert json.loads(out.read_text())["accuracy"] == 1.0

    monkeypatch.delenv("LLM_MODEL")
    assert module.main() == 2
