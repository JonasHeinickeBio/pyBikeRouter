"""scripts/self-hosted-bootstrap.sh against fake docker/curl (no network, no containers)."""

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "self-hosted-bootstrap.sh"
PAYLOAD = b"not really a pbf"
GOOD_MD5 = hashlib.md5(PAYLOAD).hexdigest()  # noqa: S324 - mirrors Geofabrik's published checksum

FAKE_CURL = """#!/bin/sh
# Records calls; answers the three kinds of request the script makes.
echo "curl $*" >> "$FAKE_LOG"
out=""
prev=""
for a in "$@"; do
  [ "$prev" = "-o" ] && out="$a"
  prev="$a"
  url="$a"
done
answer() { [ "$FAKE_READY" = "1" ] || exit 22; echo "$1"; exit 0; }
case "$url" in
  *.md5) [ -n "$FAKE_MD5" ] && echo "$FAKE_MD5  region.osm.pbf"; exit 0 ;;
  */ors/v2/health) answer '{"status":"ready"}' ;;
  */status) answer OK ;;
esac
if [ -n "$out" ]; then
  [ "$FAKE_DOWNLOAD_FAIL" = "1" ] && exit 22
  printf '%s' "not really a pbf" > "$out"
fi
exit 0
"""

FAKE_DOCKER = """#!/bin/sh
echo "docker $*" >> "$FAKE_LOG"
exit 0
"""


def run(tmp_path, *args, ready="1", md5=GOOD_MD5, download_fail="0", existing=False, timeout="20"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("curl", FAKE_CURL), ("docker", FAKE_DOCKER)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    if existing:
        (data / "region.osm.pbf").write_bytes(b"already here")
    log = tmp_path / "calls.log"
    log.write_text("")
    result = subprocess.run(
        [str(SCRIPT), *args],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DATA_DIR": str(data),
            "FAKE_LOG": str(log),
            "FAKE_READY": ready,
            "FAKE_MD5": md5,
            "FAKE_DOWNLOAD_FAIL": download_fail,
            "WAIT_TIMEOUT_S": timeout,
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, log.read_text().splitlines(), data / "region.osm.pbf"


def test_script_is_executable_and_syntactically_valid():
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_script_never_removes_volumes_or_data():
    code = "\n".join(
        line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    assert not re.search(r"\bdown\b|volume rm|(?<!command)\s-v\b|rm -rf", code)


def test_downloads_verifies_and_starts(tmp_path):
    result, calls, extract = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert extract.read_bytes() == PAYLOAD
    assert not extract.with_name("region.osm.pbf.part").exists()
    assert "Checksum OK" in result.stdout
    up = [c for c in calls if c.startswith("docker compose") and " up -d" in c]
    assert len(up) == 1 and "--profile self-hosted" in up[0]
    # exactly the two stack services -- a bare `up` would also start the api
    assert up[0].endswith("up -d ors-self-hosted nominatim-self-hosted")
    assert "ORS_BASE_URL=http://127.0.0.1:8080/ors" in result.stdout
    assert "GEOCODER_BASE_URL=http://127.0.0.1:8081" in result.stdout
    assert "GEOCODER_MIN_CONFIDENCE=0" in result.stdout
    assert "OpenStreetMap contributors" in result.stdout


def test_checksum_mismatch_aborts_before_anything_starts(tmp_path):
    result, calls, extract = run(tmp_path, md5="0" * 32)
    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr
    assert not extract.exists() and not extract.with_name("region.osm.pbf.part").exists()
    assert not any(c.startswith("docker") for c in calls)


def test_missing_checksum_warns_but_continues(tmp_path):
    result, _, extract = run(tmp_path, md5="")
    assert result.returncode == 0
    assert "extract not verified" in result.stderr
    assert extract.exists()


def test_failed_download_leaves_no_partial_file(tmp_path):
    result, calls, extract = run(tmp_path, download_fail="1")
    assert result.returncode == 1 and "download failed" in result.stderr
    assert not extract.exists() and not extract.with_name("region.osm.pbf.part").exists()
    assert not any(c.startswith("docker") for c in calls)


def test_existing_extract_is_kept_unless_forced(tmp_path):
    result, calls, extract = run(tmp_path, existing=True)
    assert result.returncode == 0
    assert extract.read_bytes() == b"already here"
    assert not any("-o" in c.split() for c in calls)  # nothing downloaded

    forced, _, extract = run(tmp_path, "--force", existing=True)
    assert forced.returncode == 0 and extract.read_bytes() == PAYLOAD


def test_times_out_with_a_pointer_to_the_logs(tmp_path):
    result, _, _ = run(tmp_path, ready="0", timeout="0", existing=True)
    assert result.returncode == 1
    assert "timed out" in result.stderr and "logs -f" in result.stderr


def test_status_reports_each_service_and_the_exit_code(tmp_path):
    ok, calls, _ = run(tmp_path, "status", ready="1")
    assert ok.returncode == 0
    assert "openrouteservice" in ok.stdout and ok.stdout.count("ready") >= 2
    assert not any(c.startswith("docker") for c in calls)

    down, _, _ = run(tmp_path, "status", ready="0")
    assert down.returncode == 1 and "not ready" in down.stdout


def test_unknown_argument_is_rejected(tmp_path):
    result, _, _ = run(tmp_path, "--nuke")
    assert result.returncode == 2 and "unknown argument" in result.stderr


def started_services(calls):
    (up,) = [c for c in calls if c.startswith("docker compose") and " up -d" in c]
    return up.split(" up -d ")[1].split()


def test_only_routing_starts_just_openrouteservice(tmp_path):
    result, calls, _ = run(tmp_path, "--only", "routing", existing=True)
    assert result.returncode == 0, result.stderr
    assert started_services(calls) == ["ors-self-hosted"]
    assert "ORS_BASE_URL=http://127.0.0.1:8080/ors" in result.stdout
    assert "GEOCODER_BASE_URL" not in result.stdout
    assert "still the public Nominatim" in result.stdout


def test_only_geocoding_starts_just_nominatim(tmp_path):
    result, calls, _ = run(tmp_path, "--only=geocoding", existing=True)
    assert result.returncode == 0, result.stderr
    assert started_services(calls) == ["nominatim-self-hosted"]
    assert "GEOCODER_BASE_URL=http://127.0.0.1:8081" in result.stdout
    assert "ORS_BASE_URL" not in result.stdout
    assert "still the public ORS" in result.stdout


def test_status_only_reports_and_judges_the_selected_service(tmp_path):
    ok, _, _ = run(tmp_path, "status", "--only", "routing", ready="1")
    assert ok.returncode == 0
    assert "openrouteservice" in ok.stdout and "nominatim" not in ok.stdout
    down, _, _ = run(tmp_path, "status", "--only", "geocoding", ready="0")
    assert down.returncode == 1
    assert "nominatim" in down.stdout and "openrouteservice" not in down.stdout


@pytest.mark.parametrize("args", [["--only", "everything"], ["--only"]])
def test_bad_only_values_are_usage_errors(tmp_path, args):
    result, calls, _ = run(tmp_path, *args)
    assert result.returncode == 2
    assert "--only" in result.stderr
    assert not any(c.startswith("docker") for c in calls)
