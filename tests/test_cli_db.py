"""``bike-router db migrate|status`` (issue #28)."""

import io
import json

import pytest

from bike_routing_agent.cli import main
from bike_routing_agent.storage.migrate import MigrationError, MigrationStatus


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(["db", *argv], stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


class FakeDatabase:
    instances: list["FakeDatabase"] = []
    status = MigrationStatus(current=1, applied=[(1, "initial")])
    error: Exception | None = None

    def __init__(self, url, *, max_size=5, auto_migrate=True):
        self.url, self.auto_migrate = url, auto_migrate
        FakeDatabase.instances.append(self)

    def migrate(self):
        if self.error:
            raise self.error
        return MigrationStatus(current=1, applied=[(1, "initial")], applied_now=[1])

    def migration_status(self):
        if self.error:
            raise self.error
        return self.status


@pytest.fixture
def fake_db(monkeypatch):
    FakeDatabase.instances = []
    FakeDatabase.status = MigrationStatus(current=1, applied=[(1, "initial")])
    FakeDatabase.error = None
    monkeypatch.setattr("bike_routing_agent.storage.postgres.PostgresDatabase", FakeDatabase)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/x")
    return FakeDatabase


def test_without_a_database_nothing_is_attempted(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    code, out, err = run_cli("migrate")
    assert code == 2 and "no database configured" in err and out == ""


def test_migrate_reports_what_it_applied(fake_db):
    code, out, _ = run_cli("migrate")
    report = json.loads(out)
    assert code == 0 and report["applied_now"] == [1] and report["up_to_date"] is True
    # the explicit command must migrate even when AUTO_MIGRATE is off
    assert fake_db.instances[0].auto_migrate is False


def test_status_exits_zero_when_up_to_date(fake_db):
    code, out, _ = run_cli("status")
    assert code == 0 and json.loads(out)["up_to_date"] is True


def test_status_exits_one_when_migrations_are_pending(fake_db):
    fake_db.status = MigrationStatus(current=1, applied=[(1, "initial")], pending=[(2, "x")])
    code, out, _ = run_cli("status")
    assert code == 1 and json.loads(out)["pending"] == [{"version": 2, "name": "x"}]


def test_a_refused_migration_is_a_clear_error(fake_db):
    fake_db.error = MigrationError("migration 0001 was edited after it was applied")
    code, out, err = run_cli("migrate")
    assert code == 1 and out == "" and "migration refused: migration 0001 was edited" in err


def test_a_database_failure_is_reported_not_raised(fake_db):
    fake_db.error = ConnectionError("connection refused")
    code, _, err = run_cli("status")
    assert code == 1 and "database error: ConnectionError: connection refused" in err


def test_without_a_command_the_group_help_is_shown():
    out, err = io.StringIO(), io.StringIO()
    assert main(["db"], stdout=out, stderr=err) == 2
    assert "migrate" in out.getvalue() and "status" in out.getvalue()
