"""The easy-install kit (easy-install/): a compose file, launchers and a start page that must
agree with each other and with the rest of the project."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from bike_routing_agent.config import Settings

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "easy-install"
PLATFORMS = {
    "Windows": (
        "Start-Windows.bat",
        "Stop-Windows.bat",
        "Update-Windows.bat",
        "Choose-area-Windows.bat",
        "Choose-language-model-Windows.bat",
    ),
    "Mac": (
        "Start-Mac.command",
        "Stop-Mac.command",
        "Update-Mac.command",
        "Choose-area-Mac.command",
        "Choose-language-model-Mac.command",
    ),
    "Linux": (
        "start-linux.sh",
        "stop-linux.sh",
        "update-linux.sh",
        "choose-area-linux.sh",
        "choose-language-model-linux.sh",
    ),
}
ALL_LAUNCHERS = [name for names in PLATFORMS.values() for name in names]


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((KIT / "docker-compose.yml").read_text())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_compose_file_has_the_app_the_engine_and_the_one_time_setup(compose):
    services = compose["services"]
    assert set(services) == {"app", "brouter", "setup", "ollama"}
    assert services["setup"]["profiles"] == ["setup"]  # never started by "up"
    assert services["setup"]["entrypoint"] == ["bike-router", "setup", "start"]
    assert services["setup"]["tty"] is True and services["setup"]["stdin_open"] is True


def test_a_local_language_model_is_opt_in_and_the_wizard_can_save_the_answer(compose):
    ollama = compose["services"]["ollama"]
    assert ollama["profiles"] == ["ollama"]  # never started unless the person chose it
    assert ollama["image"].startswith("ollama/ollama:") and ":latest" not in ollama["image"]
    assert "ports" not in ollama  # only the app, inside the network, talks to it
    assert "ollama-models" in compose["volumes"]
    setup = compose["services"]["setup"]
    assert "./settings.env:/config/settings.env" in setup["volumes"]
    assert setup["environment"]["PBR_SETTINGS_FILE"] == "/config/settings.env"
    # the base URL the wizard writes is the service name of this compose file
    from bike_routing_agent.llm_setup import OLLAMA_BASE_URL

    assert OLLAMA_BASE_URL == "http://ollama:11434/v1"


@pytest.mark.parametrize("name", [n for n in ALL_LAUNCHERS if n.lower().startswith("start")])
def test_the_start_scripts_start_and_fill_the_local_model_only_when_it_was_chosen(name):
    text = (KIT / name).read_text()
    assert "LLM_BASE_URL=http://ollama:" in text and "--profile ollama" in text
    assert "ollama pull" in text and "LLM_MODEL" in text


@pytest.mark.parametrize("name", [n for n in ALL_LAUNCHERS if n.lower().startswith("stop")])
def test_the_stop_scripts_stop_the_local_model_too(name):
    assert "--profile ollama" in (KIT / name).read_text()


@pytest.mark.parametrize("name", [n for n in ALL_LAUNCHERS if "language-model" in n.lower()])
def test_the_language_model_scripts_skip_the_map_question(name):
    assert "--profile setup run --rm setup --skip" in (KIT / name).read_text()


def test_the_app_is_only_reachable_from_this_computer_unless_the_person_says_otherwise(compose):
    (port,) = compose["services"]["app"]["ports"]
    assert port == "${APP_BIND:-127.0.0.1}:${PORT:-8000}:8000"
    assert "127.0.0.1" in (KIT / "settings.env").read_text()


def test_the_app_is_wired_to_the_engine_and_the_data_folders(compose):
    app = compose["services"]["app"]
    env = app["environment"]
    assert env["ROUTING_PROVIDER"] == "brouter"
    assert env["BROUTER_BASE_URL"] == "http://brouter:17777"
    assert env["BROUTER_SEGMENTS_DIR"] == "/data/segments" and env["EXPORT_DIR"] == "/data/exports"
    assert "./data/segments:/data/segments" in app["volumes"]
    assert "./data/exports:/data/exports" in app["volumes"]
    # the engine reads the same folder, read-only, plus the project's own profiles
    brouter = compose["services"]["brouter"]["volumes"]
    assert "./data/segments:/segments4:ro" in brouter
    assert "./brouter/profiles:/customprofiles:ro" in brouter
    assert "./brouter/lookups.dat:/profiles2/lookups.dat:ro" in brouter
    assert "ports" not in compose["services"]["brouter"]  # no authentication: not published


def test_the_image_can_be_replaced_for_a_local_build_and_defaults_to_the_published_one(compose):
    image = compose["services"]["app"]["image"]
    assert image == "${PYBIKEROUTER_IMAGE:-ghcr.io/jonasheinickebio/pybikerouter:latest}"
    assert compose["services"]["setup"]["image"] == image


def test_the_engine_image_is_the_one_the_project_was_tested_with(compose):
    project = yaml.safe_load((ROOT / "docker" / "compose.yaml").read_text())
    assert compose["services"]["brouter"]["image"] == project["services"]["brouter"]["image"]


def test_the_profiles_and_lookups_are_the_projects_own_copies():
    source = ROOT / "docker" / "brouter"
    for name in sorted((source / "profiles").glob("*.brf")):
        assert sha(KIT / "brouter" / "profiles" / name.name) == sha(name), name.name
    assert sha(KIT / "brouter" / "lookups.dat") == sha(source / "lookups.dat")
    shipped = {p.name for p in (KIT / "brouter" / "profiles").glob("*.brf")}
    assert shipped == {p.name for p in (source / "profiles").glob("*.brf")}


def test_the_settings_file_only_mentions_settings_that_exist():
    fields = {f.upper() for f in Settings.model_fields}
    seen = []
    for line in (KIT / "settings.env").read_text().splitlines():
        match = re.match(r"#?\s*([A-Z][A-Z0-9_]+)=", line)
        if match:
            seen.append(match.group(1))
    compose_level = {"PORT", "APP_BIND"}
    assert compose_level <= set(seen)
    assert [k for k in seen if k not in compose_level and k not in fields] == []


@pytest.mark.parametrize("platform", sorted(PLATFORMS))
def test_every_platform_has_start_stop_update_and_choose_area(platform):
    for name in PLATFORMS[platform]:
        assert (KIT / name).is_file(), name


@pytest.mark.parametrize("name", ALL_LAUNCHERS)
def test_every_launcher_uses_the_settings_file_and_the_compose_file_next_to_it(name):
    text = (KIT / name).read_text()
    assert "--env-file settings.env" in text
    assert "docker compose" in text
    assert 'cd /d "%~dp0"' in text or 'cd "$(dirname "$0")"' in text  # runs from its own folder


@pytest.mark.parametrize("name", [n for n in ALL_LAUNCHERS if n.startswith(("start", "Start"))])
def test_the_start_scripts_check_docker_ask_once_and_wait_until_it_is_ready(name):
    text = (KIT / name).read_text()
    assert "docker info" in text  # installed, but is it running?
    assert ".rd5" in text and "--profile setup run --rm setup" in text  # first start only
    assert "healthz" in text and "up -d" in text
    assert "image inspect" in text  # offline or a local image: reuse it instead of failing


@pytest.mark.parametrize("name", [n for n in ALL_LAUNCHERS if n.endswith((".sh", ".command"))])
def test_the_shell_launchers_are_valid_and_executable(name):
    path = KIT / name
    assert path.read_text().startswith("#!/bin/sh")
    assert b"\r\n" not in path.read_bytes()
    assert path.stat().st_mode & 0o111, f"{name} is not executable"
    if shutil.which("sh"):
        assert subprocess.run(["sh", "-n", str(path)], capture_output=True).returncode == 0


@pytest.mark.parametrize("name", [n for n in ALL_LAUNCHERS if n.endswith(".bat")])
def test_the_windows_launchers_use_windows_line_endings(name):
    data = (KIT / name).read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n") > 3
    assert b"\xef\xbb\xbf" not in data[:3]  # no BOM: cmd would print it as a command


def test_the_start_page_and_the_help_name_the_files_that_exist():
    page = (KIT / "START-HERE.txt").read_text()
    for name in ("Start-Windows.bat", "Start-Mac.command", "start-linux.sh", "settings.env"):
        assert name in page, name
    for name in ALL_LAUNCHERS:
        stem = name.split(".")[0].split("-")[0].lower()
        assert stem in page.lower()
    assert "http://localhost:8000" in page and "docs/easy-install.md" in page
    help_text = (ROOT / "docs" / "easy-install.md").read_text()
    assert "Start-Windows.bat" in help_text and "start-linux.sh" in help_text


def test_the_bundle_holds_the_kit_and_nothing_else(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import make_easy_bundle
    finally:
        sys.path.pop(0)
    data = KIT / "data"
    existed = data.exists()  # a developer may run the kit from the checkout: keep their files
    data.mkdir(exist_ok=True)
    marker = data / "secret-route.gpx"
    marker.write_text("not for the zip")
    try:
        built = make_easy_bundle.build(tmp_path)
    finally:
        marker.unlink(missing_ok=True)
        if not existed:
            shutil.rmtree(data, ignore_errors=True)
    version = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    assert built.name == f"pybikerouter-easy-{version.group(1)}.zip"  # type: ignore[union-attr]
    with zipfile.ZipFile(built) as bundle:
        names = bundle.namelist()
        assert "pyBikeRouter/START-HERE.txt" in names and "pyBikeRouter/HELP.md" in names
        assert "pyBikeRouter/docker-compose.yml" in names
        assert all(name.startswith("pyBikeRouter/") for name in names)
        assert not any("secret-route" in name or name.endswith(".pyc") for name in names)
        assert "pyBikeRouter/data/README.txt" in names
        mode = bundle.getinfo("pyBikeRouter/start-linux.sh").external_attr >> 16
        assert mode & 0o111  # still executable after unzipping
        assert b"\r\n" in bundle.read("pyBikeRouter/Start-Windows.bat")


def test_the_release_workflow_publishes_the_image_the_kit_pulls_and_attaches_the_zip():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())
    triggers = workflow.get("on") or workflow[True]  # YAML 1.1 reads a bare `on` as True
    assert triggers["push"]["tags"] == ["v*"]
    assert set(workflow["jobs"]) == {"image", "bundle"}
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "linux/amd64,linux/arm64" in text
    assert re.search(r"^\s*echo \"image=ghcr\.io/\$\{owner\}/pybikerouter\"", text, re.M)
    assert "make_easy_bundle.py" in text
    # the kit pulls exactly what the workflow pushes (the owner is lower-cased there)
    compose = (KIT / "docker-compose.yml").read_text()
    assert "ghcr.io/jonasheinickebio/pybikerouter:latest" in compose
    assert "pybikerouter:latest" in text or ":latest" in text
