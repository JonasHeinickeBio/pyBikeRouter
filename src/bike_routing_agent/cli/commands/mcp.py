"""``bike-router mcp``: let another agent plan bike routes through this service (MCP, stdio).

``serve`` speaks the Model Context Protocol on stdin/stdout and answers its tool calls by asking a
running pyBikeRouter API (``--api``); ``config`` prints the entry to put in an MCP client's
configuration. See docs/mcp.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import IO

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
DEFAULT_API = "http://127.0.0.1:8000"
API_ENV = "BIKE_ROUTER_API_URL"


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    group = subparsers.add_parser(
        "mcp", help="an MCP server: plan routes from another agent (stdio)"
    )
    sub = group.add_subparsers(dest="command", metavar="<command>")
    if sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")
    for name, text in (
        ("serve", "speak MCP on stdin/stdout, answering from a running API"),
        ("config", "print the entry for an MCP client's configuration"),
    ):
        command = sub.add_parser(name, help=text)
        command.add_argument(
            "--api",
            default=os.environ.get(API_ENV) or DEFAULT_API,
            metavar="URL",
            help=f"the pyBikeRouter API to ask (default: ${API_ENV} or {DEFAULT_API})",
        )
        if name == "serve":
            command.add_argument(
                "--timeout",
                type=float,
                default=300.0,
                metavar="SECONDS",
                help="how long one call to the API may take (default 300: a long route is slow)",
            )
    return group


def _api_url(value: str) -> str | None:
    """The API address when it is an http(s) URL, else ``None``."""
    return value.rstrip("/") if value.startswith(("http://", "https://")) else None


def run(
    args: argparse.Namespace,
    stdout: IO[str],
    stderr: IO[str],
    *,
    stdin: IO[str] | None = None,
) -> int:
    api = _api_url(args.api)
    if api is None:
        print(f"error: --api must be an http(s) URL, not {args.api!r}", file=stderr)
        return EXIT_USAGE
    if args.command == "config":
        entry = {"command": "bike-router", "args": ["mcp", "serve", "--api", api]}
        print(json.dumps({"mcpServers": {"bike-router": entry}}, indent=2), file=stdout)
        return EXIT_OK
    if args.command != "serve":
        print("unknown mcp command", file=stderr)
        return EXIT_USAGE

    from bike_routing_agent import __version__
    from bike_routing_agent.mcp_server.protocol import McpServer
    from bike_routing_agent.mcp_server.tools import INSTRUCTIONS, BikeRouterTools

    # The protocol is UTF-8 whatever the system's locale says (Windows redirects stdio in the ANSI
    # code page); a stream that cannot be reconfigured (a test's StringIO) is used as it is.
    source = stdin if stdin is not None else sys.stdin
    for stream, errors in ((source, "replace"), (stdout, "strict")):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors=errors)
    # Standard output is the protocol: nothing else may be written to it.
    logging.basicConfig(stream=stderr, level=logging.WARNING, format="mcp: %(message)s")
    server = McpServer(
        BikeRouterTools(api, timeout_s=args.timeout),
        name="bike-router",
        version=__version__,
        instructions=INSTRUCTIONS,
    )
    print(f"bike-router MCP server ready (API: {api})", file=stderr)
    try:
        server.serve(source, stdout)
    except KeyboardInterrupt:
        pass
    return EXIT_OK
