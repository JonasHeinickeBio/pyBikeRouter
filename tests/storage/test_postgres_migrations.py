"""Migrations against a real PostGIS (live tier, needs TEST_DATABASE_URL), issue #28."""

import threading
import time

import pytest

pytestmark = pytest.mark.live

psycopg = pytest.importorskip("psycopg")

from bike_routing_agent.storage.migrate import (  # noqa: E402
    Migration,
    MigrationError,
    _checksum,
    apply_pending,
    load_migrations,
)
from bike_routing_agent.storage.postgres import (  # noqa: E402
    MAINTENANCE_LOCK_KEY,
    PostgresDatabase,
    PostgresRouteHistory,
)


def tables(url):
    with psycopg.connect(url, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
    return {r[0] for r in rows}


def versions(url):
    with psycopg.connect(url, autocommit=True) as conn:
        return conn.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()


def extra(version, sql):
    return Migration(version, f"extra_{version}", sql, _checksum(sql))


def test_a_fresh_database_gets_the_schema_and_version_one(empty_database_url):
    status = PostgresDatabase(empty_database_url, auto_migrate=False).migrate()

    assert {"plans", "candidates", "artifacts", "schema_migrations"} <= tables(empty_database_url)
    assert status.applied_now == [1] and status.current == 1 and not status.pending
    (row,) = versions(empty_database_url)
    assert row[:2] == (1, "initial") and row[2] == load_migrations()[0].checksum


def test_migrating_again_changes_nothing(empty_database_url):
    database = PostgresDatabase(empty_database_url, auto_migrate=False)
    database.migrate()
    again = database.migrate()
    assert again.applied_now == [] and again.current == 1
    assert len(versions(empty_database_url)) == 1
    assert database.migration_status().pending == []


def test_a_database_created_before_migrations_existed_is_adopted_with_its_data(
    empty_database_url,
):
    # the pre-#28 world: the schema exists, there is no schema_migrations table
    initial = load_migrations()[0].sql
    with psycopg.connect(empty_database_url, autocommit=True) as conn:
        conn.execute(initial)
        conn.execute(
            "INSERT INTO plans (plan_id, created_at, status, request, constraints) "
            "VALUES ('old', now(), 'ready', '{}', '{}')"
        )
    assert "schema_migrations" not in tables(empty_database_url)

    status = PostgresDatabase(empty_database_url, auto_migrate=False).migrate()

    assert status.applied_now == [1]
    with psycopg.connect(empty_database_url, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM plans WHERE plan_id = 'old'").fetchone()[0] == 1


def test_status_does_not_write_anything(empty_database_url):
    status = PostgresDatabase(empty_database_url, auto_migrate=False).migration_status()
    assert status.current == 0 and [v for v, _ in status.pending] == [1]
    assert "schema_migrations" not in tables(empty_database_url)
    assert "plans" not in tables(empty_database_url)


def test_pending_migrations_apply_in_order_and_are_recorded(empty_database_url):
    migrations = [
        *load_migrations(),
        extra(2, "ALTER TABLE plans ADD COLUMN note text;"),
        extra(3, "CREATE INDEX plans_note_idx ON plans (note);"),
    ]
    with psycopg.connect(empty_database_url, autocommit=True) as conn:
        first = apply_pending(conn, MAINTENANCE_LOCK_KEY, migrations[:1])
        second = apply_pending(conn, MAINTENANCE_LOCK_KEY, migrations)
    assert first.applied_now == [1] and second.applied_now == [2, 3]
    assert [v for v, *_ in versions(empty_database_url)] == [1, 2, 3]
    with psycopg.connect(empty_database_url, autocommit=True) as conn:
        conn.execute("SELECT note FROM plans LIMIT 0")  # the column exists


def test_a_failing_migration_is_atomic_and_stops_the_run(empty_database_url):
    migrations = [
        *load_migrations(),
        extra(2, "ALTER TABLE plans ADD COLUMN good text;"),
        # first statement succeeds, second fails: neither may stay applied
        extra(3, "ALTER TABLE plans ADD COLUMN half text; SELECT * FROM does_not_exist;"),
        extra(4, "ALTER TABLE plans ADD COLUMN never text;"),
    ]
    with psycopg.connect(empty_database_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.UndefinedTable):
            apply_pending(conn, MAINTENANCE_LOCK_KEY, migrations)
        columns = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'plans'"
            ).fetchall()
        }
    # the lock was released despite the failure: a *different* session can take
    # it (the same session always could, advisory locks are re-entrant)
    with psycopg.connect(empty_database_url, autocommit=True) as other:
        row = other.execute("SELECT pg_try_advisory_lock(%s)", (MAINTENANCE_LOCK_KEY,)).fetchone()
        assert row is not None and row[0] is True
    assert "good" in columns and "half" not in columns and "never" not in columns
    assert [v for v, *_ in versions(empty_database_url)] == [1, 2]


def test_an_edited_applied_migration_is_refused(empty_database_url):
    PostgresDatabase(empty_database_url, auto_migrate=False).migrate()
    edited = [Migration(1, "initial", "SELECT 'something else';", _checksum("SELECT 'x';"))]
    with psycopg.connect(empty_database_url, autocommit=True) as conn:
        with pytest.raises(MigrationError, match="edited after it was applied"):
            apply_pending(conn, MAINTENANCE_LOCK_KEY, edited)


def test_a_newer_database_than_the_code_is_refused(empty_database_url):
    PostgresDatabase(empty_database_url, auto_migrate=False).migrate()
    with psycopg.connect(empty_database_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO schema_migrations (version, name, checksum) VALUES (99, 'future', 'x')"
        )
    with pytest.raises(MigrationError, match="newer schema"):
        PostgresDatabase(empty_database_url, auto_migrate=False).migration_status()


def test_instances_starting_at_once_end_with_one_consistent_schema(empty_database_url):
    results: list = []
    errors: list = []
    barrier = threading.Barrier(8)

    def start():
        try:
            barrier.wait()
            results.append(PostgresDatabase(empty_database_url, auto_migrate=False).migrate())
        except Exception as exc:  # pragma: no cover - the failure we are guarding against
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == []
    assert len(results) == 8 and all(r.current == 1 for r in results)
    # exactly one of them did the work, the others found nothing to do
    assert sum(len(r.applied_now) for r in results) == 1
    assert len(versions(empty_database_url)) == 1


def test_auto_migrate_creates_the_schema_on_first_use(empty_database_url):
    database = PostgresDatabase(empty_database_url, max_size=2)  # auto_migrate defaults on
    with database.connection() as conn:
        conn.execute("SELECT 1 FROM plans LIMIT 0")
    database.close()
    assert len(versions(empty_database_url)) == 1


def test_auto_migrate_off_leaves_the_schema_alone_and_warns(empty_database_url, caplog):
    database = PostgresDatabase(empty_database_url, max_size=2, auto_migrate=False)
    with caplog.at_level("WARNING"), database.connection():
        pass
    database.close()
    assert "plans" not in tables(empty_database_url)
    assert "pending migration" in caplog.text and "bike-router db migrate" in caplog.text


def test_a_migration_waits_for_a_running_maintenance_job(empty_database_url):
    """Startup must not migrate underneath a retention prune holding the lock."""
    PostgresDatabase(empty_database_url, auto_migrate=False).migrate()
    holder = PostgresRouteHistory(PostgresDatabase(empty_database_url, max_size=2))
    finished = threading.Event()

    def migrate():
        PostgresDatabase(empty_database_url, auto_migrate=False).migrate()
        finished.set()

    with holder.maintenance_lock() as acquired:
        assert acquired
        thread = threading.Thread(target=migrate)
        thread.start()
        time.sleep(0.7)
        assert not finished.is_set()  # blocked on the lock
    thread.join(timeout=30)
    assert finished.is_set()
