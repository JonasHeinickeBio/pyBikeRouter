"""``bike-router docker`` group: wrapper around ``docker compose``.

Resolves the compose file shipped in ``docker/compose.yaml`` (relative to the
current working directory unless ``--compose-file`` is given) and executes the
real ``docker`` CLI as a subprocess, forwarding its output.
"""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO

DEFAULT_COMPOSE_FILE = Path("docker/compose.yaml")

STATIC_ACTIONS: dict[str, list[str]] = {
    "up": ["up", "--build", "-d"],
    "down": ["down"],
    "build": ["build"],
    "ps": ["ps"],
    "restart": ["restart"],
}


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    docker = subparsers.add_parser("docker", help="manage the Docker deployment")
    docker.add_argument(
        "--compose-file",
        type=Path,
        default=DEFAULT_COMPOSE_FILE,
        help="path to the compose file (default: %(default)s)",
    )
    docker_sub = docker.add_subparsers(dest="command", metavar="<command>")
    if docker_sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")

    up = docker_sub.add_parser("up", help="build (if needed) and start in the background")
    up.add_argument("--no-build", action="store_true", help="skip the --build flag")

    logs = docker_sub.add_parser("logs", help="show container logs")
    logs.add_argument("--tail", type=int, default=100)
    logs.add_argument("-f", "--follow", action="store_true")

    for name in ("down", "build", "ps", "restart"):
        docker_sub.add_parser(name, help=f"docker compose {' '.join(STATIC_ACTIONS[name])}")
    return docker


def build_compose_args(args: argparse.Namespace) -> list[str]:
    if args.command == "up":
        action = ["up", "-d"] + ([] if args.no_build else ["--build"])
    elif args.command == "logs":
        action = ["logs", "--tail", str(args.tail)] + (["--follow"] if args.follow else [])
    else:
        action = list(STATIC_ACTIONS[args.command])
    return ["docker", "compose", "-f", str(args.compose_file), *action]


def run(
    args: argparse.Namespace,
    stdout: IO[str],
    stderr: IO[str],
    *,
    process_runner: Callable[[Sequence[str]], subprocess.CompletedProcess] | None = None,
) -> int:
    if args.command not in STATIC_ACTIONS and args.command not in ("up", "logs"):
        print("unknown docker command", file=stderr)
        return 2

    compose_file = Path(args.compose_file)
    if not compose_file.is_file():
        print(
            f"compose file not found: {compose_file} "
            "(run from the repository root or pass --compose-file)",
            file=stderr,
        )
        return 1

    command = build_compose_args(args)
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess] = (
        process_runner or _default_process_runner
    )
    try:
        completed = runner(command)
    except FileNotFoundError:
        print("docker CLI not found on PATH", file=stderr)
        return 1

    print("$ " + " ".join(command), file=stdout)
    return int(completed.returncode)


def _default_process_runner(command: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=False)
