"""scripts/tailscale-serve.sh: static checks (the real thing needs a tailnet)."""

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tailscale-serve.sh"


def test_script_is_executable_and_syntactically_valid():
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_script_never_publishes_to_the_public_internet():
    # Only the comment explaining that it does not may mention funnel.
    code = [
        line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")
    ]
    assert not any("funnel" in line for line in code)


FAKE_TAILSCALE = """#!/bin/sh
# Records every call; answers the two read-only queries the script makes.
echo "$@" >> "$FAKE_TS_LOG"
if [ "$1 $2 $3" = "serve status --json" ]; then
  [ "${FAKE_TS_STATUS_RC:-0}" = "0" ] || exit "$FAKE_TS_STATUS_RC"
  cat "$FAKE_TS_JSON"
elif [ "$1 $2" = "status --json" ]; then
  echo '{"Self": {"DNSName": "box.tail1.ts.net."}}'
fi
exit 0
"""

OURS = {"Proxy": "http://127.0.0.1:8010"}
OTHER_APP = {"Proxy": "http://127.0.0.1:8384"}


def serve_config(*, port="8443", handlers=None, tcp_forward=None):
    config: dict = {"TCP": {}, "Web": {}}
    if handlers is not None:
        config["TCP"][port] = {"HTTPS": True}
        config["Web"][f"box.tail1.ts.net:{port}"] = {"Handlers": handlers}
    if tcp_forward is not None:
        config["TCP"][port] = {"TCPForward": tcp_forward}
    # An unrelated entry that must never influence the decision.
    config["TCP"]["5173"] = {"TCPForward": "127.0.0.1:5173"}
    return config


def run_script(tmp_path, action, config, *, port="8443", app_port="8010", status_rc=0, raw=None):
    """Run the script against a fake tailscale; returns (result, mutating calls)."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    (fake_bin / "tailscale").write_text(FAKE_TAILSCALE)
    (fake_bin / "tailscale").chmod(0o755)
    state = tmp_path / "state.json"
    state.write_text(raw if raw is not None else json.dumps(config))
    log = tmp_path / "calls.log"
    log.write_text("")

    result = subprocess.run(
        [str(SCRIPT), action],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "TS_HTTPS_PORT": port,
            "APP_PORT": app_port,
            "FAKE_TS_JSON": str(state),
            "FAKE_TS_LOG": str(log),
            "FAKE_TS_STATUS_RC": str(status_rc),
        },
        capture_output=True,
        text=True,
    )
    mutating = [
        line for line in log.read_text().splitlines() if "--https=" in line and "status" not in line
    ]
    return result, mutating


def test_script_refuses_an_unsupported_https_port(tmp_path):
    result, mutating = run_script(tmp_path, "status", serve_config(), port="8080")

    assert result.returncode == 1
    assert "443, 8443 or 10000" in result.stderr
    assert mutating == []


def test_start_on_a_free_port_publishes_the_app(tmp_path):
    result, mutating = run_script(tmp_path, "start", serve_config())

    assert result.returncode == 0
    assert mutating == ["serve --bg --https=8443 http://127.0.0.1:8010"]
    assert "https://box.tail1.ts.net:8443" in result.stdout


def test_start_again_over_its_own_endpoint_is_allowed(tmp_path):
    config = serve_config(handlers={"/": OURS})

    result, mutating = run_script(tmp_path, "start", config)

    assert result.returncode == 0
    assert len(mutating) == 1


def test_start_on_port_443_has_no_port_suffix_in_the_url(tmp_path):
    result, _ = run_script(tmp_path, "start", serve_config(port="443"), port="443")

    assert result.stdout.strip().endswith("https://box.tail1.ts.net")


@pytest.mark.parametrize(
    "config",
    [
        serve_config(handlers={"/": OTHER_APP}),
        serve_config(handlers={"/": OURS, "/api": OTHER_APP}),
        serve_config(handlers={"/": {"Path": "/srv/files"}}),
        serve_config(tcp_forward="127.0.0.1:9000"),
    ],
    ids=["other-proxy", "extra-path", "file-handler", "tcp-forward"],
)
def test_start_refuses_to_replace_someone_elses_endpoint(tmp_path, config):
    result, mutating = run_script(tmp_path, "start", config)

    assert result.returncode == 1
    assert "in use by something else" in result.stderr
    assert "http://127.0.0.1:8010" in result.stderr
    assert mutating == []


def test_off_removes_its_own_endpoint(tmp_path):
    result, mutating = run_script(tmp_path, "off", serve_config(handlers={"/": OURS}))

    assert result.returncode == 0
    assert mutating == ["serve --https=8443 off"]


def test_off_on_a_free_port_is_a_no_op(tmp_path):
    result, mutating = run_script(tmp_path, "off", serve_config())

    assert result.returncode == 0
    assert "nothing of ours" in result.stdout
    assert mutating == []


@pytest.mark.parametrize(
    ("config", "app_port"),
    [
        (serve_config(handlers={"/": OTHER_APP}), "8010"),
        (serve_config(tcp_forward="127.0.0.1:9000"), "8010"),
        # Ours was started for 8010; off for another APP_PORT must not touch it.
        (serve_config(handlers={"/": OURS}), "9999"),
    ],
    ids=["other-proxy", "tcp-forward", "different-app-port"],
)
def test_off_refuses_to_remove_an_endpoint_that_is_not_ours(tmp_path, config, app_port):
    result, mutating = run_script(tmp_path, "off", config, app_port=app_port)

    assert result.returncode == 1
    assert "in use by something else" in result.stderr
    assert mutating == []


@pytest.mark.parametrize("action", ["start", "off"])
def test_unreadable_serve_status_blocks_any_change(tmp_path, action):
    unreadable, mutating = run_script(tmp_path, action, {}, status_rc=1)
    garbage, mutating_garbage = run_script(tmp_path, action, {}, raw="not json")

    assert unreadable.returncode == 1 and garbage.returncode == 1
    assert "refusing" in unreadable.stderr and "refusing" in garbage.stderr
    assert mutating == [] and mutating_garbage == []
