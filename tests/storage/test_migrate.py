"""Migration discovery and consistency rules (issue #28); no database needed."""

import pytest

from bike_routing_agent.storage.migrate import (
    Migration,
    MigrationError,
    MigrationStatus,
    _checksum,
    load_migrations,
    pending_migrations,
)


def write(directory, name, sql="SELECT 1;"):
    (directory / name).write_text(sql)


def make(version, sql="SELECT 1;"):
    return Migration(version, f"m{version}", sql, _checksum(sql))


def test_the_packaged_migrations_start_with_the_initial_schema():
    migrations = load_migrations()
    assert [m.version for m in migrations][0] == 1
    initial = migrations[0]
    assert initial.name == "initial"
    for table in ("plans", "candidates", "artifacts"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in initial.sql
    # databases created before migrations existed are adopted by re-running it
    assert "CREATE TABLE plans" not in initial.sql


def test_migrations_load_in_version_order_with_checksums(tmp_path):
    write(tmp_path, "0002_add_note.sql", "ALTER TABLE plans ADD COLUMN note text;")
    write(tmp_path, "0001_initial.sql")
    migrations = load_migrations(tmp_path)
    assert [(m.version, m.name) for m in migrations] == [(1, "initial"), (2, "add_note")]
    assert len({m.checksum for m in migrations}) == 2


def test_line_endings_do_not_change_the_checksum(tmp_path):
    assert _checksum("SELECT 1;\nSELECT 2;\n") == _checksum("SELECT 1;\r\nSELECT 2;\r\n")


@pytest.mark.parametrize(
    "name", ["initial.sql", "1_initial.sql", "0001-initial.sql", "0001_Init.sql"]
)
def test_badly_named_files_are_rejected(tmp_path, name):
    write(tmp_path, name)
    with pytest.raises(MigrationError, match="must be named"):
        load_migrations(tmp_path)


def test_gaps_are_rejected_because_skipping_a_change_is_silent_corruption(tmp_path):
    write(tmp_path, "0001_a.sql")
    write(tmp_path, "0003_c.sql")
    with pytest.raises(MigrationError, match="contiguous"):
        load_migrations(tmp_path)


def test_duplicate_versions_are_rejected(tmp_path):
    write(tmp_path, "0001_a.sql")
    write(tmp_path, "0001_b.sql")
    with pytest.raises(MigrationError, match="contiguous"):
        load_migrations(tmp_path)


def test_non_sql_files_are_ignored(tmp_path):
    write(tmp_path, "0001_a.sql")
    write(tmp_path, "README.md", "notes")
    assert [m.version for m in load_migrations(tmp_path)] == [1]


def test_pending_is_everything_not_yet_applied_in_order():
    migrations = [make(1), make(2), make(3)]
    assert [m.version for m in pending_migrations({}, migrations)] == [1, 2, 3]
    applied = {1: migrations[0].checksum}
    assert [m.version for m in pending_migrations(applied, migrations)] == [2, 3]
    full = {m.version: m.checksum for m in migrations}
    assert pending_migrations(full, migrations) == []


def test_an_edited_applied_migration_is_refused():
    migrations = [make(1, "SELECT 1;")]
    with pytest.raises(MigrationError, match="edited after it was applied"):
        pending_migrations({1: _checksum("SELECT 'what was applied';")}, migrations)


def test_a_database_newer_than_the_code_is_refused():
    migrations = [make(1)]
    applied = {1: migrations[0].checksum, 2: "abc"}
    with pytest.raises(MigrationError, match="newer schema"):
        pending_migrations(applied, migrations)


def test_status_serialises_for_the_cli():
    status = MigrationStatus(
        current=1, applied=[(1, "initial")], pending=[(2, "add_note")], applied_now=[]
    )
    assert status.as_dict() == {
        "current": 1,
        "latest": 2,
        "up_to_date": False,
        "applied": [{"version": 1, "name": "initial"}],
        "pending": [{"version": 2, "name": "add_note"}],
        "applied_now": [],
    }
