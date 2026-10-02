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

from pathlib import Path
from typing import Protocol

MEDIA_TYPES: dict[str, str] = {
    ".geojson": "application/geo+json",
    ".gpx": "application/gpx+xml",
}


def media_type_for(name: str) -> str:
    """Media type for an artifact name, by extension."""
    return MEDIA_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


class ArtifactStore(Protocol):
    def put(self, name: str, content: str) -> None:
        """Store ``content`` under ``name``, replacing any previous value."""
        ...

    def get(self, name: str) -> bytes | None:
        """The stored bytes, or ``None`` when no artifact has that name."""
        ...


class LocalArtifactStore:
    """Artifacts as plain files under one directory (the pre-#7 behavior)."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory

    @property
    def directory(self) -> Path:
        return self._dir

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
