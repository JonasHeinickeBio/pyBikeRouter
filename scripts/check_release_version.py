"""Check that every place a version lives says the same thing, before a release is published.

    python scripts/check_release_version.py v0.3.0      # the tag that was pushed
    python scripts/check_release_version.py             # only that the files agree with each other

The version is written in four places: ``pyproject.toml`` (what PyPI shows), ``__version__`` (what
``bike-router --version`` prints) and ``server.json`` twice (the server's, and its PyPI package's:
the MCP Registry installs exactly that version). The package's name is written twice, in
``pyproject.toml`` and as the PyPI package's ``identifier`` in ``server.json``. A release whose
files disagree would publish a package the registry entry cannot find, so the release workflow
runs this first.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def versions(root: Path = ROOT) -> dict[str, str]:
    """Where each version is written, and what it says."""
    found = {
        "pyproject.toml": tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"][
            "version"
        ]
    }
    init = (root / "src" / "bike_routing_agent" / "__init__.py").read_text("utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', init, flags=re.MULTILINE)
    found["__init__.__version__"] = match.group(1) if match else "(not found)"
    server = json.loads((root / "server.json").read_text("utf-8"))
    found["server.json version"] = server.get("version", "(missing)")
    pypi = [p for p in server.get("packages", []) if p.get("registryType") == "pypi"]
    found["server.json pypi package"] = pypi[0].get("version", "(missing)") if pypi else "(none)"
    return found


def package_names(root: Path = ROOT) -> dict[str, str]:
    """The name the package is built under and the one the registry entry installs."""
    project = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]["name"]
    server = json.loads((root / "server.json").read_text("utf-8"))
    pypi = [p for p in server.get("packages", []) if p.get("registryType") == "pypi"]
    return {
        "pyproject.toml name": project,
        "server.json pypi identifier": pypi[0].get("identifier", "(missing)") if pypi else "(none)",
    }


def problems(tag: str | None, root: Path = ROOT) -> list[str]:
    found = versions(root)
    out: list[str] = []
    names = package_names(root)
    if len(set(names.values())) != 1:
        out.append(
            "the registry entry would not find the package: "
            + ", ".join(f"{k} = {v}" for k, v in names.items())
        )
    if len(set(found.values())) != 1:
        out.append("the versions disagree: " + ", ".join(f"{k} = {v}" for k, v in found.items()))
    if tag is not None:
        expected = tag.removeprefix("v")
        wrong = {k: v for k, v in found.items() if v != expected}
        if wrong:
            out.append(
                f"the tag is {tag} but " + ", ".join(f"{k} says {v}" for k, v in wrong.items())
            )
    return out


def main(argv: list[str]) -> int:
    tag = argv[1] if len(argv) > 1 else None
    issues = problems(tag)
    for issue in issues:
        print(f"error: {issue}", file=sys.stderr)
    if not issues:
        print(f"ok: {next(iter(versions().values()))}" + (f" matches {tag}" if tag else ""))
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
