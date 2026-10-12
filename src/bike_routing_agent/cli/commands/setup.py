"""``bike-router setup``: first-time setup for people who do not want to learn the commands.

It asks one question -- where do you ride? -- and downloads the map data for that area
(BRouter's 5 x 5 degree tiles, 100-250 MB each) into the tile folder. Nothing is downloaded
before the sizes were shown and the person said yes (or passed ``--yes``).

The easy-install kit (``easy-install/``) runs this inside the app's container; every question
here can also be answered with a flag, so it works in scripts too.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from bike_routing_agent.cli.render import megabytes, table
from bike_routing_agent.regions import REGIONS, REGIONS_BY_KEY, parse_tiles, tiles_around

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

WELCOME = """\
Welcome to pyBikeRouter!

To plan routes, the program needs the map data of the area you ride in. It is
free, comes from OpenStreetMap, and is downloaded once (about 100-250 MB for
every 5 x 5 degrees -- roughly 350 x 550 km).
"""


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    group = subparsers.add_parser(
        "setup", help="first-time setup: pick your area and download its map data"
    )
    sub = group.add_subparsers(dest="command", metavar="<command>")
    if sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")

    start = sub.add_parser("start", help="ask where you ride and download the map data")
    start.add_argument(
        "--region", choices=[r.key for r in REGIONS], default=None, help="a ready-made area"
    )
    start.add_argument(
        "--around", default=None, metavar="TOWN", help="the area around a town or city"
    )
    start.add_argument(
        "--radius-km", type=float, default=80.0, help="with --around: how far out (default 80)"
    )
    start.add_argument(
        "--tiles", default=None, metavar="E10_N50,E5_N50", help="tile names, if you know them"
    )
    start.add_argument("--skip", action="store_true", help="download nothing now")
    start.add_argument("--yes", action="store_true", help="do not ask before downloading")
    start.add_argument("--dir", type=Path, default=None, help="BRouter's tile folder")

    sub.add_parser("regions", help="list the ready-made areas")
    return group


# ----------------------------------------------------------------------------- prompts


def _input(prompt: str, stdout: IO[str]) -> str:
    stdout.write(prompt)
    stdout.flush()
    line = sys.stdin.readline()
    if line == "":
        raise EOFError
    return line.strip()


def _menu(stdout: IO[str], ask: Callable[[str], str]) -> tuple[str, str]:
    """Show the choices; return (kind, value). Kinds: region, around, tiles, skip."""
    print("Where do you ride?\n", file=stdout)
    for i, region in enumerate(REGIONS, start=1):
        extra = f" -- {region.note}" if region.note else ""
        print(
            f"  {i:>2}) {region.label} ({len(region.tiles)} map tile"
            f"{'s' if len(region.tiles) != 1 else ''}){extra}",
            file=stdout,
        )
    around, names, skip = len(REGIONS) + 1, len(REGIONS) + 2, len(REGIONS) + 3
    print(f"  {around:>2}) Somewhere else: the area around a town I name", file=stdout)
    print(f"  {names:>2}) I know the tile names", file=stdout)
    print(f"  {skip:>2}) Skip -- I will choose in the web page later\n", file=stdout)
    while True:
        answer = ask("Type a number and press Enter: ")
        if answer.isdigit() and 1 <= int(answer) <= skip:
            n = int(answer)
            if n <= len(REGIONS):
                return "region", REGIONS[n - 1].key
            return {around: "around", names: "tiles", skip: "skip"}[n], ""
        print("That is not one of the numbers above.", file=stdout)


def _yes(answer: str, default: bool = False) -> bool:
    a = answer.strip().lower()
    return default if not a else a in ("y", "yes", "j", "ja")


# --------------------------------------------------------------------------- the work


def _geocode(town: str, factory: Callable[[], Any]) -> tuple[float, float, str]:
    provider = factory()
    found = asyncio.run(provider.geocode(town, limit=1))
    if not found:
        raise ValueError(f"I could not find a place called '{town}'")
    best = found[0]
    return best.coordinate.lon, best.coordinate.lat, best.label


def run(
    args: argparse.Namespace,
    stdout: IO[str],
    stderr: IO[str],
    *,
    ask: Callable[[str], str] | None = None,
    geocoder_factory: Callable[[], Any] | None = None,
    downloader_factory: Callable[[Path, Any], Any] | None = None,
    poll_interval_s: float = 1.0,
) -> int:
    if args.command == "regions":
        rows = [[r.key, r.label, ", ".join(r.tiles), r.note] for r in REGIONS]
        print(table(rows, ["KEY", "AREA", "TILES", "NOTE"]), file=stdout)
        return EXIT_OK
    if args.command != "start":
        print("unknown setup command", file=stderr)
        return EXIT_USAGE

    from bike_routing_agent.cli.commands.brouter import _download
    from bike_routing_agent.config import Settings
    from bike_routing_agent.providers.brouter_downloads import (
        MAX_SEGMENTS_PER_JOB,
        SegmentDownloader,
    )

    cfg = Settings()
    directory = args.dir or (Path(cfg.brouter_segments_dir) if cfg.brouter_segments_dir else None)
    interactive = ask is not None or sys.stdin.isatty()
    prompt = ask or (lambda q: _input(q, stdout))

    chosen = [bool(args.region), bool(args.around), bool(args.tiles), bool(args.skip)]
    if sum(chosen) > 1:
        print("error: use only one of --region, --around, --tiles and --skip", file=stderr)
        return EXIT_USAGE

    try:
        if args.skip:
            print("Nothing downloaded. You can choose map data in the web page later.", file=stdout)
            return EXIT_OK
        kind, value = ("", "")
        if args.region:
            kind, value = "region", args.region
        elif args.around:
            kind, value = "around", args.around
        elif args.tiles:
            kind, value = "tiles", args.tiles
        else:
            if not interactive:
                print(
                    "error: nothing to ask here (no terminal). Say where you ride with --region, "
                    "--around or --tiles, or --skip. See 'bike-router setup regions'.",
                    file=stderr,
                )
                return EXIT_USAGE
            print(WELCOME, file=stdout)
            kind, value = _menu(stdout, prompt)
            if kind == "skip":
                print(
                    "Nothing downloaded. You can choose map data in the web page later.",
                    file=stdout,
                )
                return EXIT_OK

        names = _resolve(
            kind, value, args, stdout, prompt if interactive else None, geocoder_factory
        )
    except ValueError as exc:
        print(f"error: {exc}", file=stderr)
        return EXIT_USAGE
    except (EOFError, KeyboardInterrupt):
        print("\nStopped. Nothing was downloaded.", file=stderr)
        return EXIT_FAILURE

    if directory is None or not directory.is_dir():
        print(
            "error: I do not know where to put the map data. Pass --dir, or set "
            "BROUTER_SEGMENTS_DIR to an existing folder.",
            file=stderr,
        )
        return EXIT_FAILURE

    make = downloader_factory or (
        lambda d, c: SegmentDownloader(
            d, base_url=c.brouter_segments_url, max_bytes=c.brouter_segments_max_mb * 2**20
        )
    )
    downloader = make(directory, cfg)
    try:
        infos = []
        for first in range(0, len(names), MAX_SEGMENTS_PER_JOB):  # one job's worth at a time
            infos += asyncio.run(downloader.inspect(names[first : first + MAX_SEGMENTS_PER_JOB]))
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=stderr)
        return EXIT_FAILURE
    rows = [
        [i.name, "already here" if i.present else "to download", megabytes(i.size_bytes)]
        for i in infos
    ]
    print("\nMap data for your area:\n", file=stdout)
    print(table(rows, ["TILE", "STATE", "SIZE"]), file=stdout)
    todo = [i for i in infos if not i.present]
    if not todo:
        print("\nEverything is already here. Nothing to download.", file=stdout)
        return EXIT_OK
    total = sum(i.size_bytes or 0 for i in todo)
    print(
        f"\nTo download: {megabytes(total) if total else 'size unknown'}. "
        f"Free disk space: {downloader.free_bytes() / 1e9:.0f} GB.",
        file=stdout,
    )
    if not args.yes:
        if not interactive:
            print("error: not asking without a terminal; pass --yes to download", file=stderr)
            return EXIT_FAILURE
        try:
            if not _yes(prompt("Download it now? [Y/n] "), default=True):
                print("Not downloaded.", file=stdout)
                return EXIT_FAILURE
        except (EOFError, KeyboardInterrupt):
            print("\nStopped. Nothing was downloaded.", file=stderr)
            return EXIT_FAILURE

    wanted = [i.name for i in todo]
    for start in range(0, len(wanted), MAX_SEGMENTS_PER_JOB):
        code = asyncio.run(
            _download(
                downloader,
                wanted[start : start + MAX_SEGMENTS_PER_JOB],
                stdout,
                stderr,
                poll_interval_s,
            )
        )
        if code != EXIT_OK:
            return code
    print("\nAll set. Open the web page and plan your first route.", file=stdout)
    return EXIT_OK


def _resolve(
    kind: str,
    value: str,
    args: argparse.Namespace,
    stdout: IO[str],
    prompt: Callable[[str], str] | None,
    geocoder_factory: Callable[[], Any] | None,
) -> list[str]:
    if kind == "region":
        return list(REGIONS_BY_KEY[value].tiles)
    if kind == "tiles":
        text = value or (
            prompt("Tile names, separated by commas (e.g. E10_N50): ") if prompt else ""
        )
        return parse_tiles(text)
    # around a town
    town = value or (prompt("Which town or city do you ride around? ") if prompt else "")
    if not town.strip():
        raise ValueError("no town given")
    factory = geocoder_factory or _default_geocoder
    while True:
        lon, lat, label = _geocode(town, factory)
        print(f"Found: {label}", file=stdout)
        if prompt is None or _yes(prompt("Is that the right place? [Y/n] "), default=True):
            break
        town = prompt("Please type the town again, with the country if you can: ")
    names = tiles_around(lon, lat, args.radius_km)
    print(
        f"Everything within about {args.radius_km:g} km is covered by "
        f"{len(names)} map tile{'s' if len(names) != 1 else ''}: {', '.join(names)}.",
        file=stdout,
    )
    return names


def _default_geocoder() -> Any:
    from bike_routing_agent.api import build_providers
    from bike_routing_agent.config import settings

    return build_providers(settings)[0]
