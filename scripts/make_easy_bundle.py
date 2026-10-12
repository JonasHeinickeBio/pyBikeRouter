#!/usr/bin/env python3
"""Builds the easy-install download: ``pybikerouter-easy-<version>.zip``.

The zip holds only what a person needs (docs/easy-install.md describes it): the compose file, the
start/stop/update scripts for Windows, macOS and Linux, a plain-language START-HERE.txt, the
BRouter profiles, and an empty ``data`` folder. No Python, no source code: the program itself
comes from the container image the scripts pull.

    python3 scripts/make_easy_bundle.py [OUTPUT_DIR]      # default: dist/
"""

from __future__ import annotations

import re
import stat
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "easy-install"
TOP = "pyBikeRouter"
EXECUTABLE = {".sh", ".command"}


def version() -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    if not match:
        raise SystemExit("could not read the version from pyproject.toml")
    return match.group(1)


def files() -> list[tuple[Path, str]]:
    """(source, name inside the zip) for everything the kit ships, in a stable order."""
    out: list[tuple[Path, str]] = []
    for path in sorted(KIT.rglob("*")):
        if path.is_file() and "data" not in path.relative_to(KIT).parts[:1]:
            if path.name in {".DS_Store"} or path.suffix == ".pyc":
                continue
            out.append((path, f"{TOP}/{path.relative_to(KIT).as_posix()}"))
    for extra in ("LICENSE", "LICENSE.md", "LICENSE.txt"):
        if (ROOT / extra).is_file():
            out.append((ROOT / extra, f"{TOP}/{extra}"))
            break
    out.append((ROOT / "docs" / "easy-install.md", f"{TOP}/HELP.md"))
    return out


def build(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"pybikerouter-easy-{version()}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for source, name in files():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            executable = source.suffix in EXECUTABLE
            info.external_attr = (0o755 if executable else 0o644) << 16 | (stat.S_IFREG << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            data = source.read_bytes()
            if source.suffix == ".bat":  # Windows batch files need CRLF
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            bundle.writestr(info, data)
        # An empty data folder, so people see where their files will be.
        note = zipfile.ZipInfo(f"{TOP}/data/README.txt", date_time=(2026, 1, 1, 0, 0, 0))
        note.external_attr = (0o644 << 16) | (stat.S_IFREG << 16)
        bundle.writestr(
            note,
            "Your map data (segments) and your routes (exports, GPX and GeoJSON) are kept here.\n"
            "You can copy the GPX files to your phone or bike computer.\n",
        )
    return target


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"
    print(build(out))
