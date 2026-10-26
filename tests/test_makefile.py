"""The Makefile: its help matches its targets, and the bike-router commands it runs exist."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from bike_routing_agent.cli.main import build_parser

ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
DOCUMENTED = re.findall(r"^([a-zA-Z_-]+):.*## (.+)$", MAKEFILE, flags=re.MULTILINE)


def phony() -> set[str]:
    block = re.search(r"^\.PHONY:(.*?)(?:\n\n|\Z)", MAKEFILE, flags=re.MULTILINE | re.DOTALL)
    assert block is not None
    return set(block.group(1).replace("\\\n", " ").split())


def test_every_documented_target_is_phony_and_every_phony_target_is_documented():
    names = {name for name, _ in DOCUMENTED}
    assert names == phony()
    assert {"help", "install", "test", "lint", "typecheck", "run", "ci", "check", "deploy"} <= names


def test_every_command_the_makefile_runs_is_a_real_bike_router_command():
    commands = re.findall(r"\$\(BIN\)bike-router (.+)$", MAKEFILE, flags=re.MULTILINE)
    assert commands  # run, deploy, deploy-status, deploy-logs
    for line in commands:
        words = line.replace("$(HOST)", "127.0.0.1").replace("$(PORT)", "8000").split()
        build_parser().parse_args(words)  # exits (SystemExit) on an unknown command or option


def test_ci_runs_what_the_workflow_runs():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for command in ("ruff check .", "mypy src", "pytest --maxfail=1"):
        assert command in workflow
    ci = re.search(r"^ci:.*\n((?:\t.*\n)+)", MAKEFILE, flags=re.MULTILINE)
    assert ci is not None and "pytest --maxfail=1" in ci.group(1)
    assert re.search(r"^ci: lint typecheck", MAKEFILE, flags=re.MULTILINE)


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_make_help_lists_every_documented_target():
    result = subprocess.run(
        ["make", "-C", str(ROOT), "help"], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    for name, text in DOCUMENTED:
        assert name in result.stdout and text.split(" (")[0] in result.stdout


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_a_dry_run_of_run_uses_the_host_and_port_given():
    result = subprocess.run(
        ["make", "-C", str(ROOT), "-n", "run", "HOST=0.0.0.0", "PORT=9000"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "serve start --reload --host 0.0.0.0 --port 9000" in result.stdout
