"""Running the API's own endpoint functions from the command line.

The CLI is a thin layer over the same handlers the HTTP API serves (points of interest,
history, readiness, ...): one implementation, the same validation, the same errors. They are
called in-process with the settings of the environment, so no server has to be running.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import IO, Any, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel

T = TypeVar("T")

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


def call_handler(make: Callable[[], Awaitable[T]], stderr: IO[str]) -> tuple[T | None, int]:
    """Run ``make()`` to completion. An ``HTTPException`` becomes a message and an exit code
    (422 = a usage error, anything else a runtime failure); the result is ``None`` then."""
    try:
        return asyncio.run(_await(make)), EXIT_OK
    except HTTPException as exc:
        print(f"error: {exc.detail}", file=stderr)
        return None, EXIT_USAGE if exc.status_code == 422 else EXIT_FAILURE


async def _await(make: Callable[[], Awaitable[T]]) -> T:
    return await make()


def to_plain(value: Any) -> Any:
    """Pydantic models (and lists of them) as plain JSON-able data."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [to_plain(v) for v in value]
    return value


def dump_json(value: Any, stdout: IO[str]) -> None:
    print(json.dumps(to_plain(value), indent=2, ensure_ascii=False), file=stdout)
