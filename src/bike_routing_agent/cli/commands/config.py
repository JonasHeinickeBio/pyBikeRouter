"""``bike-router config`` group: inspect and validate the settings model."""

from __future__ import annotations

import argparse
import json
import os
from typing import IO

from pydantic import BaseModel, ValidationError

REDACTED = "***REDACTED***"
SECRET_FIELD_HINTS = ("api_key", "token", "password", "secret")


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    config = subparsers.add_parser("config", help="inspect or validate configuration")
    config_sub = config.add_subparsers(dest="command", metavar="<command>")
    if config_sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")

    config_sub.add_parser("show", help="print effective settings (secrets redacted)")
    config_sub.add_parser("check", help="validate settings against the current environment")
    return config


def _is_secret(field_name: str) -> bool:
    lowered = field_name.lower()
    return any(hint in lowered for hint in SECRET_FIELD_HINTS)


def _redact(values: dict[str, object]) -> dict[str, object]:
    return {
        name: (REDACTED if _is_secret(name) and value not in ("", None) else value)
        for name, value in values.items()
    }


def redacted_dump(cfg: BaseModel) -> dict[str, object]:
    return _redact(cfg.model_dump(mode="json"))


def _env_overrides(cfg: BaseModel) -> dict[str, object]:
    """Settings fields whose UPPER-CASE environment variable is currently set."""
    return {
        name: os.environ[name.upper()]
        for name in type(cfg).model_fields
        if name.upper() in os.environ
    }


def run(args: argparse.Namespace, stdout: IO[str], stderr: IO[str]) -> int:
    from bike_routing_agent.config import Settings

    if args.command == "show":
        try:
            cfg = Settings()
        except ValidationError as exc:
            print(f"invalid configuration: {exc}", file=stderr)
            return 1
        print(json.dumps(redacted_dump(cfg), indent=2, sort_keys=True), file=stdout)
        return 0

    if args.command == "check":
        try:
            cfg = Settings()
        except ValidationError as exc:
            print("configuration INVALID:", file=stderr)
            for error in exc.errors():
                location = ".".join(str(part) for part in error["loc"])
                print(f"  {location}: {error['msg']}", file=stderr)
            return 1
        overrides = _redact(_env_overrides(cfg))
        print("configuration OK", file=stdout)
        print(f"  env overrides: {json.dumps(overrides, sort_keys=True)}", file=stdout)
        return 0

    print("unknown config command", file=stderr)
    return 2
