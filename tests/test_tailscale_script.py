"""scripts/tailscale-serve.sh: static checks (the real thing needs a tailnet)."""

import os
import subprocess
from pathlib import Path

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


def test_script_refuses_an_unsupported_https_port(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "tailscale").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "tailscale").chmod(0o755)

    result = subprocess.run(
        [str(SCRIPT), "status"],
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "TS_HTTPS_PORT": "8080"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "443, 8443 or 10000" in result.stderr
