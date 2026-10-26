"""The MCP Registry entry (server.json), the package it points at, and the release guard.

The registry installs exactly what server.json names, so these keep the files honest with each
other offline: the name is the one the README proves ownership with, the version is the one that
gets built, the command exists, the environment variable is the one the server reads.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from bike_routing_agent import __version__
from bike_routing_agent.cli.commands.mcp import API_ENV, DEFAULT_API
from bike_routing_agent.cli.main import build_parser

ROOT = Path(__file__).resolve().parents[1]
SERVER = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
SCRIPT = ROOT / "scripts" / "check_release_version.py"
PACKAGE = SERVER["packages"][0]


def test_the_entry_follows_the_registrys_rules():
    assert SERVER["$schema"].startswith("https://static.modelcontextprotocol.io/schemas/")
    assert re.fullmatch(r"[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+", SERVER["name"])
    assert len(SERVER["name"]) <= 200 and len(SERVER["description"]) <= 100  # the schema's limits
    assert SERVER["name"].startswith("io.github.JonasHeinickeBio/")  # GitHub login authentication
    assert re.fullmatch(r"\d+\.\d+\.\d+", SERVER["version"])  # a real version, never a range
    assert SERVER["repository"] == {
        "url": "https://github.com/JonasHeinickeBio/pyBikeRouter",
        "source": "github",
    }


def test_the_package_is_this_project_on_pypi_and_run_with_uvx():
    assert len(SERVER["packages"]) == 1
    assert PACKAGE["registryType"] == "pypi"
    assert PACKAGE["identifier"] == PYPROJECT["project"]["name"]
    assert PACKAGE["version"] == SERVER["version"] == PYPROJECT["project"]["version"]
    assert PACKAGE["transport"] == {"type": "stdio"}
    assert PACKAGE["runtimeHint"] == "uvx"


def test_uvx_finds_an_executable_called_like_the_package():
    """``uvx <package> ...`` runs the executable that has the package's name."""
    scripts = PYPROJECT["project"]["scripts"]
    assert scripts[PACKAGE["identifier"]] == "bike_routing_agent.cli:main"
    assert PACKAGE["identifier"] == "bike-router"  # the command people already know


def test_the_arguments_are_a_real_command_and_the_environment_variable_is_the_one_read():
    arguments = [a["value"] for a in PACKAGE["packageArguments"]]
    assert all(a["type"] == "positional" for a in PACKAGE["packageArguments"])
    parsed = build_parser().parse_args(arguments)  # exits on an unknown command
    assert (parsed.group, parsed.command) == ("mcp", "serve")
    (variable,) = PACKAGE["environmentVariables"]
    assert variable["name"] == API_ENV and variable["default"] == DEFAULT_API
    assert variable["isRequired"] is True and not variable.get("isSecret")  # it is only an address


def test_the_readme_carries_the_name_the_registry_checks_for():
    """PyPI shows the README as the description, and the registry looks for this exact string."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"mcp-name: {SERVER['name']}" in readme
    assert PYPROJECT["project"]["readme"] == "README.md"


def test_the_version_is_the_same_everywhere():
    assert __version__ == PYPROJECT["project"]["version"] == SERVER["version"]
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"ok: {__version__}"


@pytest.mark.parametrize(
    ("tag", "ok"),
    [(f"v{__version__}", True), (__version__, True), ("v9.9.9", False), ("main", False)],
)
def test_the_release_guard_accepts_only_the_tag_that_matches(tag, ok):
    result = subprocess.run([sys.executable, str(SCRIPT), tag], capture_output=True, text=True)
    assert (result.returncode == 0) is ok
    if not ok:
        assert f"the tag is {tag}" in result.stderr


def test_the_guard_notices_files_that_disagree(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import check_release_version as guard
    finally:
        sys.path.pop(0)
    (tmp_path / "src" / "bike_routing_agent").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1.0.0"\n')
    (tmp_path / "src" / "bike_routing_agent" / "__init__.py").write_text('__version__ = "1.0.1"\n')
    server = {"version": "1.0.0", "packages": [{"registryType": "pypi", "version": "1.0.2"}]}
    (tmp_path / "server.json").write_text(json.dumps(server))
    found = guard.problems("v1.0.0", tmp_path)
    assert len(found) == 2 and "disagree" in found[0] and "the tag is v1.0.0" in found[1]
    assert "__init__.__version__ = 1.0.1" in found[0] and "pypi package = 1.0.2" in found[0]
    (tmp_path / "server.json").write_text(json.dumps({"version": "1.0.0", "packages": []}))
    assert "(none)" in guard.problems(None, tmp_path)[0]


def test_the_publish_workflow_builds_from_the_tag_and_publishes_without_a_token():
    workflow = (ROOT / ".github" / "workflows" / "publish-pypi.yml").read_text(encoding="utf-8")
    assert 'tags: ["v*"]' in workflow and "workflow_dispatch" not in workflow  # only on a tag
    assert 'check_release_version.py "$GITHUB_REF_NAME"' in workflow
    assert workflow.index("check_release_version.py") < workflow.index("python -m build")
    assert "id-token: write" in workflow and "environment: pypi" in workflow
    assert "secrets." not in workflow and "password:" not in workflow  # trusted publishing
    # the permission to publish is only on the publish job, not the build job
    assert workflow.count("id-token: write") == 1
    assert re.search(r"pypa/gh-action-pypi-publish@[0-9a-f]{40} # v", workflow)  # pinned by hash
