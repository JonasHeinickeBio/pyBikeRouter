"""``bike-router brouter`` group: BRouter map tiles (which a trip needs, what is on disk, fetching).

BRouter only routes where it has the 5x5 degree ``.rd5`` tiles. These commands are the terminal
counterpart of the web form's *Map data missing* card (docs/providers.md#brouter-map-coverage):
nothing is downloaded without showing the size and asking (or ``--yes``).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from pathlib import Path
from typing import IO

from bike_routing_agent.cli.render import megabytes, table

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    group = subparsers.add_parser("brouter", help="BRouter map tiles: needed, present, download")
    sub = group.add_subparsers(dest="command", metavar="<command>")
    if sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")

    needed = sub.add_parser("needed", help="which map tiles a trip touches (no network)")
    needed.add_argument("--origin", required=True, metavar="LAT,LON")
    needed.add_argument("--destination", required=True, metavar="LAT,LON")
    needed.add_argument("--via", action="append", default=[], metavar="LAT,LON")
    needed.add_argument("--dir", type=Path, default=None, help="BRouter's tile folder")

    info = sub.add_parser("info", help="are the tiles on disk, and how big are they at the source")
    info.add_argument("names", nargs="+", metavar="TILE", help="e.g. W5_N50")
    info.add_argument("--dir", type=Path, default=None, help="BRouter's tile folder")

    download = sub.add_parser("download", help="download tiles (large: ~100-200 MB each)")
    download.add_argument("names", nargs="+", metavar="TILE", help="e.g. W5_N50")
    download.add_argument("--dir", type=Path, default=None, help="BRouter's tile folder")
    download.add_argument("--yes", action="store_true", help="do not ask before downloading")
    return group


def _lat_lon(text: str) -> tuple[float, float]:
    try:
        lat, lon = (float(p) for p in text.replace(" ", "").split(","))
    except ValueError:
        raise ValueError(f"{text!r} is not LAT,LON (place names are not looked up here)") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"{text!r} is out of range")
    return lon, lat


def _directory(arg_dir: Path | None, settings_dir: str | None) -> Path | None:
    chosen = arg_dir or (Path(settings_dir) if settings_dir else None)
    return chosen


def run(
    args: argparse.Namespace,
    stdout: IO[str],
    stderr: IO[str],
    *,
    confirm: Callable[[str], bool] | None = None,
    poll_interval_s: float = 1.0,
) -> int:
    from bike_routing_agent.config import Settings
    from bike_routing_agent.providers.brouter_downloads import SegmentDownloader
    from bike_routing_agent.providers.brouter_segments import SEGMENTS_BASE_URL, segments_for_points

    cfg = Settings()
    directory = _directory(args.dir, cfg.brouter_segments_dir)

    if args.command == "needed":
        try:
            points = [
                _lat_lon(args.origin),
                *(_lat_lon(v) for v in args.via),
                _lat_lon(args.destination),
            ]
        except ValueError as exc:
            print(f"invalid request: {exc}", file=stderr)
            return EXIT_USAGE
        rows = []
        for name in segments_for_points(points):
            if directory is None:
                state = "unknown (no --dir / BROUTER_SEGMENTS_DIR)"
            else:
                state = "on disk" if (directory / f"{name}.rd5").is_file() else "MISSING"
            rows.append([name, state, f"{SEGMENTS_BASE_URL}{name}.rd5"])
        print(table(rows, ["TILE", "STATE", "SOURCE"]), file=stdout)
        return EXIT_OK

    if args.command not in ("info", "download"):
        print("unknown brouter command", file=stderr)
        return EXIT_USAGE

    if directory is None or not directory.is_dir():
        print(
            "error: BRouter's tile folder is needed: pass --dir or set BROUTER_SEGMENTS_DIR "
            "(an existing directory, the one BRouter reads its .rd5 tiles from)",
            file=stderr,
        )
        return EXIT_FAILURE
    downloader = SegmentDownloader(
        directory, base_url=cfg.brouter_segments_url, max_bytes=cfg.brouter_segments_max_mb * 2**20
    )

    try:
        infos = asyncio.run(downloader.inspect(list(args.names)))
    except ValueError as exc:
        print(f"invalid request: {exc}", file=stderr)
        return EXIT_USAGE
    rows = [[i.name, "on disk" if i.present else "missing", megabytes(i.size_bytes)] for i in infos]
    print(table(rows, ["TILE", "STATE", "SIZE AT SOURCE"]), file=stdout)
    if args.command == "info":
        print(f"free disk space: {downloader.free_bytes() / 1e9:.0f} GB", file=stdout)
        return EXIT_OK

    todo = [i for i in infos if not i.present]
    if not todo:
        print("nothing to download: every tile is on disk", file=stdout)
        return EXIT_OK
    total = sum(i.size_bytes or 0 for i in todo)
    question = (
        f"Download {', '.join(i.name for i in todo)} "
        f"({megabytes(total) if total else 'size unknown'}) from {cfg.brouter_segments_url} "
        f"into {directory}?"
    )
    if not args.yes:
        ask = confirm or (lambda q: _ask(q, stderr))
        if not ask(question):
            print("not downloaded", file=stderr)
            return EXIT_FAILURE
    return asyncio.run(
        _download(downloader, [i.name for i in todo], stdout, stderr, poll_interval_s)
    )


def _ask(question: str, stderr: IO[str]) -> bool:
    if not sys.stdin.isatty():
        print(f"{question}\nnot asking without a terminal: pass --yes to download", file=stderr)
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


async def _download(
    downloader: object, names: list[str], stdout: IO[str], stderr: IO[str], interval_s: float
) -> int:
    from bike_routing_agent.providers.brouter_downloads import DownloadBusyError, SegmentDownloader

    assert isinstance(downloader, SegmentDownloader)
    try:
        job = downloader.start(names)
    except (DownloadBusyError, ValueError) as exc:
        print(f"error: {exc}", file=stderr)
        return EXIT_FAILURE
    last = ""
    while job.state == "running":
        line = "  ".join(
            f"{s.name}: {s.state}"
            + (
                f" {100 * s.bytes // s.total_bytes}%"
                if s.total_bytes and s.state == "downloading"
                else ""
            )
            for s in job.segments
        )
        if line != last:
            print(line, file=stderr)
            last = line
        await asyncio.sleep(interval_s)
    for s in job.segments:
        print(f"{s.name}: {s.state}" + (f" ({s.error})" if s.error else ""), file=stdout)
    if job.state != "done":
        print(f"error: {job.error}", file=stderr)
        return EXIT_FAILURE
    print("done; restart BRouter if it still reports the data as missing", file=stdout)
    return EXIT_OK
