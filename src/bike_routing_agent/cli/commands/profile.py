"""``bike-router profile`` group: the settings a routing profile takes, and a dry run of them.

The parameter space is the LinkML schema in ``providers/profile_settings.linkml.yaml``; these
commands only show it (``settings``, ``schema``) and check input against it (``check``), so what
``route plan --profile-param`` will accept can be tried without routing.
"""

from __future__ import annotations

import argparse
import json
from typing import IO

from pydantic import ValidationError

from bike_routing_agent.cli import profile_params as cli_params
from bike_routing_agent.config import BROUTER_PROFILE_MAP
from bike_routing_agent.models import BikeType
from bike_routing_agent.providers import brouter_params as bp


def _add_target(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--bike-type",
        choices=[b.value for b in BikeType],
        help="the BRouter profile this bike type is planned with",
    )
    parser.add_argument("--profile", help="a BRouter profile name, e.g. custom_touring-v1")


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    profile = subparsers.add_parser(
        "profile", help="the settings a routing profile takes (route plan --profile-param)"
    )
    sub = profile.add_subparsers(dest="command", metavar="<command>")
    if sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")

    settings = sub.add_parser(
        "settings", help="list the settings of a profile (all profiles when none is named)"
    )
    _add_target(settings)
    settings.add_argument("--format", choices=("text", "json"), default="text")

    schema = sub.add_parser("schema", help="the JSON Schema of a profile's settings")
    _add_target(schema)

    check = sub.add_parser(
        "check", help="dry run: are these settings valid, and what would BRouter be sent?"
    )
    _add_target(check)
    check.add_argument("--profile-param", action="append", default=[], metavar="NAME=VALUE")
    check.add_argument(
        "--profile-params-file",
        metavar="FILE",
        help="a JSON object of settings ('-' reads standard input); --profile-param wins",
    )
    check.add_argument("--format", choices=("text", "json"), default="text")
    return profile


def _profile_of(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """(profile, error) for the --bike-type / --profile the command line named."""
    if args.bike_type and args.profile:
        return None, "give --bike-type or --profile, not both"
    if args.bike_type:
        return BROUTER_PROFILE_MAP[args.bike_type], None
    return args.profile, None


def _bike_types_of(profile: str) -> list[str]:
    return sorted(b for b, p in BROUTER_PROFILE_MAP.items() if p == profile)


def _settings_text(profile: str) -> str:
    uses = ", ".join(_bike_types_of(profile)) or "no bike type"
    specs = bp.settings_for(profile)
    if not specs:
        return f"{profile} ({uses}): no per-request settings"
    width = max(len(s.name) for s in specs)
    lines = [f"{profile} ({uses}): {len(specs)} settings", ""]
    for group in dict.fromkeys(s.group for s in specs):
        lines.append(f"{group}:")
        for s in (s for s in specs if s.group == group):
            note = "  [time estimate only]" if s.affects == "time" else ""
            lines.append(f"  {s.name:<{width}}  {s.range_text:<10}  {s.meaning}{note}")
    return "\n".join(lines)


def _settings(args: argparse.Namespace, stdout: IO[str], stderr: IO[str]) -> int:
    profile, error = _profile_of(args)
    if error:
        print(error, file=stderr)
        return 2
    if profile is None:  # every profile that has settings
        names = sorted(bp.PROFILE_MODELS)
        if args.format == "json":
            payload = {name: bp.describe(name)["settings"] for name in names}
            print(json.dumps(payload, indent=2), file=stdout)
        else:
            for name in names:
                print(_settings_text(name), file=stdout, end="\n\n")
        return 0
    if args.format == "json":
        print(json.dumps(bp.describe(profile)["settings"], indent=2), file=stdout)
    else:
        print(_settings_text(profile), file=stdout)
    return 0


def _schema(args: argparse.Namespace, stdout: IO[str], stderr: IO[str]) -> int:
    profile, error = _profile_of(args)
    if error or profile is None:
        print(error or "give --bike-type or --profile", file=stderr)
        return 2
    schema = bp.describe(profile)["json_schema"]
    if schema is None:
        print(f"{profile} has no per-request settings", file=stderr)
        return 2
    print(json.dumps(schema, indent=2), file=stdout)
    return 0


def _check(args: argparse.Namespace, stdout: IO[str], stderr: IO[str]) -> int:
    profile, error = _profile_of(args)
    if error or profile is None:
        print(error or "give --bike-type or --profile", file=stderr)
        return 2
    try:
        requested = cli_params.gather(args.profile_param, args.profile_params_file)
    except ValueError as exc:
        print(str(exc), file=stderr)
        return 2
    problems: list[str] = []
    try:
        bp.validate(profile, requested)
    except ValidationError as exc:
        problems = bp.problems(exc, profile)
    except ValueError as exc:
        problems = [str(exc)]
    sent = bp.resolve(profile, requested).values if not problems else {}
    if args.format == "json":
        payload = {"profile": profile, "valid": not problems, "problems": problems, "sent": sent}
        print(json.dumps(payload, indent=2), file=stdout)
    elif problems:
        print(f"{profile}: not valid", file=stdout)
        for problem in problems:
            print(f"  - {problem}", file=stdout)
    else:
        words = " ".join(f"profile:{k}={v}" for k, v in sent.items()) or "(nothing)"
        print(f"{profile}: ok\nsent to BRouter: {words}", file=stdout)
    return 2 if problems else 0


def run(args: argparse.Namespace, stdout: IO[str], stderr: IO[str]) -> int:
    handlers = {"settings": _settings, "schema": _schema, "check": _check}
    handler = handlers.get(args.command)
    if handler is None:
        print("unknown profile command", file=stderr)
        return 2
    return handler(args, stdout, stderr)
