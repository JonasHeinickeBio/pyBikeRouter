"""Retention pruning (issue #27) over the in-memory history and real/fake stores."""

import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("botocore")

from bike_routing_agent.storage.artifacts import LocalArtifactStore  # noqa: E402
from bike_routing_agent.storage.history import InMemoryRouteHistory, record_from_state  # noqa: E402
from bike_routing_agent.storage.retention import (  # noqa: E402
    RetentionBusy,
    RetentionPolicy,
    prune,
)
from bike_routing_agent.storage.s3 import S3ArtifactStore  # noqa: E402

from .fake_s3 import FakeS3Client  # noqa: E402
from .test_history import candidate, ready_state  # noqa: E402

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
POLICY = RetentionPolicy(max_age=timedelta(days=30), orphan_grace=timedelta(hours=24))


def name(n: int, ext: str = "geojson") -> str:
    return f"{n:032x}.{ext}"


def pid(n: int) -> str:
    return f"{n:032x}"


class Env:
    """A store + history pair with helpers to create aged data."""

    def __init__(self, store, put_aged, history):
        self.store = store
        self._put_aged = put_aged
        self.history = history

    def artifact(self, artifact_name: str, age: timedelta, content: str = "0123456789") -> None:
        self._put_aged(artifact_name, content, NOW - age)

    def plan(self, n: int, age_days: float, *artifact_names: str) -> None:
        state = ready_state([candidate()])
        state["artifacts"] = {f"f{i}": a for i, a in enumerate(artifact_names)}
        age = timedelta(days=age_days)
        for a in artifact_names:
            self.artifact(a, age)
        self.history.save(record_from_state(f"{n:032x}", state, created_at=NOW - age))

    def stored(self) -> set[str]:
        return {i.name for i in self.store.list_artifacts()}


@pytest.fixture(params=["local", "s3"])
def env(request, tmp_path):
    history = InMemoryRouteHistory()
    if request.param == "local":
        store = LocalArtifactStore(tmp_path)

        def put_aged(artifact_name, content, modified):
            store.put(artifact_name, content)
            os.utime(tmp_path / artifact_name, (modified.timestamp(), modified.timestamp()))

        return Env(store, put_aged, history)
    client = FakeS3Client()
    store = S3ArtifactStore(bucket="bucket", client=client)
    return Env(store, lambda n, c, m: client.seed(n, c, m), history)


def test_old_plans_and_only_their_artifacts_are_deleted(env):
    env.plan(1, 90, name(1), name(2, "gpx"))
    env.plan(2, 45, name(3))
    env.plan(3, 5, name(4))

    report = prune(env.history, env.store, POLICY, dry_run=False, now=NOW)

    assert (report.plans, report.plan_artifacts, report.orphans) == (2, 3, 0)
    assert report.bytes_freed == 30
    assert env.history.get(pid(1)) is None and env.history.get(pid(2)) is None
    assert env.history.get(pid(3)) is not None
    assert env.stored() == {name(4)}


def test_an_artifact_still_referenced_by_a_retained_plan_is_never_deleted(env):
    shared = name(7)
    env.plan(1, 90, shared, name(8))
    env.plan(2, 3, shared)

    report = prune(env.history, env.store, POLICY, dry_run=False, now=NOW)

    assert report.plans == 1
    assert env.stored() == {shared}
    assert report.plan_artifacts == 1  # only name(8)


def test_dry_run_reports_exactly_what_a_real_run_deletes_and_changes_nothing(env):
    env.plan(1, 90, name(1))
    env.plan(2, 60, name(2), name(3, "gpx"))
    env.plan(3, 2, name(4))
    env.artifact(name(9), timedelta(days=10))  # orphan older than the grace

    dry = prune(env.history, env.store, POLICY, dry_run=True, now=NOW)
    assert env.stored() == {name(1), name(2), name(3, "gpx"), name(4), name(9)}
    assert env.history.get(pid(1)) is not None

    real = prune(env.history, env.store, POLICY, dry_run=False, now=NOW)
    assert dry.dry_run is True and real.dry_run is False
    assert {k: v for k, v in dry.as_dict().items() if k != "dry_run"} == {
        k: v for k, v in real.as_dict().items() if k != "dry_run"
    }
    assert (real.plans, real.plan_artifacts, real.orphans) == (2, 3, 1)


def test_dry_run_is_the_default(env):
    env.plan(1, 90, name(1))
    report = prune(env.history, env.store, POLICY, now=NOW)
    assert report.dry_run and env.stored() == {name(1)}


def test_orphans_are_swept_only_after_the_grace_period(env):
    env.artifact(name(1), timedelta(hours=2))  # a fresh export whose plan may still be saving
    env.artifact(name(2), timedelta(hours=30))  # abandoned
    env.plan(3, 1, name(3))  # referenced -> untouched whatever its age

    report = prune(env.history, env.store, POLICY, dry_run=False, now=NOW)

    assert report.orphans == 1 and report.plans == 0
    assert env.stored() == {name(1), name(3)}


def test_batches_do_not_change_the_outcome(env):
    for n in range(1, 8):
        env.plan(n, 40 + n, name(n))
    env.plan(20, 1, name(20))
    small = RetentionPolicy(max_age=POLICY.max_age, batch_size=2)

    dry = prune(env.history, env.store, small, dry_run=True, now=NOW)
    real = prune(env.history, env.store, small, dry_run=False, now=NOW)

    assert dry.plans == real.plans == 7
    assert env.stored() == {name(20)}


def test_a_second_run_finds_nothing_to_do(env):
    env.plan(1, 90, name(1))
    prune(env.history, env.store, POLICY, dry_run=False, now=NOW)
    again = prune(env.history, env.store, POLICY, dry_run=False, now=NOW)
    assert (again.plans, again.artifacts, again.bytes_freed, again.errors) == (0, 0, 0, [])


def test_plans_without_artifacts_are_just_expired(env):
    env.plan(1, 90)
    report = prune(env.history, env.store, POLICY, dry_run=False, now=NOW)
    assert (report.plans, report.artifacts) == (1, 0)


def test_without_a_history_artifacts_expire_by_age_alone(env):
    env.artifact(name(1), timedelta(days=40))
    env.artifact(name(2), timedelta(days=5))

    report = prune(None, env.store, POLICY, dry_run=False, now=NOW)

    assert (report.plans, report.orphans) == (0, 1)
    assert env.stored() == {name(2)}


def test_a_busy_lock_aborts_before_anything_is_touched(env):
    class Busy(InMemoryRouteHistory):
        @contextmanager
        def maintenance_lock(self):
            yield False

    busy = Busy()
    env.history = busy
    env.plan(1, 90, name(1))

    with pytest.raises(RetentionBusy):
        prune(busy, env.store, POLICY, dry_run=False, now=NOW)
    assert busy.get(pid(1)) is not None and env.stored() == {name(1)}


def test_a_failing_delete_is_reported_and_does_not_stop_the_run(env, monkeypatch):
    env.plan(1, 90, name(1))
    env.plan(2, 80, name(2))
    real_delete = env.store.delete

    def flaky(artifact_name):
        if artifact_name == name(1):
            raise OSError("disk says no")
        return real_delete(artifact_name)

    monkeypatch.setattr(env.store, "delete", flaky)

    report = prune(env.history, env.store, POLICY, dry_run=False, now=NOW)

    assert report.plans == 2 and report.plan_artifacts == 1
    assert report.errors == [f"{name(1)}: OSError"]
    assert report.bytes_freed == 10  # only what was actually freed
    # the plan is gone, the artifact is now an orphan the next run sweeps
    assert env.stored() == {name(1)}
    monkeypatch.setattr(env.store, "delete", real_delete)
    sweep = prune(env.history, env.store, POLICY, dry_run=False, now=NOW + timedelta(days=2))
    assert sweep.orphans == 1 and env.stored() == set()


def test_the_report_serialises_for_the_cli(env):
    env.plan(1, 90, name(1))
    data = prune(env.history, env.store, POLICY, now=NOW).as_dict()
    assert data["dry_run"] is True and data["artifacts"] == 1
    assert data["cutoff"] == (NOW - timedelta(days=30)).isoformat()
