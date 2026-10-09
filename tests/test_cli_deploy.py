"""``bike-router deploy``: the commands it builds, the order it runs them, and what it does when
the new container does not come up. Docker and git are replaced by a scripted runner."""

from __future__ import annotations

import io
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from bike_routing_agent.cli.commands import deploy as deploy_cmd
from bike_routing_agent.cli.main import build_parser


def parse(*argv: str) -> Any:
    return build_parser().parse_args(["deploy", *argv])


class Scripted:
    """Records every command; answers reads from ``answers`` (prefix -> (code, stdout))."""

    def __init__(self, answers: dict[tuple[str, ...], tuple[int, str]] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.answers = answers or {}

    def __call__(self, cmd: Sequence[str], capture: bool) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        for prefix, (code, out) in self.answers.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                return subprocess.CompletedProcess(cmd, code, out, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def ran(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if tuple(c[: len(prefix)]) == prefix]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "docker").mkdir()
    (tmp_path / "docker/Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "docker/brouter/segments").mkdir(parents=True)
    (tmp_path / ".env").write_text("ORS_API_KEY=secret\n")
    return tmp_path


def git_answers(branch: str = "main", dirty: str = "") -> dict[tuple[str, ...], tuple[int, str]]:
    return {
        ("git", "-C"): (0, "abc1234"),  # rev-parse (the most specific ones follow)
        **{},
    } | {
        ("git",): (0, "abc1234"),
    }


class Docker(Scripted):
    """Git and docker behave like a clean main checkout unless told otherwise."""

    def __init__(
        self,
        *,
        branch: str = "main",
        dirty: bool = False,
        existing_image: str | None = None,
        previous: bool = True,
    ) -> None:
        super().__init__()
        self.branch, self.dirty = branch, dirty
        self.existing_image, self.has_previous = existing_image, previous

    def __call__(self, cmd: Sequence[str], capture: bool) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        c = list(cmd)
        out, code = "", 0
        if c[0] == "git":
            if "rev-parse" in c:
                out = "abc1234"
            elif "branch" in c:
                out = self.branch
            elif "status" in c:
                out = " M file.py" if self.dirty else ""
        elif c[:2] == ["docker", "inspect"] and "-f" in c:
            code, out = (0, self.existing_image) if self.existing_image else (1, "")
        elif c[:3] == ["docker", "image", "inspect"]:
            code = 0 if self.has_previous else 1
        return subprocess.CompletedProcess(cmd, code, out, "")


def run(repo: Path, *argv: str, docker: Docker | None = None, healthy: list[bool] | None = None,
        ) -> tuple[int, str, str, Docker]:  # fmt: skip
    docker = docker or Docker()
    answers = iter(healthy if healthy is not None else [True])
    last = {"v": True}

    def probe(url: str) -> bool:
        last["v"] = next(answers, last["v"])
        return last["v"]

    out, err = io.StringIO(), io.StringIO()
    common = ["--repo", str(repo), "--env-file", str(repo / ".env"),
              "--exports-dir", str(repo / "exports"), "--user", "1000:1000"]  # fmt: skip
    args = parse(argv[0], *common, *argv[1:])
    code = deploy_cmd.run(args, out, err, runner=docker, probe=probe, sleep=lambda s: None)
    return code, out.getvalue(), err.getvalue(), docker


# --------------------------------------------------------------------- command lines


def options(repo: Path, *argv: str) -> deploy_cmd.Options:
    args = parse("up", "--repo", str(repo), "--env-file", str(repo / ".env"),
                 "--exports-dir", str(repo / "exports"), "--user", "1000:1000", *argv)  # fmt: skip
    return deploy_cmd.build_options(args)


def test_the_build_uses_the_host_network_and_tags_the_commit_and_latest(repo: Path):
    cmd = deploy_cmd.build_command(options(repo), "abc1234", dirty=False)
    assert cmd[:4] == ["docker", "build", "--network", "host"]
    assert "pybikerouter-api:latest" in cmd and "pybikerouter-api:abc1234" in cmd
    assert "org.opencontainers.image.revision=abc1234" in cmd
    assert "io.pybikerouter.dirty=false" in cmd and cmd[-1] == str(repo)
    bridge = deploy_cmd.build_command(options(repo, "--network", "bridge"), "x", dirty=True)
    assert "--network" not in bridge and "io.pybikerouter.dirty=true" in bridge


def test_the_run_command_mounts_tiles_and_exports_and_stays_on_loopback(repo: Path):
    cmd = deploy_cmd.run_command(options(repo, "--routing-provider", "brouter"), "img:latest")
    joined = " ".join(cmd)
    assert cmd[:6] == ["docker", "run", "-d", "--name", "pybikerouter-api-hostnet", "--restart"]
    assert "--network host" in joined and "--user 1000:1000" in joined
    assert f"--env-file {repo / '.env'}" in joined
    assert "-e APP_HOST=127.0.0.1" in joined and "-e APP_PORT=8765" in joined
    assert "-e ROUTING_PROVIDER=brouter" in joined
    assert "-e BROUTER_BASE_URL=http://127.0.0.1:17777" in joined
    assert "-e BROUTER_SEGMENTS_DIR=/brouter-segments" in joined
    assert f"-v {(repo / 'docker/brouter/segments').resolve()}:/brouter-segments" in joined
    assert f"-v {(repo / 'exports').resolve()}:/app/exports" in joined
    assert cmd[-1] == "img:latest"


def test_without_a_routing_override_the_env_file_decides_and_brouter_is_not_wired(repo: Path):
    joined = " ".join(deploy_cmd.run_command(options(repo), "img"))
    assert "ROUTING_PROVIDER" not in joined and "BROUTER_BASE_URL" not in joined


def test_bridge_mode_publishes_the_port_and_listens_inside_on_8000(repo: Path):
    joined = " ".join(
        deploy_cmd.run_command(options(repo, "--network", "bridge", "--port", "9000"), "i")
    )
    assert "-p 127.0.0.1:9000:8000" in joined and "--network host" not in joined
    assert "-e APP_PORT=8000" in joined and "-e APP_HOST=0.0.0.0" in joined


def test_no_env_file_and_no_tile_folder_leave_those_flags_out(tmp_path: Path):
    args = parse(
        "up", "--repo", str(tmp_path), "--no-env-file", "--exports-dir", str(tmp_path / "e")
    )
    joined = " ".join(deploy_cmd.run_command(deploy_cmd.build_options(args), "i"))
    assert "--env-file" not in joined and "BROUTER_SEGMENTS_DIR" not in joined


# ------------------------------------------------------------------------------ up


def test_up_builds_replaces_the_container_and_waits_until_it_answers(repo: Path):
    code, out, _, docker = run(repo, "up")
    assert code == 0 and "up: http://127.0.0.1:8765" in out
    kinds = [c[:2] for c in docker.calls if c[0] == "docker"]
    assert (
        kinds.index(["docker", "build"])
        < kinds.index(["docker", "rm"])
        < kinds.index(["docker", "run"])
    )
    assert (repo / "exports").is_dir()  # created for the mount
    assert not docker.ran("docker", "tag")  # nothing was running before: no previous image


def test_up_keeps_the_running_image_as_previous_before_replacing_it(repo: Path):
    _, _, _, docker = run(repo, "up", docker=Docker(existing_image="sha256:old"))
    tag = docker.ran("docker", "tag")[0]
    assert tag == ["docker", "tag", "sha256:old", "pybikerouter-api:previous"]
    assert docker.calls.index(tag) < docker.calls.index(docker.ran("docker", "rm")[0])


def test_a_new_container_that_never_answers_is_rolled_back(repo: Path):
    docker = Docker(existing_image="sha256:old")
    # Unhealthy on the new image, healthy again on the previous one.
    code, out, err, docker = run(repo, "up", "--wait-s", "0", docker=docker, healthy=[False, True])
    assert code == 1 and "did not answer" in err and "rolling back" in err
    assert "previous version is running again" in out
    runs = docker.ran("docker", "run")
    assert runs[0][-1] == "pybikerouter-api:latest" and runs[1][-1] == "pybikerouter-api:previous"


def test_if_the_previous_image_is_broken_too_it_says_so(repo: Path):
    code, _, err, _ = run(
        repo,
        "up",
        "--wait-s",
        "0",
        docker=Docker(existing_image="sha256:old"),
        healthy=[False, False],
    )
    assert code == 1 and "did not become healthy either" in err


def test_without_a_previous_container_there_is_nothing_to_roll_back_to(repo: Path):
    code, _, err, docker = run(repo, "up", "--wait-s", "0", healthy=[False])
    assert code == 1 and "no previous image" in err and len(docker.ran("docker", "run")) == 1


def test_no_rollback_keeps_the_broken_container_for_inspection(repo: Path):
    code, _, err, docker = run(
        repo, "up", "--wait-s", "0", "--no-rollback",
        docker=Docker(existing_image="sha256:old"), healthy=[False],
    )  # fmt: skip
    assert code == 1 and "left as it is" in err and len(docker.ran("docker", "run")) == 1


def test_deploying_a_branch_or_uncommitted_changes_needs_a_flag(repo: Path):
    code, _, err, docker = run(repo, "up", docker=Docker(branch="feat/x"))
    assert code == 2 and "not main" in err and not docker.ran("docker", "build")
    code, _, err, docker = run(repo, "up", docker=Docker(dirty=True))
    assert code == 2 and "uncommitted changes" in err and not docker.ran("docker", "build")
    code, _, _, docker = run(repo, "up", "--allow-branch", "--allow-dirty",
                             docker=Docker(branch="feat/x", dirty=True))  # fmt: skip
    assert code == 0 and docker.ran("docker", "build")
    assert "io.pybikerouter.dirty=true" in docker.ran("docker", "build")[0]


def test_no_build_deploys_the_existing_image_whatever_the_checkout_looks_like(repo: Path):
    code, _, _, docker = run(repo, "up", "--no-build", docker=Docker(branch="feat/x", dirty=True))
    assert code == 0 and not docker.ran("docker", "build") and docker.ran("docker", "run")


def test_a_dry_run_prints_the_mutating_commands_and_runs_none_of_them(repo: Path):
    code, out, _, docker = run(repo, "up", "--dry-run", docker=Docker(existing_image="sha256:old"))
    assert code == 0 and "$ docker build" in out and "$ docker run" in out
    assert not docker.ran("docker", "build") and not docker.ran("docker", "run")
    assert not docker.ran("docker", "rm") and not docker.ran("docker", "tag")
    assert not (repo / "exports").exists()


def test_config_shows_what_up_would_run_and_changes_nothing(repo: Path):
    code, out, _, docker = run(repo, "config", "--routing-provider", "brouter")
    assert code == 0 and "# abc1234 on main" in out
    assert "$ docker build --network host" in out and "$ docker run -d --name" in out
    assert "ROUTING_PROVIDER=brouter" in out
    assert not docker.ran("docker", "build") and not docker.ran("docker", "run")


def test_problems_with_the_setup_are_named(repo: Path, tmp_path: Path):
    (repo / "docker/Dockerfile").unlink()
    code, _, err, _ = run(repo, "build")
    assert code == 1 and "Dockerfile" in err
    (repo / "docker/Dockerfile").write_text("x")
    (repo / ".env").unlink()
    code, _, err, docker = run(repo, "up", "--no-build")
    assert code == 1 and "env file" in err and not docker.ran("docker", "run")


def test_a_failing_build_or_run_stops_the_deployment(repo: Path):
    class FailingBuild(Docker):
        def __call__(self, cmd: Sequence[str], capture: bool) -> subprocess.CompletedProcess:
            done = super().__call__(cmd, capture)
            return (
                subprocess.CompletedProcess(cmd, 1, "", "")
                if cmd[:2] == ["docker", "build"]
                else done
            )

    code, _, err, docker = run(repo, "up", docker=FailingBuild())
    assert code == 1 and "build failed" in err and not docker.ran("docker", "run")

    class FailingRun(Docker):
        def __call__(self, cmd: Sequence[str], capture: bool) -> subprocess.CompletedProcess:
            done = super().__call__(cmd, capture)
            return (
                subprocess.CompletedProcess(cmd, 125, "", "")
                if cmd[:2] == ["docker", "run"]
                else done
            )

    code, _, err, _ = run(repo, "up", docker=FailingRun())
    assert code == 1 and "could not start" in err


# ----------------------------------------------------------------- rollback and others


def test_rollback_runs_the_previous_image_and_needs_one(repo: Path):
    code, _, err, docker = run(repo, "rollback", docker=Docker(existing_image="sha256:new"))
    assert code == 0 and docker.ran("docker", "run")[0][-1] == "pybikerouter-api:previous"
    assert ["docker", "tag", "sha256:new", "pybikerouter-api:rolled-back"] in docker.calls
    code, _, err, docker = run(repo, "rollback", docker=Docker(previous=False))
    assert code == 1 and "nothing to roll back to" in err and not docker.ran("docker", "run")


def test_logs_stop_and_restart_are_the_matching_docker_commands(repo: Path):
    _, _, _, docker = run(repo, "logs", "--tail", "5", "-f")
    assert docker.ran("docker", "logs")[0] == [
        "docker", "logs", "--tail", "5", "--follow", "pybikerouter-api-hostnet"
    ]  # fmt: skip
    assert run(repo, "stop")[3].ran("docker", "rm", "-f", "pybikerouter-api-hostnet")
    assert run(repo, "restart")[3].ran("docker", "restart", "pybikerouter-api-hostnet")


class Inspected(Docker):
    def __call__(self, cmd: Sequence[str], capture: bool) -> subprocess.CompletedProcess:
        c = list(cmd)
        if c[:2] == ["docker", "inspect"] and "-f" not in c:
            self.calls.append(c)
            state = {"Status": "running", "StartedAt": "2026-10-09T12:00:00Z"}
            body = json.dumps([{"State": state, "Image": "sha256:" + "a" * 64}])
            return subprocess.CompletedProcess(cmd, 0, body, "")
        done = super().__call__(cmd, capture)
        if c[:3] == ["docker", "image", "inspect"]:
            label = "true" if "dirty" in " ".join(c) else "abc1234"
            return subprocess.CompletedProcess(cmd, 0, label, "")
        return done


def test_status_reports_state_commit_and_health(repo: Path):
    code, out, _, _ = run(repo, "status", docker=Inspected())
    assert code == 0 and "pybikerouter-api-hostnet: running, healthy" in out
    assert "commit:  abc1234 (built dirty)" in out
    data = json.loads(run(repo, "status", "--format", "json", docker=Inspected())[1])
    assert data["healthy"] is True and data["commit"] == "abc1234" and data["url"].endswith(":8765")


def test_status_fails_when_it_is_not_running_or_not_answering(repo: Path):
    code, out, _, _ = run(repo, "status")  # no such container: docker inspect "fails"
    assert code == 1 and "not running" in out
    code, out, _, _ = run(repo, "status", docker=Inspected(), healthy=[False])
    assert code == 1 and "NOT answering" in out


def test_tailscale_runs_the_repo_script_with_the_chosen_ports(repo: Path):
    script = repo / "scripts/tailscale-serve.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n")
    calls: list[Any] = []

    def runner(cmd: Sequence[str], capture: bool) -> subprocess.CompletedProcess:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    out, err = io.StringIO(), io.StringIO()
    code = deploy_cmd.run(parse("tailscale", "status", "--repo", str(repo), "--port", "9001"),
                          out, err, runner=runner)  # fmt: skip
    assert code == 0 and calls == [[str(script), "status"]]
    assert "APP_PORT=9001 TS_HTTPS_PORT=8443" in out.getvalue()
    missing = deploy_cmd.run(
        parse("tailscale", "--repo", str(repo / "nope")), out, err, runner=runner
    )
    assert missing == 1 and "not found" in err.getvalue()


def test_a_missing_docker_cli_is_reported(repo: Path):
    def broken(cmd: Sequence[str], capture: bool) -> subprocess.CompletedProcess:
        raise FileNotFoundError("docker")

    out, err = io.StringIO(), io.StringIO()
    args = parse("stop", "--repo", str(repo))
    assert deploy_cmd.run(args, out, err, runner=broken) == 1
    assert "docker CLI not found" in err.getvalue()


def test_the_group_is_registered():
    assert "deploy" in build_parser().format_help()
