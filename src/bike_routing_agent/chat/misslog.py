"""An opt-in, local log of what the chat could not read.

Off unless ``CHAT_MISS_LOG_PATH`` is set. A *miss* is a message the chat's rules did not understand:
free text that started the question-and-answer dialogue without telling it anything (``unread``),
a change it could not make out (``refine_unknown``), or a free-text request the language model
could not read (``text_failed``). Each one is a line of JSON in a file on this machine -- the
message (cut at 500 characters), the kind, the intent it fell to, whether a route was on screen and
the time. Nothing is sent anywhere, and no session id, address or place lookup is stored; the file
is capped (the old half is kept once as ``<path>.1``) and ``bike-router chat misses --clear``
deletes it.

It exists to find out what people actually say, so that the chat benchmark
(``chat/data/chat-v1.json``) grows from real phrasings before any model is asked to read them.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

KINDS = ("unread", "refine_unknown", "text_failed")
MAX_TEXT_CHARS = 500
DEFAULT_MAX_BYTES = 2_000_000


def _writable(text: str) -> str:
    """The text as UTF-8 can hold it: a lone surrogate (a JSON client can send ``"\\ud800"``) cannot
    be written to a file and is replaced."""
    return text.encode("utf-8", "replace").decode("utf-8")


class MissLog:
    def __init__(self, path: Path | str, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.path = Path(path)
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._warned = False

    def record(
        self, text: str, kind: str, *, intent: str | None, had_plan: bool, now: float | None = None
    ) -> bool:
        """Append one miss; ``False`` (and one warning in the log) when the file cannot be written.
        A failing log never fails a chat turn."""
        line = json.dumps(
            {
                "at": round(now if now is not None else time.time(), 1),
                "kind": kind,
                "intent": intent,
                "had_plan": bool(had_plan),
                "text": _writable(text.strip()[:MAX_TEXT_CHARS]),
            },
            ensure_ascii=False,
        )
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size >= self._max_bytes:
                    os.replace(self.path, self.path.with_name(self.path.name + ".1"))
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                return True
            except (OSError, ValueError) as exc:
                if not self._warned:
                    logger.warning("chat miss log %s cannot be written: %s", self.path, exc)
                    self._warned = True
                return False

    def clear(self) -> int:
        """Delete the log (and its one rotated copy); how many files were removed."""
        removed = 0
        for path in (self.path, self.path.with_name(self.path.name + ".1")):
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
        return removed


def load(path: Path | str) -> list[dict[str, Any]]:
    """The recorded misses, oldest first (the rotated copy first); a line that is not a miss is
    skipped, so a hand-edited or half-written file does not stop the report."""
    path = Path(path)
    records: list[dict[str, Any]] = []
    for source in (path.with_name(path.name + ".1"), path):
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, OSError):
            continue
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and isinstance(item.get("text"), str) and item.get("kind"):
                records.append(item)
    return records


def normalise(text: str) -> str:
    """The same phrase written slightly differently counts once: lower case, one space, no end
    punctuation."""
    return re.sub(r"\s+", " ", text.strip().lower()).strip(" .!?,;")


def summarise(records: Iterable[dict[str, Any]], *, top: int = 15) -> dict[str, Any]:
    """How many misses of each kind, and the phrases that come back most often."""
    items = list(records)
    by_kind = Counter(str(r["kind"]) for r in items)
    phrases = Counter(normalise(str(r["text"])) for r in items)
    return {
        "total": len(items),
        "by_kind": dict(by_kind.most_common()),
        "top": [{"text": text, "count": n} for text, n in phrases.most_common(top) if text],
    }


def candidate_cases(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The distinct misses as unlabelled benchmark cases: ``expected`` is left empty for a person
    to fill in (what *should* the chat have done?), so a case only joins the benchmark once it has
    been judged."""
    seen: set[str] = set()
    cases: list[dict[str, Any]] = []
    for record in records:
        key = (normalise(str(record["text"])), bool(record.get("had_plan")))
        if key in seen or not key[0]:
            continue
        seen.add(key)  # type: ignore[arg-type]
        cases.append(
            {
                "id": f"miss-{len(cases) + 1:03d}",
                "text": str(record["text"]).strip(),
                "state": "route" if record.get("had_plan") else "none",
                "expected": None,
                "seen_as": record.get("intent"),
            }
        )
    return cases
