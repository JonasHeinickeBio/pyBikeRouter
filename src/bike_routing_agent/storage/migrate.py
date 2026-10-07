"""Schema migrations for the PostGIS backend (issue #28).

Plain, ordered SQL files (``storage/migrations/NNNN_name.sql``) applied by a
small runner -- no ORM, no extra dependency, every change reviewable as the
SQL it is. The runner keeps a ``schema_migrations`` table and:

* applies pending files in order, each in its own transaction together with
  the bookkeeping row (a failed migration leaves the database at the previous
  version);
* takes the maintenance advisory lock for the whole run, so several instances
  starting at once serialise (the others wait, then find nothing to do) and
  never race ``CREATE`` statements, and a retention prune never runs against a
  half-migrated schema;
* refuses to continue when an applied migration's file has been edited
  (checksum) or the database is *newer* than the code (a downgrade): both are
  situations where guessing would corrupt data.

``0001_initial.sql`` is idempotent, so databases created before migrations
existed are adopted by simply running it and recording version 1.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

MIGRATION_FILE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     integer PRIMARY KEY,
    name        text NOT NULL,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """The migration state cannot be reconciled safely."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


@dataclass
class MigrationStatus:
    """Where a database stands relative to the migrations in the code."""

    current: int
    applied: list[tuple[int, str]] = field(default_factory=list)
    pending: list[tuple[int, str]] = field(default_factory=list)
    # Versions applied by *this* run (empty for a status check).
    applied_now: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "current": self.current,
            "latest": self.current + len(self.pending),
            "up_to_date": not self.pending,
            "applied": [{"version": v, "name": n} for v, n in self.applied],
            "pending": [{"version": v, "name": n} for v, n in self.pending],
            "applied_now": list(self.applied_now),
        }


def _checksum(sql: str) -> str:
    # Normalise line endings so a checkout with CRLF does not look "edited".
    return hashlib.sha256(sql.replace("\r\n", "\n").encode()).hexdigest()


def load_migrations(directory: Path | None = None) -> list[Migration]:
    """The migrations in ``directory`` (default: the packaged ones), in order.

    Versions must be unique and contiguous from 1: a gap means a file is
    missing, and applying "the rest" would silently skip a schema change.
    """
    if directory is None:
        entries = [
            (entry.name, entry.read_text(encoding="utf-8"))
            for entry in resources.files("bike_routing_agent.storage.migrations").iterdir()
            if entry.name.endswith(".sql")
        ]
    else:
        entries = [(p.name, p.read_text(encoding="utf-8")) for p in directory.glob("*.sql")]

    migrations: list[Migration] = []
    for filename, sql in entries:
        match = MIGRATION_FILE.match(filename)
        if match is None:
            raise MigrationError(
                f"migration file {filename!r} must be named NNNN_name.sql "
                "(four digits, lowercase letters, digits and underscores)"
            )
        migrations.append(Migration(int(match.group(1)), match.group(2), sql, _checksum(sql)))

    migrations.sort(key=lambda m: m.version)
    versions = [m.version for m in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(
            f"migration versions must be unique and contiguous from 1, found {versions}"
        )
    return migrations


def pending_migrations(
    applied: Mapping[int, str], migrations: Sequence[Migration]
) -> list[Migration]:
    """Migrations still to apply, after checking the history is consistent.

    ``applied`` maps version -> checksum as recorded in the database.
    """
    known = {m.version: m for m in migrations}
    latest = max(known, default=0)
    newer = sorted(v for v in applied if v > latest)
    if newer:
        raise MigrationError(
            f"the database is at schema version {max(newer)} but this code only knows "
            f"versions up to {latest}: refusing to run an older release against a "
            "newer schema"
        )
    for version, recorded in sorted(applied.items()):
        migration = known.get(version)
        if migration is None or migration.checksum != recorded:
            raise MigrationError(
                f"migration {version:04d} was edited after it was applied "
                "(checksum differs); never change an applied migration, add a new one"
            )
    return [m for m in migrations if m.version not in applied]


def _applied(conn: Any) -> dict[int, str]:
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    return {int(version): str(checksum) for version, checksum in rows}


def read_status(conn: Any, migrations: Sequence[Migration] | None = None) -> MigrationStatus:
    """Status without writing anything (a missing bookkeeping table = nothing applied)."""
    migrations = load_migrations() if migrations is None else migrations
    exists = conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL").fetchone()[0]
    applied = _applied(conn) if exists else {}
    todo = pending_migrations(applied, migrations)
    return _status(migrations, applied, todo, [])


def _status(
    migrations: Sequence[Migration],
    applied: Mapping[int, str],
    todo: Sequence[Migration],
    applied_now: list[int],
) -> MigrationStatus:
    done = [(m.version, m.name) for m in migrations if m.version in applied]
    done += [(m.version, m.name) for m in todo if m.version in applied_now]
    done.sort()
    return MigrationStatus(
        current=max((v for v, _ in done), default=0),
        applied=done,
        pending=[(m.version, m.name) for m in todo if m.version not in applied_now],
        applied_now=applied_now,
    )


def apply_pending(
    conn: Any, lock_key: int, migrations: Sequence[Migration] | None = None
) -> MigrationStatus:
    """Bring the database up to date.

    ``conn`` must be an *autocommit* psycopg connection: the advisory lock is a
    session lock held across the statements below, and each migration opens its
    own explicit transaction.
    """
    migrations = load_migrations() if migrations is None else migrations
    conn.execute("SELECT pg_advisory_lock(%s)", (lock_key,))
    try:
        conn.execute(SCHEMA_MIGRATIONS_DDL)
        applied = _applied(conn)
        todo = pending_migrations(applied, migrations)
        done_now: list[int] = []
        for migration in todo:
            with conn.transaction():
                conn.execute(migration.sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                    (migration.version, migration.name, migration.checksum),
                )
            done_now.append(migration.version)
        return _status(migrations, applied, todo, done_now)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
