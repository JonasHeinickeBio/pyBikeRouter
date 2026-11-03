"""Reading profile settings from the command line: ``NAME=VALUE`` words and JSON files.

``route plan`` and ``profile check`` take the same input, so it is read in one place. A file (or
``-`` for standard input) holds one JSON object, ``{"uphillcost": 120, "consider_noise": true}``;
``--profile-param`` words are applied after it and win. What the names and values may be is the
profile's own business (``brouter_params``), not checked here beyond "a boolean or a number".
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Iterable
from pathlib import Path

TRUE_WORDS = ("true", "on", "yes")
FALSE_WORDS = ("false", "off", "no")


def parse_items(items: Iterable[str]) -> dict[str, bool | float]:
    """``NAME=VALUE`` words -> settings; true/false/on/off/yes/no are booleans, the rest numbers."""
    found: dict[str, bool | float] = {}
    for item in items:
        name, sep, raw = item.partition("=")
        name, raw = name.strip(), raw.strip().lower()
        if not sep or not name or not raw:
            raise ValueError(f"--profile-param wants NAME=VALUE, got {item!r}")
        if raw in TRUE_WORDS:
            found[name] = True
        elif raw in FALSE_WORDS:
            found[name] = False
        else:
            try:
                number = float(raw)
            except ValueError:
                raise ValueError(
                    f"--profile-param {name}: {raw!r} is not a number or true/false"
                ) from None
            if not math.isfinite(number):
                raise ValueError(f"--profile-param {name}: {raw!r} is not a finite number")
            found[name] = number
    return found


def load_file(path: str) -> dict[str, bool | float]:
    """A JSON object of settings from a file, or from standard input when ``path`` is ``-``."""
    try:
        text = sys.stdin.read() if path == "-" else Path(path).read_text()
    except OSError as exc:
        raise ValueError(f"--profile-params-file {path}: {exc.strerror or exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--profile-params-file {path}: not JSON ({exc.msg})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"--profile-params-file {path}: expected an object of NAME: VALUE")
    for name, value in data.items():
        if isinstance(value, bool):
            continue
        if not isinstance(value, int | float) or not math.isfinite(value):
            raise ValueError(f"--profile-params-file {path}: {name} must be true/false or a number")
    return data


def gather(items: Iterable[str], file: str | None) -> dict[str, bool | float]:
    """The settings from ``file`` (if any), then the ``NAME=VALUE`` words on top."""
    found = load_file(file) if file else {}
    found.update(parse_items(items))
    return found
