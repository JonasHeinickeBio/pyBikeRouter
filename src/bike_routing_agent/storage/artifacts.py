"""Artifact storage abstraction (issue #7).

``explain_and_export`` writes the GeoJSON/GPX exports through an
:class:`ArtifactStore`, and the API serves them back through the same
object, so where the bytes live is a deployment decision rather than a code
path: local disk (default, one instance) or the database (several API
instances sharing state, see ``storage/postgres.py``).

Artifact names are opaque, already-validated file names
(``<32-hex>.geojson`` / ``.gpx``); the API rejects anything else before it
reaches a store.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

MEDIA_TYPES: dict[str, str] = {
    ".geojson": "application/geo+json",
    ".gpx": "application/gpx+xml",
}


def media_type_for(name: str) -> str:
    """Media type for an artifact name, by extension."""
    return MEDIA_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


# The names explain_and_export generates (<32 hex>.geojson|.gpx); the same
# shape the API accepts. Anything else in a store is not ours to list or delete.
ARTIFACT_NAME = re.compile(r"^[0-9a-f]{32}\.(geojson|gpx)$")


@dataclass(frozen=True)
class ArtifactInfo:
    """What retention needs to know about a stored artifact."""

    name: str
    size: int
    created_at: datetime


class ArtifactStore(Protocol):
    def put(self, name: str, content: str) -> None:
        """Store ``content`` under ``name``, replacing any previous value."""
        ...

    def get(self, name: str) -> bytes | None:
        """The stored bytes, or ``None`` when no artifact has that name."""
        ...

    def list_artifacts(self) -> Iterator[ArtifactInfo]:
        """Every artifact this service wrote (names matching ``ARTIFACT_NAME``)."""
        ...

    def delete(self, name: str) -> bool:
        """Remove an artifact; ``False`` when there was none (never an error)."""
        ...


class LocalArtifactStore:
    """Artifacts as plain files under one directory (the pre-#7 behavior)."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory

    @property
    def directory(self) -> Path:
        return self._dir

    def ping(self) -> dict[str, str]:
        """Writability of the export directory (or the nearest existing parent,
        since the directory is created on first write)."""
        target = self._dir
        while not target.exists() and target != target.parent:
            target = target.parent
        ok = target.is_dir() and os.access(target, os.W_OK)
        return {"status": "ok" if ok else "unavailable"}

    def put(self, name: str, content: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / name).write_text(content)

    def get(self, name: str) -> bytes | None:
        path = self._dir / name
        # A name that escapes the directory is simply "not found" -- callers
        # validate names, this is defence in depth, not the first line.
        if path.parent != self._dir or not path.is_file():
            return None
        return path.read_bytes()

    def list_artifacts(self) -> Iterator[ArtifactInfo]:
        """Our exports in the directory; the file's mtime is its age."""
        if not self._dir.is_dir():
            return
        for path in self._dir.iterdir():
            if not ARTIFACT_NAME.match(path.name) or not path.is_file():
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:  # deleted between listing and stat
                continue
            yield ArtifactInfo(
                name=path.name,
                size=stat.st_size,
                created_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            )

    def delete(self, name: str) -> bool:
        path = self._dir / name
        if not ARTIFACT_NAME.match(name) or path.parent != self._dir:
            return False
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True
