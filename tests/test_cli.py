"""Unit tests for the ``bike-router`` CLI (subcommand parsing and execution)."""

from __future__ import annotations

import argparse
import io
import json
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bike_routing_agent.cli import main
from bike_routing_agent.cli.commands import config as config_cmd
from bike_routing_agent.cli.commands import docker as docker_cmd
from bike_routing_agent.cli.commands import providers as providers_cmd
from bike_routing_agent.cli.commands import route as route_cmd
from bike_routing_agent.cli.commands import serve as serve_cmd
from bike_routing_agent.cli.main import build_parser


def parse(*argv: str) -> Any:
    return build_parser().parse_args(list(argv))


@pytest.fixture(autouse=True)
def _no_history_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's DATABASE_URL must never make these tests write a history."""
    monkeypatch.setattr(route_cmd, "_default_history_factory", lambda: None)


class FakeGraph:
    def __init__(self, final_state: dict[str, Any] | None = None, error: Exception | None = None):
        self.final_state = final_state or {}
        self.error = error
        self.invoked_with: dict[str, Any] | None = None

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        self.invoked_with = state
        if self.error is not None:
            raise self.error
        return self.final_state


READY_STATE: dict[str, Any] = {
    "status": "ready",
    "explanation": "direct route",
    "errors": [],
    "selected_candidate": {
        "distance_m": 4200.0,
        "duration_s": 900.0,
        "raw_provider_response": {"secret": "payload"},
    },
    "artifacts": {"geojson_file": "a" * 32 + ".geojson", "gpx_file": "a" * 32 + ".gpx"},
}


# ---------------------------------------------------------------- global behaviour


def test_main_without_args_prints_help_and_exits_2() -> None:
    out, err = io.StringIO(), io.StringIO()
    assert main([], stdout=out, stderr=err) == 2
    assert "usage: bike-router" in out.getvalue()


def test_main_group_without_command_prints_group_help_and_exits_2() -> None:
    out, err = io.StringIO(), io.StringIO()
    assert main(["route"], stdout=out, stderr=err) == 2
    assert "plan" in out.getvalue()


def test_main_unknown_group_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["nope"]) == 2
    assert "invalid choice" in capsys.readouterr().err


def test_main_version_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert "bike-router" in capsys.readouterr().out


# ---------------------------------------------------------------- route plan


def test_route_plan_ready_prints_json_and_strips_raw_response() -> None:
    graph = FakeGraph(READY_STATE)
    out, err = io.StringIO(), io.StringIO()
    args = parse("route", "plan", "--origin", "Berlin Hbf", "--destination", "Potsdam")
    rc = route_cmd.run(args, out, err, graph_factory=lambda: graph)
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["status"] == "ready"
    assert "raw_provider_response" not in payload["route"]
    assert payload["artifacts"]["geojson_file"].endswith(".geojson")
    assert graph.invoked_with is not None
    assert graph.invoked_with["raw_input"]["origin"] == "Berlin Hbf"


def test_route_plan_awaiting_clarification_exits_1_with_candidates() -> None:
    state = {
        "status": "awaiting_clarification",
        "errors": [],
        "clarification": [
            {"field": "Berlin", "candidates": [{"label": "Berlin DE", "confidence": 0.9}]}
        ],
    }
    out, err = io.StringIO(), io.StringIO()
    args = parse("route", "plan", "--origin", "Berlin", "--destination", "Potsdam")
    rc = route_cmd.run(args, out, err, graph_factory=lambda: FakeGraph(state))
    assert rc == 1
    payload = json.loads(out.getvalue())
    assert payload["clarification"][0]["candidates"][0]["label"] == "Berlin DE"


def test_route_plan_provider_failure_exits_1() -> None:
    out, err = io.StringIO(), io.StringIO()
    args = parse("route", "plan", "--origin", "a", "--destination", "b")
    state = {"status": "provider_failure", "errors": ["ors down"]}
    rc = route_cmd.run(args, out, err, graph_factory=lambda: FakeGraph(state))
    assert rc == 1
    assert json.loads(out.getvalue())["errors"] == ["ors down"]


def test_route_plan_graph_value_error_is_usage_error_2() -> None:
    out, err = io.StringIO(), io.StringIO()
    args = parse("route", "plan", "--origin", "a", "--destination", "b")
    rc = route_cmd.run(
        args, out, err, graph_factory=lambda: FakeGraph(error=ValueError("bad bike_type"))
    )
    assert rc == 2
    assert "invalid request" in err.getvalue()


def test_route_plan_runtime_error_exits_1() -> None:
    out, err = io.StringIO(), io.StringIO()
    args = parse("route", "plan", "--origin", "a", "--destination", "b")
    rc = route_cmd.run(
        args, out, err, graph_factory=lambda: FakeGraph(error=ConnectionError("ORS unreachable"))
    )
    assert rc == 1
    assert "routing failed" in err.getvalue()


def test_route_plan_output_file_written(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "plan.json"
    out, err = io.StringIO(), io.StringIO()
    args = parse(
        "route", "plan", "--origin", "a", "--destination", "b", "--output", str(target)
    )
    assert route_cmd.run(args, out, err, graph_factory=lambda: FakeGraph(READY_STATE)) == 0
    assert json.loads(target.read_text())["status"] == "ready"


def test_route_plan_passes_constraints_through() -> None:
    graph = FakeGraph(READY_STATE)
    out, err = io.StringIO(), io.StringIO()
    args = parse(
        "route",
        "plan",
        "--origin",
        "52.5,13.4",
        "--via",
        "52.45,13.2",
        "--bike-type",
        "road",
        "--target-distance-km",
        "12",
        "--prefer-surfaces",
        "asphalt,paving_stones",
        "--allow-high-traffic",
        "--loop",
        "--loop-direction",
        "counterclockwise",
    )
    assert route_cmd.run(args, out, err, graph_factory=lambda: graph) == 0
    raw = graph.invoked_with["raw_input"]  # type: ignore[index]
    assert raw["origin"] == {"lat": 52.5, "lon": 13.4}
    # A loop is single-origin: no destination key reaches the graph at all.
    assert "destination" not in raw
    assert raw["via"] == [{"lat": 52.45, "lon": 13.2}]
    assert raw["constraints"]["bike_type"] == "road"
    assert raw["constraints"]["target_distance_km"] == 12.0
    assert raw["constraints"]["prefer_surfaces"] == ["asphalt", "paving_stones"]
    assert raw["constraints"]["avoid_high_traffic_roads"] is False
    assert raw["constraints"]["return_to_origin"] is True
    assert raw["constraints"]["loop_direction"] == "counterclockwise"


@pytest.mark.parametrize(
    ("argv", "expected_fragment"),
    [
        (
            ("--origin", "a", "--destination", "b", "--loop", "--target-distance-km", "10"),
            "takes no --destination",
        ),
        (("--origin", "a", "--loop"), "--target-distance-km"),
        (("--origin", "a"), "--destination is required"),
    ],
)
def test_route_plan_loop_usage_errors(argv: tuple[str, ...], expected_fragment: str) -> None:
    out, err = io.StringIO(), io.StringIO()
    args = parse("route", "plan", *argv)
    rc = route_cmd.run(args, out, err, graph_factory=lambda: FakeGraph(READY_STATE))
    assert rc == 2
    assert expected_fragment in err.getvalue()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Berlin", "Berlin"),
        ("52.5,13.4", {"lon": 13.4, "lat": 52.5}),
        ("52.5 13.4", {"lon": 13.4, "lat": 52.5}),
        ("999,13", "999,13"),
        ("abc,def", "abc,def"),
    ],
)
def test_place_parsing(text: str, expected: Any) -> None:
    assert route_cmd._place(text) == expected


# ---------------------------------------------------------------- serve


def test_serve_start_invokes_runner() -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []
    out, err = io.StringIO(), io.StringIO()
    args = parse("serve", "start", "--port", "8010", "--host", "0.0.0.0")
    rc = serve_cmd.run(args, out, err, runner=lambda *a, **k: calls.append((a, k)))
    assert rc == 0
    assert calls == [
        (("bike_routing_agent.api:app",), {"host": "0.0.0.0", "port": 8010, "reload": False})
    ]


# ---------------------------------------------------------------- docker


def _fake_runner(returncode: int = 0) -> tuple[Any, list[list[str]]]:
    seen: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess:
        seen.append(list(command))
        return subprocess.CompletedProcess(command, returncode)

    return runner, seen


def test_docker_up_builds_compose_command() -> None:
    runner, seen = _fake_runner()
    out, err = io.StringIO(), io.StringIO()
    args = parse("docker", "up")
    assert docker_cmd.run(args, out, err, process_runner=runner) == 0
    assert seen == [["docker", "compose", "-f", "docker/compose.yaml", "up", "-d", "--build"]]


def test_docker_up_no_build_flag() -> None:
    runner, seen = _fake_runner()
    args = parse("docker", "up", "--no-build")
    assert docker_cmd.run(args, io.StringIO(), io.StringIO(), process_runner=runner) == 0
    assert seen[0][-2:] == ["up", "-d"]


def test_docker_logs_tail_and_follow() -> None:
    runner, seen = _fake_runner()
    args = parse("docker", "logs", "--tail", "5", "-f")
    assert docker_cmd.run(args, io.StringIO(), io.StringIO(), process_runner=runner) == 0
    assert seen[0][-4:] == ["logs", "--tail", "5", "--follow"]


def test_docker_down_passthrough() -> None:
    runner, seen = _fake_runner()
    args = parse("docker", "down")
    assert docker_cmd.run(args, io.StringIO(), io.StringIO(), process_runner=runner) == 0
    assert seen[0][-1] == "down"


def test_docker_propagates_compose_exit_code() -> None:
    runner, _ = _fake_runner(returncode=17)
    args = parse("docker", "ps")
    assert docker_cmd.run(args, io.StringIO(), io.StringIO(), process_runner=runner) == 17


def test_docker_missing_cli_exits_1() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess:
        raise FileNotFoundError(command[0])

    out, err = io.StringIO(), io.StringIO()
    args = parse("docker", "ps")
    assert docker_cmd.run(args, out, err, process_runner=runner) == 1
    assert "docker CLI not found" in err.getvalue()


def test_docker_missing_compose_file_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    args = parse("docker", "ps")
    rc = docker_cmd.run(args, out, err, process_runner=lambda c: subprocess.CompletedProcess(c, 0))
    assert rc == 1
    assert "compose file not found" in err.getvalue()


# ---------------------------------------------------------------- config


def test_config_show_redacts_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORS_API_KEY", "super-secret-value")
    out, err = io.StringIO(), io.StringIO()
    args = parse("config", "show")
    assert config_cmd.run(args, out, err) == 0
    assert "super-secret-value" not in out.getvalue()
    assert config_cmd.REDACTED in out.getvalue()


def test_config_check_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEOCODER_PROVIDER", raising=False)
    monkeypatch.setenv("ORS_TIMEOUT_S", "11")
    out, err = io.StringIO(), io.StringIO()
    args = parse("config", "check")
    assert config_cmd.run(args, out, err) == 0
    assert "configuration OK" in out.getvalue()
    assert '"ors_timeout_s": "11"' in out.getvalue()


def test_config_check_rejects_pelias_on_public_ors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOCODER_PROVIDER", "pelias")
    monkeypatch.setenv("ORS_BASE_URL", "https://api.openrouteservice.org")
    out, err = io.StringIO(), io.StringIO()
    args = parse("config", "check")
    assert config_cmd.run(args, out, err) == 1
    assert "pelias" in err.getvalue().lower()


# ---------------------------------------------------------------- providers


def test_providers_list_reports_profile_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTING_PROVIDER", "ors")
    out, err = io.StringIO(), io.StringIO()
    args = parse("providers", "list")
    assert providers_cmd.run(args, out, err) == 0
    payload = json.loads(out.getvalue())
    assert payload["bike_type_profiles"]["road"] == "cycling-road"
    assert payload["routing"]["configured"] == "ors"


def test_providers_list_reports_brouter_when_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTING_PROVIDER", "brouter")
    monkeypatch.setenv("BROUTER_BASE_URL", "http://127.0.0.1:17777")
    out, err = io.StringIO(), io.StringIO()
    args = parse("providers", "list")
    assert providers_cmd.run(args, out, err) == 0
    payload = json.loads(out.getvalue())
    assert payload["routing"]["configured"] == "brouter"
    assert payload["routing"]["base_url"] == "http://127.0.0.1:17777"
    assert payload["bike_type_profiles"]["gravel"] == "custom_gravel-v1"


# ------------------------------------------------- dispatch and default wiring


def test_main_dispatches_group_module_and_returns_its_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROUTING_PROVIDER", "ors")
    out, err = io.StringIO(), io.StringIO()
    assert main(["providers", "list"], stdout=out, stderr=err) == 0
    assert json.loads(out.getvalue())["routing"]["configured"] == "ors"


def test_python_m_entrypoint_exits_with_main_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setattr(sys, "argv", ["bike-router", "--version"])
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("bike_routing_agent.cli", run_name="__main__")
    assert excinfo.value.code == 0
    assert "bike-router" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("module", "label"),
    [
        (route_cmd, "unknown route command"),
        (serve_cmd, "unknown serve command"),
        (docker_cmd, "unknown docker command"),
        (config_cmd, "unknown config command"),
        (providers_cmd, "unknown providers command"),
    ],
)
def test_unknown_subcommand_guard_returns_2(module: Any, label: str) -> None:
    out, err = io.StringIO(), io.StringIO()
    assert module.run(argparse.Namespace(command="bogus"), out, err) == 2
    assert label in err.getvalue()


def test_config_show_invalid_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOCODER_PROVIDER", "pelias")
    monkeypatch.setenv("ORS_BASE_URL", "https://api.openrouteservice.org")
    out, err = io.StringIO(), io.StringIO()
    args = parse("config", "show")
    assert config_cmd.run(args, out, err) == 1
    assert "invalid configuration" in err.getvalue()


def test_serve_start_defaults_to_uvicorn_run(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    calls: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    out, err = io.StringIO(), io.StringIO()
    args = parse("serve", "start")
    assert serve_cmd.run(args, out, err) == 0
    assert calls[0][0] == (serve_cmd.APP_IMPORT_STRING,)
    assert calls[0][1] == {"host": "127.0.0.1", "port": 8000, "reload": False}


def test_docker_run_uses_default_process_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[list[str], bool]] = []

    def fake_run(
        command: list[str], check: bool = False
    ) -> subprocess.CompletedProcess:
        seen.append((list(command), check))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    out, err = io.StringIO(), io.StringIO()
    args = parse("docker", "ps")
    assert docker_cmd.run(args, out, err) == 0
    assert seen[0][0] == ["docker", "compose", "-f", "docker/compose.yaml", "ps"]
    assert seen[0][1] is False


def test_route_default_graph_factory_uses_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    import bike_routing_agent.api as api_module
    from bike_routing_agent.config import settings

    sentinel = object()
    seen: list[Any] = []

    def fake_build(cfg: Any) -> object:
        seen.append(cfg)
        return sentinel

    monkeypatch.setattr(api_module, "build_graph_for_settings", fake_build)
    assert route_cmd._default_graph_factory() is sentinel
    assert seen == [settings]


# ----------------------------------------------- history recording (issue #28)


class FakeHistory:
    def __init__(self, error: Exception | None = None) -> None:
        self.records: list[Any] = []
        self.error = error

    def save(self, record: Any) -> None:
        if self.error is not None:
            raise self.error
        self.records.append(record)


def recordable_state() -> dict[str, Any]:
    """A complete ready state (the CLI tests' READY_STATE candidate is partial)."""
    from storage.test_history import candidate, ready_state

    return ready_state([candidate()])


def run_plan(
    *argv: str,
    state: dict[str, Any] | None = None,
    history: Any = None,
    graph: Any = None,
) -> tuple[int, dict[str, Any] | None, str]:
    out, err = io.StringIO(), io.StringIO()
    args = parse("route", "plan", "--origin", "a", "--destination", "b", *argv)
    rc = route_cmd.run(
        args,
        out,
        err,
        graph_factory=lambda: graph or FakeGraph(state or recordable_state()),
        history_factory=lambda: history,
    )
    return rc, (json.loads(out.getvalue()) if out.getvalue() else None), err.getvalue()


def test_plans_are_recorded_when_a_history_is_configured() -> None:
    history = FakeHistory()
    state = {**recordable_state(), "route_id": "a" * 32}

    rc, payload, err = run_plan(state=state, history=history)

    assert rc == 0 and err == ""
    assert payload is not None and payload["plan_id"] == "a" * 32
    (record,) = history.records
    assert record.plan_id == "a" * 32 and record.status == "ready"


def test_plans_without_a_route_id_get_a_generated_plan_id() -> None:
    history = FakeHistory()
    state = recordable_state()
    state.pop("route_id")
    rc, payload, _ = run_plan(state=state, history=history)
    assert rc == 0 and payload is not None
    assert history.records[0].plan_id == payload["plan_id"]
    assert len(payload["plan_id"]) == 32


def test_failed_plans_are_recorded_too_like_the_api_does() -> None:
    history = FakeHistory()
    state = {"status": "no_route", "errors": [{"code": "no_route", "message": "x"}]}

    rc, payload, _ = run_plan(state=state, history=history)

    assert rc == 1 and payload is not None and payload["plan_id"] is not None
    assert history.records[0].status == "no_route"


def test_no_record_skips_the_history_entirely() -> None:
    history = FakeHistory()
    calls: list[int] = []

    def factory() -> Any:
        calls.append(1)
        return history

    out, err = io.StringIO(), io.StringIO()
    args = parse("route", "plan", "--origin", "a", "--destination", "b", "--no-record")
    rc = route_cmd.run(
        args, out, err, graph_factory=lambda: FakeGraph(READY_STATE), history_factory=factory
    )

    assert rc == 0 and json.loads(out.getvalue())["plan_id"] is None
    assert history.records == [] and calls == []


def test_without_a_configured_history_plan_id_is_null() -> None:
    rc, payload, err = run_plan(history=None)
    assert rc == 0 and payload is not None and payload["plan_id"] is None and err == ""


def test_a_history_outage_is_a_warning_not_a_failed_command() -> None:
    rc, payload, err = run_plan(history=FakeHistory(error=ConnectionError("db down")))
    assert rc == 0 and payload is not None and payload["plan_id"] is None
    assert "could not record the plan" in err and "db down" in err


def test_a_history_factory_failure_is_also_only_a_warning() -> None:
    def broken() -> Any:
        raise RuntimeError("psycopg missing")

    out, err = io.StringIO(), io.StringIO()
    args = parse("route", "plan", "--origin", "a", "--destination", "b")
    rc = route_cmd.run(
        args, out, err, graph_factory=lambda: FakeGraph(READY_STATE), history_factory=broken
    )
    assert rc == 0 and "psycopg missing" in err.getvalue()


def test_a_crashed_graph_records_nothing() -> None:
    history = FakeHistory()
    rc, payload, _ = run_plan(graph=FakeGraph(error=ConnectionError("ORS unreachable")),
                              history=history)
    assert rc == 1 and payload is None and history.records == []


def test_default_history_factory_follows_the_database_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bike_routing_agent.cli.commands import route as route_module

    monkeypatch.undo()  # drop the autouse stub for this test
    import bike_routing_agent.api as api_module
    import bike_routing_agent.config as config_module

    monkeypatch.setattr(config_module.settings, "database_url", None)
    assert route_module._default_history_factory() is None

    sentinel = object()
    monkeypatch.setattr(config_module.settings, "database_url", "postgresql://x/y")
    monkeypatch.setattr(api_module, "build_storage", lambda cfg: (object(), sentinel))
    assert route_module._default_history_factory() is sentinel
