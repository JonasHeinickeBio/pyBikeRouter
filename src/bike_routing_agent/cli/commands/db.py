"""``bike-router db`` group: schema migrations for the PostGIS backend (issue #28)."""

from __future__ import annotations

import argparse
import json
from typing import IO


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    db = subparsers.add_parser("db", help="manage the database schema (PostGIS history)")
    db_sub = db.add_subparsers(dest="command", metavar="<command>")
    if db_sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")

    db_sub.add_parser(
        "migrate",
        help="apply pending schema migrations",
        description=(
            "Apply pending migrations to DATABASE_URL. Safe to run from several places at "
            "once (an advisory lock serialises them) and idempotent. Needed in a release step "
            "when AUTO_MIGRATE is off; otherwise the app does it on first use."
        ),
    )
    db_sub.add_parser(
        "status",
        help="show applied and pending migrations (exit 1 when some are pending)",
    )
    return db


def run(args: argparse.Namespace, stdout: IO[str], stderr: IO[str]) -> int:
    from bike_routing_agent.config import Settings

    if args.command not in ("migrate", "status"):
        print("unknown db command", file=stderr)
        return 2

    cfg = Settings()
    if not cfg.database_url:
        print("no database configured: set DATABASE_URL (nothing was changed)", file=stderr)
        return 2

    from bike_routing_agent.storage.migrate import MigrationError
    from bike_routing_agent.storage.postgres import PostgresDatabase

    # auto_migrate is irrelevant here: this command is the explicit migration.
    database = PostgresDatabase(cfg.database_url, max_size=1, auto_migrate=False)
    try:
        status = database.migrate() if args.command == "migrate" else database.migration_status()
    except MigrationError as exc:
        print(f"migration refused: {exc}", file=stderr)
        return 1
    except Exception as exc:
        print(f"database error: {type(exc).__name__}: {exc}", file=stderr)
        return 1

    print(json.dumps(status.as_dict(), indent=2), file=stdout)
    return 1 if args.command == "status" and status.pending else 0
