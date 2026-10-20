"""The chat's checkpointers: in memory, and in a SQLite file.

``LatestOnlyMemorySaver`` forgets everything when the process ends, so every redeploy of the
service wiped the open conversations. ``SqliteChatSaver`` is the same saver (same pruning to the
latest checkpoint) that also writes each conversation to a file after a turn and reads them all
back at start. Only the one conversation that changed is written, and since a thread keeps just
its latest checkpoint a row stays small.

The rows are JSON (bytes as base64), never pickles: the file is read back as data, so a damaged or
tampered file cannot run code. A row that cannot be read (damaged, or written by another version of
LangGraph's checkpoint layout) is dropped, not guessed at: that conversation starts over.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)

FORMAT = 1
DAY_S = 86400.0


def _layout() -> str:
    """The checkpoint layout a row was written with (a row from another one is dropped)."""
    try:
        version = metadata.version("langgraph-checkpoint")
    except metadata.PackageNotFoundError:  # pragma: no cover - installed together with langgraph
        version = "unknown"
    return f"{FORMAT}:{'.'.join(version.split('.')[:2])}"


def _encode(value: Any) -> Any:
    """Bytes and tuples (which JSON lacks) as tagged values; everything else as it is."""
    if isinstance(value, bytes):
        return {"$b": base64.b64encode(value).decode("ascii")}
    if isinstance(value, tuple):
        return {"$t": [_encode(item) for item in value]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {"$d": [[_encode(k), _encode(v)] for k, v in value.items()]}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if "$b" in value:
            return base64.b64decode(value["$b"])
        if "$t" in value:
            return tuple(_decode(item) for item in value["$t"])
        if "$d" in value:
            return {_decode(k): _decode(v) for k, v in value["$d"]}
        raise ValueError("unknown tagged value")
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


class LatestOnlyMemorySaver(MemorySaver):
    """``MemorySaver`` that can drop a thread's history and keep only its latest checkpoint.

    LangGraph writes a checkpoint per step, each holding the plan (route geometries): without
    pruning, one long conversation grows the process without limit. Resuming a paused question
    needs only the latest checkpoint and its pending writes, so those are kept.
    """

    def prune(self, thread_ids: Sequence[str], *, strategy: str = "keep_latest") -> None:
        if strategy != "keep_latest":
            raise NotImplementedError(strategy)
        for thread_id in thread_ids:
            self._keep_latest(thread_id)

    def _keep_latest(self, thread_id: str) -> None:
        for namespace, checkpoints in self.storage.get(thread_id, {}).items():
            if len(checkpoints) <= 1:
                continue
            latest = max(checkpoints)  # checkpoint ids sort in creation order
            versions = self.serde.loads_typed(checkpoints[latest][0]).get("channel_versions") or {}
            for checkpoint_id in [c for c in checkpoints if c != latest]:
                del checkpoints[checkpoint_id]
                self.writes.pop((thread_id, namespace, checkpoint_id), None)
            kept = {(thread_id, namespace, channel, v) for channel, v in versions.items()}
            for key in [k for k in self.blobs if k[:2] == (thread_id, namespace) and k not in kept]:
                del self.blobs[key]


class SqliteChatSaver(LatestOnlyMemorySaver):
    """``LatestOnlyMemorySaver`` whose conversations survive a restart (see the module docs)."""

    def __init__(self, path: str | Path, *, max_age_days: float = 30.0) -> None:
        super().__init__()
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._max_age_s = max_age_days * DAY_S
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self._path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS chat_threads ("
            "thread_id TEXT PRIMARY KEY, layout TEXT NOT NULL, updated REAL NOT NULL, "
            "data TEXT NOT NULL)"
        )
        self._db.commit()
        self._order: list[str] = self._load()

    @property
    def path(self) -> Path:
        return self._path

    def thread_ids(self) -> list[str]:
        """The conversations that were read back at start, least recently used first."""
        return list(self._order)

    def updated_at(self, thread_id: str) -> float | None:
        """When the conversation was last written, or ``None`` when it never was."""
        with self._lock:
            row = self._db.execute(
                "SELECT updated FROM chat_threads WHERE thread_id = ?", (thread_id,)
            ).fetchone()
        return float(row[0]) if row else None

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------------ reading

    def _load(self) -> list[str]:
        layout = _layout()
        cutoff = time.time() - self._max_age_s
        loaded: list[str] = []
        drop: list[str] = []
        with self._lock:
            rows = self._db.execute(
                "SELECT thread_id, layout, updated, data FROM chat_threads ORDER BY updated"
            ).fetchall()
        for thread_id, row_layout, updated, data in rows:
            if updated < cutoff:
                drop.append(thread_id)
                continue
            try:
                if row_layout != layout:
                    raise ValueError(f"written with layout {row_layout}, this is {layout}")
                self._restore(thread_id, json.loads(data))
            except Exception as exc:  # a conversation that cannot be read starts over
                logger.warning("dropping stored chat %s: %s", thread_id, exc)
                self._forget(thread_id)
                drop.append(thread_id)
                continue
            loaded.append(thread_id)
        if drop:
            with self._lock:
                self._db.executemany(
                    "DELETE FROM chat_threads WHERE thread_id = ?", [(t,) for t in drop]
                )
                self._db.commit()
        return loaded

    def _restore(self, thread_id: str, row: dict[str, Any]) -> None:
        for namespace, checkpoints in _decode(row["storage"]).items():
            self.storage[thread_id][namespace].update(checkpoints)
        for namespace, checkpoint_id, entries in _decode(row["writes"]):
            for key, value in entries:
                self.writes[(thread_id, namespace, checkpoint_id)][key] = value
        for namespace, channel, version, value in _decode(row["blobs"]):
            self.blobs[(thread_id, namespace, channel, version)] = value

    def _forget(self, thread_id: str) -> None:
        """Remove a thread from memory only (the row is handled by the caller)."""
        super().delete_thread(thread_id)

    # ------------------------------------------------------------------ writing

    def persist(self, thread_id: str) -> None:
        """Write one conversation to the file (call after a turn, once it is pruned)."""
        row = {
            "storage": _encode(
                {ns: dict(cps) for ns, cps in self.storage.get(thread_id, {}).items()}
            ),
            "writes": _encode(
                [
                    [key[1], key[2], list(entries.items())]
                    for key, entries in self.writes.items()
                    if key[0] == thread_id
                ]
            ),
            "blobs": _encode(
                [
                    [key[1], key[2], key[3], value]
                    for key, value in self.blobs.items()
                    if key[0] == thread_id
                ]
            ),
        }
        with self._lock:
            self._db.execute(
                "INSERT INTO chat_threads (thread_id, layout, updated, data) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET layout = excluded.layout, "
                "updated = excluded.updated, data = excluded.data",
                (thread_id, _layout(), time.time(), json.dumps(row)),
            )
            self._db.commit()

    def delete_thread(self, thread_id: str) -> None:
        super().delete_thread(thread_id)
        with self._lock:
            self._db.execute("DELETE FROM chat_threads WHERE thread_id = ?", (thread_id,))
            self._db.commit()
