"""``bike-router chat eval`` and ``chat misses``."""

from __future__ import annotations

import io
import json

import pytest

from bike_routing_agent.chat.misslog import MissLog
from bike_routing_agent.cli import main


def run(*argv: str):
    out, err = io.StringIO(), io.StringIO()
    rc = main(["chat", *argv], stdout=out, stderr=err)
    return rc, out.getvalue(), err.getvalue()


@pytest.fixture(autouse=True)
def _no_miss_log_from_the_environment(monkeypatch):
    from bike_routing_agent.config import settings

    monkeypatch.setattr(settings, "chat_miss_log_path", None)


# ------------------------------------------------------------------------------ eval


def test_eval_prints_the_score_the_table_and_what_was_read_differently():
    rc, out, _ = run("eval")
    assert rc == 0
    assert out.startswith("Chat benchmark (with a free-text parser):")
    assert "recall" in out and "precision" in out and "Read differently than expected:" in out
    assert "'points of interest': expected sights, got plan_text" in out


def test_eval_rules_only_scores_lower_than_with_a_parser():
    def accuracy(*extra):
        rc, out, _ = run("eval", "--format", "json", *extra)
        assert rc == 0
        return json.loads(out)["accuracy"]

    assert accuracy("--rules-only") < accuracy()


def test_eval_json_has_the_failures_and_the_table():
    rc, out, _ = run("eval", "--format", "json")
    data = json.loads(out)
    assert rc == 0 and data["cases"] >= 100 and data["by_intent"]["alternatives"]["recall"] == 1.0
    assert all({"id", "text", "state", "expected", "got"} <= set(f) for f in data["failures"])


def test_eval_fails_below_the_given_accuracy_and_passes_above():
    assert run("eval", "--fail-under", "0.99")[0] == 1
    assert run("eval", "--fail-under", "0.5")[0] == 0


def test_eval_with_a_bad_benchmark_is_a_usage_error(tmp_path):
    missing = tmp_path / "nope.json"
    rc, _, err = run("eval", "--benchmark", str(missing))
    assert rc == 2 and "error:" in err
    broken = tmp_path / "b.json"
    broken.write_text('{"schema_version": 1, "cases": [{"id": "a", "text": "x"}]}')
    assert run("eval", "--benchmark", str(broken))[0] == 2


# ------------------------------------------------------------------------------ misses


def seed(path):
    log = MissLog(path)
    for text, kind, plan in [
        ("show me something nice", "unread", False),
        ("Show me something nice!", "unread", False),
        ("make it purple", "refine_unknown", True),
    ]:
        log.record(text, kind, intent="guided", had_plan=plan)


def test_misses_without_a_log_says_how_to_switch_it_on():
    rc, _, err = run("misses")
    assert rc == 2 and "CHAT_MISS_LOG_PATH" in err and "off by default" in err


def test_misses_summarises_by_kind_and_lists_the_phrases_that_come_back(tmp_path):
    path = tmp_path / "m.jsonl"
    seed(path)
    rc, out, _ = run("misses", "--path", str(path))
    assert rc == 0
    assert "3 message(s) the chat could not read (unread 2, refine_unknown 1)." in out
    assert "2x  show me something nice" in out and "1x  make it purple" in out


def test_misses_json_and_an_empty_log(tmp_path):
    path = tmp_path / "m.jsonl"
    assert "No misses recorded" in run("misses", "--path", str(path))[1]
    seed(path)
    data = json.loads(run("misses", "--path", str(path), "--format", "json", "--top", "1")[1])
    assert data["total"] == 3 and len(data["top"]) == 1


def test_misses_uses_the_configured_path(tmp_path, monkeypatch):
    from bike_routing_agent.config import settings

    path = tmp_path / "m.jsonl"
    seed(path)
    monkeypatch.setattr(settings, "chat_miss_log_path", path)
    assert "3 message(s)" in run("misses")[1]


def test_misses_exports_unlabelled_cases_that_the_benchmark_refuses_until_judged(tmp_path):
    from bike_routing_agent.chat.evaluation import load_benchmark

    path, out_file = tmp_path / "m.jsonl", tmp_path / "cases" / "new.json"
    seed(path)
    rc, _, err = run("misses", "--path", str(path), "--export-cases", str(out_file))
    assert rc == 0 and "wrote 2 unlabelled case(s)" in err
    data = json.loads(out_file.read_text())
    assert [c["text"] for c in data["cases"]] == ["show me something nice", "make it purple"]
    assert out_file.read_text().endswith("\n")
    with pytest.raises(ValueError, match="unlabelled"):
        load_benchmark(out_file)  # a person has to say what it should have been


def test_misses_clear_deletes_the_log(tmp_path):
    path = tmp_path / "m.jsonl"
    seed(path)
    rc, out, _ = run("misses", "--path", str(path), "--clear")
    assert rc == 0 and "removed 1 file(s)" in out and not path.exists()
