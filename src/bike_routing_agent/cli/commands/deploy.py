"""``bike-router deploy`` group: build, run, check and roll back the API container.

The whole deployment as commands, instead of a recipe to remember: build the image from the
checkout (tagged with the commit, and ``latest``), (re)create the container with the right
mounts, wait until it answers ``/healthz``, and go back to the previous image when the new one
does not. ``docker`` (the compose wrapper) stays for the compose stack; this group is for one
container on one machine, including machines where Docker's published ports or build-time DNS do
not work (``--network host``, the default: the app then listens on loopback only).

Nothing here talks to a remote host: it drives the local ``docker`` CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

DEFAULT_NAME = "pybikerouter-api-hostnet"
DEFAULT_IMAGE = "pybikerouter-api"
DEFAULT_PORT = 8765
DEFAULT_BIND = "127.0.0.1"
CONTAINER_TILES = "/brouter-segments"
CONTAINER_EXPORTS = "/app/exports"
MAIN_BRANCH = "main"

Runner = Callable[[Sequence[str], bool], subprocess.CompletedProcess]
Probe = Callable[[str], bool]


@dataclass(frozen=True)
class Options:
    repo: Path
    name: str
    image: str
    port: int
    bind: str
    network: str  # "host" or "bridge"
    env_file: Path | None
    routing_provider: str | None
    brouter_url: str
    segments_dir: Path | None
    exports_dir: Path
    user: str | None

    @property
    def url(self) -> str:
        return f"http://{self.bind}:{self.port}"


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    group = subparsers.add_parser(
        "deploy", help="build, run, check and roll back the API container"
    )
    sub = group.add_subparsers(dest="command", metavar="<command>")
    if sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repo", type=Path, default=Path("."), help="the checkout (default: .)")
        p.add_argument("--name", default=DEFAULT_NAME, help="container name (default: %(default)s)")
        p.add_argument("--image", default=DEFAULT_IMAGE, help="image name (default: %(default)s)")
        p.add_argument("--port", type=int, default=DEFAULT_PORT, help="(default: %(default)s)")
        p.add_argument(
            "--bind",
            default=DEFAULT_BIND,
            help="address the app listens on (default: %(default)s = this machine only)",
        )
        p.add_argument(
            "--network",
            choices=("host", "bridge"),
            default="host",
            help="host: the container shares the host network (works where published ports "
            "or build-time DNS do not); bridge: publish --bind:--port",
        )
        p.add_argument("--env-file", type=Path, default=Path(".env"), help="(default: .env)")
        p.add_argument("--no-env-file", action="store_true", help="run without an env file")
        p.add_argument(
            "--routing-provider",
            choices=("ors", "brouter", "valhalla", "all"),
            default=None,
            help="override ROUTING_PROVIDER from the env file for this container",
        )
        p.add_argument(
            "--brouter-url",
            default="http://127.0.0.1:17777",
            help="BRouter as seen from the container (host network: the host's loopback)",
        )
        p.add_argument(
            "--segments-dir",
            type=Path,
            default=None,
            help="BRouter's tile folder, mounted writable so the web form can download tiles "
            "(default: docker/brouter/segments when it exists)",
        )
        p.add_argument(
            "--exports-dir",
            type=Path,
            default=Path.home() / ".local/share/pybikerouter/exports",
            help="where exported GPX/GeoJSON files are kept (default: %(default)s)",
        )
        p.add_argument(
            "--user",
            default=None,
            help="UID:GID to run as (default: you, so the mounted folders are writable)",
        )

    build = sub.add_parser("build", help="build the image from the checkout")
    common(build)
    build.add_argument(
        "--allow-branch", action="store_true", help=f"build a branch other than {MAIN_BRANCH}"
    )
    build.add_argument("--allow-dirty", action="store_true", help="build with uncommitted changes")
    build.add_argument("--dry-run", action="store_true", help="print the command, run nothing")

    up = sub.add_parser("up", help="build, (re)create the container, wait until healthy")
    common(up)
    up.add_argument("--no-build", action="store_true", help="use the image that is already built")
    up.add_argument(
        "--allow-branch", action="store_true", help=f"deploy a branch other than {MAIN_BRANCH}"
    )
    up.add_argument("--allow-dirty", action="store_true", help="deploy with uncommitted changes")
    up.add_argument("--no-rollback", action="store_true", help="keep a broken new container")
    up.add_argument("--wait-s", type=float, default=60.0, help="how long to wait for /healthz")
    up.add_argument("--dry-run", action="store_true", help="print the commands, run nothing")

    rollback = sub.add_parser("rollback", help="run the image that was deployed before")
    common(rollback)
    rollback.add_argument("--wait-s", type=float, default=60.0)
    rollback.add_argument("--dry-run", action="store_true")

    status = sub.add_parser("status", help="is it running, which commit, healthy, what it offers")
    common(status)
    status.add_argument("--format", choices=("text", "json"), default="text")

    logs = sub.add_parser("logs", help="the container's logs")
    common(logs)
    logs.add_argument("--tail", type=int, default=100)
    logs.add_argument("-f", "--follow", action="store_true")

    for name, text in (("stop", "stop and remove the container"), ("restart", "restart it")):
        p = sub.add_parser(name, help=text)
        common(p)

    show = sub.add_parser("config", help="print the commands 'up' would run (changes nothing)")
    common(show)

    ts = sub.add_parser("tailscale", help="publish the app to your tailnet (docs/mobile.md)")
    ts.add_argument("action", choices=("start", "status", "off"), nargs="?", default="start")
    ts.add_argument("--port", type=int, default=DEFAULT_PORT, help="the app's local port")
    ts.add_argument("--https-port", type=int, default=8443, choices=(443, 8443, 10000))
    ts.add_argument("--repo", type=Path, default=Path("."))
    return group


# ------------------------------------------------------------------- command lines


def build_options(args: argparse.Namespace) -> Options:
    repo = Path(args.repo)
    segments = args.segments_dir
    if segments is None and (repo / "docker/brouter/segments").is_dir():
        segments = repo / "docker/brouter/segments"
    return Options(
        repo=repo,
        name=args.name,
        image=args.image,
        port=args.port,
        bind=args.bind,
        network=args.network,
        env_file=None if args.no_env_file else Path(args.env_file),
        routing_provider=args.routing_provider,
        brouter_url=args.brouter_url,
        segments_dir=segments,
        exports_dir=Path(args.exports_dir),
        user=args.user if args.user is not None else f"{os.getuid()}:{os.getgid()}",
    )


def build_command(opts: Options, commit: str, *, dirty: bool) -> list[str]:
    cmd = ["docker", "build"]
    if opts.network == "host":
        cmd += ["--network", "host"]  # build containers have no DNS on some machines
    cmd += [
        "-f", str(opts.repo / "docker/Dockerfile"),
        "-t", f"{opts.image}:latest",
        "-t", f"{opts.image}:{commit}",
        "--label", f"org.opencontainers.image.revision={commit}",
        "--label", f"io.pybikerouter.dirty={'true' if dirty else 'false'}",
        str(opts.repo),
    ]  # fmt: skip
    return cmd


def run_command(opts: Options, image_ref: str) -> list[str]:
    cmd = ["docker", "run", "-d", "--name", opts.name, "--restart", "unless-stopped"]
    if opts.network == "host":
        cmd += ["--network", "host"]
        listen_host, listen_port = opts.bind, opts.port
    else:
        cmd += ["-p", f"{opts.bind}:{opts.port}:8000"]
        listen_host, listen_port = "0.0.0.0", 8000  # noqa: S104 - inside the container only
    if opts.user:
        cmd += ["--user", opts.user]
    if opts.env_file is not None:
        cmd += ["--env-file", str(opts.env_file)]
    env = {
        "EXPORT_DIR": CONTAINER_EXPORTS,
        "APP_HOST": listen_host,
        "APP_PORT": str(listen_port),
    }
    if opts.routing_provider:
        env["ROUTING_PROVIDER"] = opts.routing_provider
    if opts.routing_provider in ("brouter", "all"):
        env["BROUTER_BASE_URL"] = opts.brouter_url
    if opts.segments_dir is not None:
        env["BROUTER_SEGMENTS_DIR"] = CONTAINER_TILES
    for key, value in env.items():
        cmd += ["-e", f"{key}={value}"]
    if opts.segments_dir is not None:
        cmd += ["-v", f"{opts.segments_dir.resolve()}:{CONTAINER_TILES}"]
    cmd += ["-v", f"{opts.exports_dir.resolve()}:{CONTAINER_EXPORTS}", image_ref]
    return cmd


def _shown(cmd: Sequence[str]) -> str:
    return "$ " + " ".join(shlex.quote(c) for c in cmd)


# --------------------------------------------------------------------------- running


def _default_runner(cmd: Sequence[str], capture: bool) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, capture_output=capture, text=True)  # noqa: S603


def _default_probe(url: str) -> bool:
    import httpx

    try:
        return httpx.get(url, timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


class Deployer:
    def __init__(
        self,
        opts: Options,
        stdout: IO[str],
        stderr: IO[str],
        *,
        runner: Runner | None = None,
        probe: Probe | None = None,
        sleep: Callable[[float], None] = time.sleep,
        dry_run: bool = False,
    ) -> None:
        self.opts = opts
        self.out = stdout
        self.err = stderr
        self._runner = runner or _default_runner
        self._probe = probe or _default_probe
        self._sleep = sleep
        self.dry_run = dry_run

    def run(
        self, cmd: Sequence[str], *, capture: bool = False, effect: bool = True
    ) -> subprocess.CompletedProcess:
        """Run (or, in a dry run, only print) a command. Reads are always run."""
        print(_shown(cmd), file=self.out)
        if self.dry_run and effect:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return self._runner(cmd, capture)

    def read(self, cmd: Sequence[str]) -> str | None:
        """A command's stdout, or ``None`` when it failed (never printed, never skipped)."""
        try:
            done = self._runner(cmd, True)
        except FileNotFoundError:
            return None
        return str(done.stdout).strip() if done.returncode == 0 else None

    # -- repository state
    def git(self, *args: str) -> str | None:
        return self.read(["git", "-C", str(self.opts.repo), *args])

    def checkout_state(self) -> tuple[str, str, bool]:
        commit = self.git("rev-parse", "--short", "HEAD") or "unknown"
        branch = self.git("branch", "--show-current") or "(detached)"
        dirty = bool(self.git("status", "--porcelain"))
        return commit, branch, dirty

    # -- steps
    def build(self, allow_branch: bool, allow_dirty: bool) -> int:
        dockerfile = self.opts.repo / "docker/Dockerfile"
        if not dockerfile.is_file():
            print(
                f"error: {dockerfile} not found (run from the repository root or pass --repo)",
                file=self.err,
            )
            return EXIT_FAILURE
        commit, branch, dirty = self.checkout_state()
        if branch != MAIN_BRANCH and not allow_branch:
            print(
                f"error: the checkout is on '{branch}', not {MAIN_BRANCH}; deploy from "
                f"{MAIN_BRANCH} "
                f"or pass --allow-branch",
                file=self.err,
            )
            return EXIT_USAGE
        if dirty and not allow_dirty:
            print(
                "error: uncommitted changes in the checkout; commit them or pass --allow-dirty",
                file=self.err,
            )
            return EXIT_USAGE
        print(f"building {commit} from {branch}{' (dirty)' if dirty else ''}", file=self.out)
        done = self.run(build_command(self.opts, commit, dirty=dirty))
        if done.returncode != 0:
            print("error: the build failed", file=self.err)
            return EXIT_FAILURE
        return EXIT_OK

    def _prepare_mounts(self) -> str | None:
        if self.opts.env_file is not None and not self.opts.env_file.is_file():
            return f"env file {self.opts.env_file} not found (pass --env-file, or --no-env-file)"
        if self.opts.segments_dir is not None and not self.opts.segments_dir.is_dir():
            return f"tile folder {self.opts.segments_dir} is not a directory"
        if not self.dry_run:
            self.opts.exports_dir.mkdir(parents=True, exist_ok=True)
        return None

    def container_image_id(self) -> str | None:
        return self.read(["docker", "inspect", "-f", "{{.Image}}", self.opts.name])

    def wait_healthy(self, wait_s: float) -> bool:
        if self.dry_run:
            return True
        deadline = time.monotonic() + wait_s
        while True:
            if self._probe(f"{self.opts.url}/healthz"):
                return True
            if time.monotonic() >= deadline:
                return False
            self._sleep(1.0)

    def start(self, image_ref: str, wait_s: float) -> bool:
        """Replace the container with one running ``image_ref``; True when it became healthy."""
        problem = self._prepare_mounts()
        if problem:
            print(f"error: {problem}", file=self.err)
            return False
        self.run(["docker", "rm", "-f", self.opts.name], capture=True)
        done = self.run(run_command(self.opts, image_ref))
        if done.returncode != 0:
            print("error: docker could not start the container", file=self.err)
            return False
        return self.wait_healthy(wait_s)

    def up(self, args: argparse.Namespace) -> int:
        if not args.no_build:
            code = self.build(args.allow_branch, args.allow_dirty)
            if code != EXIT_OK:
                return code
        previous = self.container_image_id()
        if previous:
            self.run(["docker", "tag", previous, f"{self.opts.image}:previous"])
        if self.start(f"{self.opts.image}:latest", args.wait_s):
            self.report_up()
            return EXIT_OK
        print(
            f"error: the new container did not answer {self.opts.url}/healthz "
            f"in {args.wait_s:.0f}s",
            file=self.err,
        )
        tail = self.read(["docker", "logs", "--tail", "15", self.opts.name])
        if tail:
            print(tail, file=self.err)
        if args.no_rollback or not previous:
            print(
                "left as it is" if args.no_rollback else "no previous image to go back to",
                file=self.err,
            )
            return EXIT_FAILURE
        print("rolling back to the previous image", file=self.err)
        if self.start(f"{self.opts.image}:previous", args.wait_s):
            print("rolled back; the previous version is running again", file=self.out)
        else:
            print("error: the previous image did not become healthy either", file=self.err)
        return EXIT_FAILURE

    def rollback(self, wait_s: float) -> int:
        if self.read(["docker", "image", "inspect", f"{self.opts.image}:previous"]) is None:
            print(
                f"error: no {self.opts.image}:previous image: nothing to roll back to",
                file=self.err,
            )
            return EXIT_FAILURE
        current = self.container_image_id()
        if current:
            self.run(["docker", "tag", current, f"{self.opts.image}:rolled-back"])
        if not self.start(f"{self.opts.image}:previous", wait_s):
            print("error: the previous image did not become healthy", file=self.err)
            return EXIT_FAILURE
        self.report_up()
        return EXIT_OK

    def report_up(self) -> None:
        print(f"up: {self.opts.url}  (container {self.opts.name})", file=self.out)

    def status(self, fmt: str) -> int:
        raw = self.read(["docker", "inspect", self.opts.name])
        try:
            info: dict[str, Any] = json.loads(raw or "")[0]
        except (ValueError, IndexError):
            print(f"{self.opts.name}: not running (no such container)", file=self.out)
            return EXIT_FAILURE
        state = info.get("State", {})
        image = info.get("Image", "")
        revision = self.read(
            [
                "docker",
                "image",
                "inspect",
                "-f",
                '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                image,
            ]
        )
        dirty = self.read(
            [
                "docker",
                "image",
                "inspect",
                "-f",
                '{{index .Config.Labels "io.pybikerouter.dirty"}}',
                image,
            ]
        )
        healthy = self._probe(f"{self.opts.url}/healthz")
        caps = self._capabilities() if healthy else None
        data = {
            "name": self.opts.name,
            "state": state.get("Status"),
            "started_at": state.get("StartedAt"),
            "image": image[:19],
            "commit": revision or None,
            "dirty": dirty == "true",
            "url": self.opts.url,
            "healthy": healthy,
            "capabilities": caps,
        }
        if fmt == "json":
            print(json.dumps(data, indent=2), file=self.out)
        else:
            print(
                f"{data['name']}: {data['state']}, "
                f"{'healthy' if healthy else 'NOT answering /healthz'}",
                file=self.out,
            )
            print(f"  url:     {data['url']}", file=self.out)
            print(
                f"  commit:  {data['commit'] or 'unknown'}"
                f"{' (built dirty)' if data['dirty'] else ''}",
                file=self.out,
            )
            print(f"  started: {data['started_at']}", file=self.out)
            if caps:
                shown = ", ".join(k for k, v in caps.items() if v is True)
                print(
                    f"  features: {shown or 'none'}; engines: {', '.join(caps.get('engines', []))}",
                    file=self.out,
                )
        return EXIT_OK if state.get("Status") == "running" and healthy else EXIT_FAILURE

    def _capabilities(self) -> dict[str, Any] | None:
        import httpx

        try:
            response = httpx.get(f"{self.opts.url}/v1/capabilities", timeout=3.0)
            return dict(response.json()) if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None


def run(
    args: argparse.Namespace,
    stdout: IO[str],
    stderr: IO[str],
    *,
    runner: Runner | None = None,
    probe: Probe | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    command = args.command
    if command == "tailscale":
        return _tailscale(args, stdout, stderr, runner)
    if command not in ("build", "up", "rollback", "status", "logs", "stop", "restart", "config"):
        print("unknown deploy command", file=stderr)
        return EXIT_USAGE

    opts = build_options(args)
    deployer = Deployer(
        opts,
        stdout,
        stderr,
        runner=runner,
        probe=probe,
        sleep=sleep,
        dry_run=bool(getattr(args, "dry_run", False)) or command == "config",
    )
    try:
        if command == "config":
            commit, branch, dirty = deployer.checkout_state()
            print(f"# {commit} on {branch}{' (dirty)' if dirty else ''}", file=stdout)
            print(_shown(build_command(opts, commit, dirty=dirty)), file=stdout)
            print(_shown(run_command(opts, f"{opts.image}:latest")), file=stdout)
            return EXIT_OK
        if command == "build":
            return deployer.build(args.allow_branch, args.allow_dirty)
        if command == "up":
            return deployer.up(args)
        if command == "rollback":
            return deployer.rollback(args.wait_s)
        if command == "status":
            return deployer.status(args.format)
        if command == "logs":
            cmd = [
                "docker",
                "logs",
                "--tail",
                str(args.tail),
                *(["--follow"] if args.follow else []),
                opts.name,
            ]
            return int(deployer.run(cmd).returncode)
        if command == "stop":
            return int(deployer.run(["docker", "rm", "-f", opts.name]).returncode)
        return int(deployer.run(["docker", "restart", opts.name]).returncode)
    except FileNotFoundError:
        print("docker CLI not found on PATH", file=stderr)
        return EXIT_FAILURE


def _tailscale(
    args: argparse.Namespace, stdout: IO[str], stderr: IO[str], runner: Runner | None
) -> int:
    script = Path(args.repo) / "scripts/tailscale-serve.sh"
    if not script.is_file():
        print(
            f"error: {script} not found (run from the repository root or pass --repo)", file=stderr
        )
        return EXIT_FAILURE
    env_note = f"APP_PORT={args.port} TS_HTTPS_PORT={args.https_port}"
    print(f"$ {env_note} {script} {args.action}", file=stdout)
    environment = {**os.environ, "APP_PORT": str(args.port), "TS_HTTPS_PORT": str(args.https_port)}
    try:
        if runner is not None:
            return int(runner([str(script), args.action], False).returncode)
        return int(
            subprocess.run([str(script), args.action], env=environment, check=False).returncode
        )  # noqa: S603
    except FileNotFoundError:
        print("cannot run the script (bash missing?)", file=stderr)
        return EXIT_FAILURE
