"""``bike-router retention prune`` (issue #27)."""

import io
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from storage.test_history import candidate, ready_state

from bike_routing_agent.cli import main
from bike_routing_agent.storage.artifacts import LocalArtifactStore
from bike_routing_agent.storage.history import InMemoryRouteHistory, record_from_state


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(["retention", *argv], stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def stack(tmp_path, monkeypatch):
    """A history + local store wired in place of the configured storage."""
    history = InMemoryRouteHistory()
    store = LocalArtifactStore(tmp_path)
    monkeypatch.setattr("bike_routing_agent.api.build_storage", lambda cfg: (store, history))
    for var in ("RETENTION_MAX_AGE_DAYS", "RETENTION_ORPHAN_GRACE_HOURS", "RETENTION_BATCH_SIZE"):
        monkeypatch.delenv(var, raising=False)

    def add_plan(n: int, age_days: int):
        name = f"{n:032x}.geojson"
        store.put(name, "0123456789")
        old = (datetime.now(UTC) - timedelta(days=age_days)).timestamp()
        os.utime(tmp_path / name, (old, old))
        state = ready_state([candidate()])
        state["artifacts"] = {"geojson_file": name}
        history.save(
            record_from_state(
                f"{n:032x}", state, created_at=datetime.now(UTC) - timedelta(days=age_days)
            )
        )

    add_plan(1, 90)
    add_plan(2, 2)
    return history, store, tmp_path


def test_retention_off_touches_nothing_and_exits_2(stack):
    history, store, _ = stack
    code, out, err = run_cli("prune")
    assert code == 2 and "retention is off" in err and out == ""
    assert len(list(store.list_artifacts())) == 2


def test_default_is_a_dry_run(stack, monkeypatch):
    history, store, _ = stack
    monkeypatch.setenv("RETENTION_MAX_AGE_DAYS", "30")

    code, out, err = run_cli("prune")

    report = json.loads(out)
    assert code == 0 and report["dry_run"] is True
    assert (report["plans"], report["artifacts"], report["bytes_freed"]) == (1, 1, 10)
    assert "dry run" in err
    assert len(list(store.list_artifacts())) == 2


def test_execute_deletes_and_the_flag_overrides_the_environment(stack, monkeypatch):
    history, store, _ = stack
    monkeypatch.setenv("RETENTION_MAX_AGE_DAYS", "1000")  # would delete nothing

    code, out, _ = run_cli("prune", "--execute", "--max-age-days", "30")

    assert code == 0 and json.loads(out)["dry_run"] is False
    assert [i.name for i in store.list_artifacts()] == [f"{2:032x}.geojson"]
    assert history.get(f"{1:032x}") is None


def test_invalid_values_are_a_usage_error(stack):
    code, _, err = run_cli("prune", "--max-age-days", "0")
    assert code == 2 and "invalid values" in err
    code, _, err = run_cli("prune", "--max-age-days", "5", "--batch-size", "0")
    assert code == 2 and "invalid values" in err


def test_a_busy_lock_exits_1_without_deleting(stack, monkeypatch):
    history, store, _ = stack

    @contextmanager
    def busy():
        yield False

    monkeypatch.setattr(history, "maintenance_lock", busy)
    code, out, err = run_cli("prune", "--execute", "--max-age-days", "30")
    assert code == 1 and "in progress" in err and out == ""
    assert len(list(store.list_artifacts())) == 2


def test_partial_failures_exit_1_but_still_print_the_report(stack, monkeypatch):
    history, store, _ = stack

    def broken(name):
        raise OSError("nope")

    monkeypatch.setattr(store, "delete", broken)
    code, out, _ = run_cli("prune", "--execute", "--max-age-days", "30")
    assert code == 1 and json.loads(out)["errors"]


def test_a_storage_failure_is_reported_not_raised(monkeypatch):
    def boom(cfg):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr("bike_routing_agent.api.build_storage", boom)
    monkeypatch.delenv("RETENTION_MAX_AGE_DAYS", raising=False)
    code, _, err = run_cli("prune", "--max-age-days", "30")
    assert code == 1 and "retention failed: RuntimeError: database unreachable" in err


def test_without_a_command_the_group_help_is_shown():
    out, err = io.StringIO(), io.StringIO()
    assert main(["retention"], stdout=out, stderr=err) == 2
    assert "prune" in out.getvalue()
