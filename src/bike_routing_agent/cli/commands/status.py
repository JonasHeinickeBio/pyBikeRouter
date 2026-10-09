"""``bike-router status`` group: is this deployment ready, and what can it do.

The CLI counterpart of ``GET /readyz`` and ``GET /v1/capabilities``: each component (routing
engines, geocoder, artifact store, weather, database, cache) probed with its time bound, and the
optional features that are switched on.
"""

from __future__ import annotations

import argparse
import json
from typing import IO

from bike_routing_agent.cli._handlers import (
    EXIT_FAILURE,
    EXIT_OK,
    EXIT_USAGE,
    call_handler,
    dump_json,
)
from bike_routing_agent.cli.render import status_text


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    group = subparsers.add_parser("status", help="readiness of the services and active features")
    sub = group.add_subparsers(dest="command", metavar="<command>")
    if sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")
    show = sub.add_parser("show", help="probe every component; exit 0 when ready, 1 when not")
    show.add_argument("--format", choices=("text", "json"), default="text")
    return group


def run(args: argparse.Namespace, stdout: IO[str], stderr: IO[str]) -> int:
    from bike_routing_agent import api

    if args.command != "show":
        print("unknown status command", file=stderr)
        return EXIT_USAGE
    response, code = call_handler(api.readyz, stderr)
    caps, caps_code = call_handler(api.capabilities, stderr)
    if response is None or caps is None:
        return code or caps_code or EXIT_FAILURE
    report = json.loads(bytes(response.body))
    if args.format == "json":
        dump_json({"readiness": report, "capabilities": caps}, stdout)
    else:
        print(status_text(report, caps), file=stdout)
    return EXIT_OK if report.get("ready") else EXIT_FAILURE
