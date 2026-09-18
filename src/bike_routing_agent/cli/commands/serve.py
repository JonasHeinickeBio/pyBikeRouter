"""``bike-router serve`` group: run the FastAPI application."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import IO, Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
APP_IMPORT_STRING = "bike_routing_agent.api:app"


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    serve = subparsers.add_parser("serve", help="run the HTTP API server")
    serve_sub = serve.add_subparsers(dest="command", metavar="<command>")
    if serve_sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")

    start = serve_sub.add_parser("start", help="start uvicorn with the API app")
    start.add_argument("--host", default=DEFAULT_HOST)
    start.add_argument("--port", type=int, default=DEFAULT_PORT)
    start.add_argument(
        "--reload",
        action="store_true",
        help="auto-reload on code changes (development)",
    )
    return serve


def run(
    args: argparse.Namespace,
    stdout: IO[str],
    stderr: IO[str],
    *,
    runner: Callable[..., Any] | None = None,
) -> int:
    if args.command != "start":
        print("unknown serve command", file=stderr)
        return 2

    if runner is None:
        import uvicorn

        runner = uvicorn.run

    print(f"serving {APP_IMPORT_STRING} on http://{args.host}:{args.port}", file=stdout)
    runner(APP_IMPORT_STRING, host=args.host, port=args.port, reload=args.reload)
    return 0
