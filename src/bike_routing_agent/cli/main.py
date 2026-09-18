"""Top-level CLI parser and dispatch for the ``bike-router`` command."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from importlib import import_module
from typing import IO, Any

GROUP_MODULES: tuple[str, ...] = ("route", "serve", "docker", "config", "providers")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bike-router",
        description="OSM bike routing agent: plan routes, serve the API, manage Docker.",
        epilog="Run 'bike-router <group> --help' for the commands in each group.",
    )
    parser.add_argument("--version", action="version", version=_version_string())
    subparsers = parser.add_subparsers(dest="group", metavar="<group>")
    if subparsers is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")

    for module_name in GROUP_MODULES:
        module: Any = import_module(f"bike_routing_agent.cli.commands.{module_name}")
        group_parser = module.add_parser(subparsers)
        if group_parser is not None:
            group_parser.set_defaults(_group_parser=group_parser)

    return parser


def _version_string() -> str:
    from bike_routing_agent import __version__

    return f"bike-router {__version__}"


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> int:
    """Run the CLI. Returns the process exit code (0/1/2)."""
    out: IO[str] = stdout if stdout is not None else sys.stdout
    err: IO[str] = stderr if stderr is not None else sys.stderr

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # -h/--version and argparse usage errors
        return int(exc.code or 0)

    group = getattr(args, "group", None)
    if not group:
        parser.print_help(out)
        return 2

    group_parser: argparse.ArgumentParser | None = getattr(args, "_group_parser", None)
    if not getattr(args, "command", None):
        if group_parser is not None:
            group_parser.print_help(out)
        return 2

    module = import_module(f"bike_routing_agent.cli.commands.{group}")
    return int(module.run(args, out, err))


__all__ = ["GROUP_MODULES", "build_parser", "main"]
