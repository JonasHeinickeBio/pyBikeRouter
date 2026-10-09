"""``bike-router history`` group: past plans recorded in the PostGIS route history.

Needs ``DATABASE_URL`` (docs/persistence.md). The same queries as ``/v1/history/*``.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime
from typing import IO

from bike_routing_agent.cli._handlers import (
    EXIT_FAILURE,
    EXIT_OK,
    EXIT_USAGE,
    call_handler,
    dump_json,
    to_plain,
)
from bike_routing_agent.cli.render import km, table

STATUSES = ("ready", "awaiting_clarification", "invalid", "provider_failure", "no_route")


def _when(text: str) -> datetime:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not an ISO-8601 date or datetime") from None


def _bounded(name: str, low: int, high: int | None) -> Callable[[str], int]:
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from None
        if value < low or (high is not None and value > high):
            raise argparse.ArgumentTypeError(
                f"{name} must be between {low} and {high}"
                if high is not None
                else f"{name} must be at least {low}"
            )
        return value

    return parse


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    group = subparsers.add_parser("history", help="past plans from the route history")
    sub = group.add_subparsers(dest="command", metavar="<command>")
    if sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")

    ls = sub.add_parser("list", help="past plans, newest first")
    ls.add_argument("--provider")
    ls.add_argument("--profile")
    ls.add_argument("--status", choices=STATUSES)
    ls.add_argument("--bike-type")
    ls.add_argument("--selected-only", action="store_true")
    ls.add_argument("--since", type=_when, metavar="ISO8601")
    ls.add_argument("--until", type=_when, metavar="ISO8601")
    ls.add_argument("--bbox", metavar="W,S,E,N", help="min_lon,min_lat,max_lon,max_lat")
    ls.add_argument("--limit", type=_bounded("limit", 1, 200), default=20)
    ls.add_argument("--offset", type=_bounded("offset", 0, None), default=0)
    ls.add_argument("--format", choices=("text", "json"), default="text")

    show = sub.add_parser("show", help="one plan with its request, candidates and errors (JSON)")
    show.add_argument("plan_id", metavar="PLAN_ID")

    stats = sub.add_parser("stats", help="evaluation aggregates over the recorded plans")
    stats.add_argument("--bike-type")
    stats.add_argument("--since", type=_when, metavar="ISO8601")
    stats.add_argument("--until", type=_when, metavar="ISO8601")
    stats.add_argument("--format", choices=("text", "json"), default="text")
    return group


def run(args: argparse.Namespace, stdout: IO[str], stderr: IO[str]) -> int:
    from bike_routing_agent import api

    if args.command == "list":
        listed, code = call_handler(
            lambda: api.list_history(
                provider=args.provider,
                profile=args.profile,
                status=args.status,
                bike_type=args.bike_type,
                selected_only=args.selected_only,
                since=args.since,
                until=args.until,
                bbox=args.bbox,
                limit=args.limit,
                offset=args.offset,
            ),
            stderr,
        )
        if listed is None:
            return code or EXIT_FAILURE
        plans = to_plain(listed)
        if args.format == "json":
            dump_json(plans, stdout)
            return EXIT_OK
        if not plans:
            print("no plans recorded for these filters", file=stdout)
            return EXIT_OK
        rows = []
        for p in plans:
            best = (p.get("candidates") or [{}])[0]
            rows.append(
                [
                    p["plan_id"],
                    str(p["created_at"])[:19].replace("T", " "),
                    p["status"],
                    str(p.get("bike_type") or "-"),
                    str(best.get("provider") or "-"),
                    str(best.get("profile") or best.get("provider_profile") or "-"),
                    km(best.get("distance_m")),
                ]
            )
        print(
            table(rows, ["PLAN", "WHEN", "STATUS", "BIKE", "ENGINE", "PROFILE", "DISTANCE"]),
            file=stdout,
        )
        return EXIT_OK

    if args.command == "show":
        record, code = call_handler(lambda: api.get_history_plan(args.plan_id), stderr)
        if record is None:
            return code or EXIT_FAILURE
        dump_json(record, stdout)
        return EXIT_OK

    if args.command == "stats":
        stats, code = call_handler(
            lambda: api.history_stats(args.bike_type, args.since, args.until), stderr
        )
        if stats is None:
            return code or EXIT_FAILURE
        data = to_plain(stats)
        if args.format == "json":
            dump_json(data, stdout)
            return EXIT_OK
        rate = data.get("ready_rate")
        print(
            f"{data['total_plans']} plans, "
            + ("ready rate n/a" if rate is None else f"{100 * rate:.0f} % ready")
            + "  "
            + ", ".join(f"{k}: {v}" for k, v in sorted(data["by_status"].items())),
            file=stdout,
        )
        providers = data.get("providers") or []
        if providers:
            print("", file=stdout)
            print(
                table(
                    [[str(p.get(k, "-")) for k in providers[0]] for p in providers],
                    [k.upper() for k in providers[0]],
                ),
                file=stdout,
            )
        return EXIT_OK

    print("unknown history command", file=stderr)
    return EXIT_USAGE
