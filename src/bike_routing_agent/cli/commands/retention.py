"""``bike-router retention`` group: expire old plans and their artifacts (issue #27)."""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from typing import IO


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    retention = subparsers.add_parser(
        "retention", help="expire old route history and exported artifacts"
    )
    retention_sub = retention.add_subparsers(dest="command", metavar="<command>")
    if retention_sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")

    prune = retention_sub.add_parser(
        "prune",
        help="delete plans (and the artifacts only they reference) older than the retention age",
        description=(
            "Report what would be deleted, or delete it with --execute. The age comes from "
            "RETENTION_MAX_AGE_DAYS or --max-age-days; with neither, retention is off and "
            "nothing is touched. Safe to run from cron or a Kubernetes CronJob: concurrent "
            "runs are serialised with a database advisory lock."
        ),
    )
    prune.add_argument(
        "--execute", action="store_true", help="actually delete (default: dry run, nothing changes)"
    )
    prune.add_argument("--max-age-days", type=int, help="override RETENTION_MAX_AGE_DAYS")
    prune.add_argument(
        "--orphan-grace-hours",
        type=int,
        help="override RETENTION_ORPHAN_GRACE_HOURS (younger unreferenced artifacts stay)",
    )
    prune.add_argument("--batch-size", type=int, help="override RETENTION_BATCH_SIZE")
    return retention


def run(args: argparse.Namespace, stdout: IO[str], stderr: IO[str]) -> int:
    from bike_routing_agent.config import Settings

    if args.command != "prune":
        print("unknown retention command", file=stderr)
        return 2

    cfg = Settings()
    max_age_days = (
        args.max_age_days if args.max_age_days is not None else cfg.retention_max_age_days
    )
    if max_age_days is None:
        print(
            "retention is off: set RETENTION_MAX_AGE_DAYS or pass --max-age-days "
            "(nothing was touched)",
            file=stderr,
        )
        return 2
    grace = (
        args.orphan_grace_hours
        if args.orphan_grace_hours is not None
        else cfg.retention_orphan_grace_hours
    )
    batch = args.batch_size if args.batch_size is not None else cfg.retention_batch_size
    if max_age_days < 1 or grace < 0 or batch < 1:
        print(
            "invalid values: max age >= 1 day, orphan grace >= 0 hours, batch size >= 1",
            file=stderr,
        )
        return 2

    from bike_routing_agent.api import build_storage
    from bike_routing_agent.storage.retention import RetentionBusy, RetentionPolicy, prune

    policy = RetentionPolicy(
        max_age=timedelta(days=max_age_days),
        orphan_grace=timedelta(hours=grace),
        batch_size=batch,
    )
    try:
        store, history = build_storage(cfg)
        report = prune(history, store, policy, dry_run=not args.execute)
    except RetentionBusy as exc:
        print(f"{exc}; nothing was deleted by this run", file=stderr)
        return 1
    except Exception as exc:
        print(f"retention failed: {type(exc).__name__}: {exc}", file=stderr)
        return 1

    print(json.dumps(report.as_dict(), indent=2), file=stdout)
    if report.dry_run:
        print("dry run: nothing was deleted (pass --execute to delete)", file=stderr)
    return 1 if report.errors else 0
