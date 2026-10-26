"""An opt-in, local log of which route a person takes when several were compared.

Off unless ``PICK_LOG_PATH`` is set. A *pick* is a route the person downloads from the web page's
alternatives. What is kept is numbers only: for every distinct route that was on screen the value of
each optimiser metric (traffic, paved share, climbing, distance, ...), which one the optimiser
recommended, which one was taken, the goals and weights the plan was asked for, the bike type and
the time. No place name, coordinate, geometry, address, session or text is stored, and nothing is
sent anywhere; the file is capped (the old half is kept once as ``<path>.1``) and
``bike-router route picks --clear`` deletes it.

It exists to see whether the optimiser's choice is the one people take, and which way they lean
when it is not (``summarise``), before anyone changes a default weight. It never changes a weight
itself: the numbers are reported, a person decides (docs/optimizer.md#what-people-pick).
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from bike_routing_agent.models import RouteMetrics
from bike_routing_agent.optimizer.engine import METRICS, goodness

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 16
DEFAULT_MAX_BYTES = 2_000_000
# Below this many picks a lean is noise: the summary says so instead of reporting it as a finding.
MIN_PICKS = 20


class _Shim:
    """What the optimiser's metric readers need from a route: its ``metrics``."""

    def __init__(self, metrics: RouteMetrics) -> None:
        self.metrics = metrics


class PickGoal(BaseModel):
    metric: str
    weight: float = Field(gt=0, le=10)

    @field_validator("metric")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in METRICS:
            raise ValueError(f"unknown goal metric {value!r}")
        return value


class PickCandidate(BaseModel):
    """A route as the web page knows it, cut to the numbers (the page strips the geometry)."""

    metrics: RouteMetrics


class PickRequest(BaseModel):
    """``POST /v1/route/pick``: the distinct routes that were compared and the one taken."""

    candidates: list[PickCandidate] = Field(min_length=2, max_length=MAX_CANDIDATES)
    picked: int = Field(ge=0)  # index into candidates: the route that was downloaded
    selected: int = Field(ge=0)  # index into candidates: the route the optimiser recommended
    bike_type: str | None = Field(default=None, max_length=40)
    goals: list[PickGoal] = Field(default_factory=list, max_length=len(METRICS))

    @model_validator(mode="after")
    def _indices_inside(self) -> PickRequest:
        n = len(self.candidates)
        if self.picked >= n or self.selected >= n:
            raise ValueError("picked and selected must index into candidates")
        return self


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return round(float(value), 4)


def values_of(metrics: RouteMetrics) -> dict[str, float | None]:
    """Every optimiser metric of one route; ``None`` where the engine gave no data."""
    shim = _Shim(metrics)
    return {name: _number(metric.value(shim)) for name, metric in METRICS.items()}  # type: ignore[arg-type]


def build_record(request: PickRequest, *, now: float | None = None) -> dict[str, Any]:
    """The line to keep for ``request``: numbers only (see the module docstring)."""
    return {
        "at": round(now if now is not None else time.time(), 1),
        "bike_type": request.bike_type,
        "goals": [{"metric": g.metric, "weight": g.weight} for g in request.goals],
        "picked": request.picked,
        "selected": request.selected,
        "values": [values_of(c.metrics) for c in request.candidates],
    }


class PickLog:
    def __init__(self, path: Path | str, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.path = Path(path)
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._warned = False

    def record(self, record: dict[str, Any]) -> bool:
        """Append one pick; ``False`` (and one warning in the log) when the file cannot be written.
        A failing log never fails a request."""
        line = json.dumps(record, ensure_ascii=False)
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
                    logger.warning("pick log %s cannot be written: %s", self.path, exc)
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


def _valid(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    values = item.get("values")
    picked, selected = item.get("picked"), item.get("selected")
    return (
        isinstance(values, list)
        and len(values) >= 2
        and all(isinstance(v, dict) for v in values)
        and isinstance(picked, int)
        and isinstance(selected, int)
        and not isinstance(picked, bool)
        and not isinstance(selected, bool)
        and 0 <= picked < len(values)
        and 0 <= selected < len(values)
    )


def load(path: Path | str) -> list[dict[str, Any]]:
    """The recorded picks, oldest first (the rotated copy first); a line that is not a pick is
    skipped, so a hand-edited or half-written file does not stop the report."""
    path = Path(path)
    records: list[dict[str, Any]] = []
    for source in (path.with_name(path.name + ".1"), path):
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if _valid(item):
                records.append(item)
    return records


def _gain(record: dict[str, Any], name: str) -> float | None:
    """How much better the taken route is than the recommended one on one metric, on the
    optimiser's own 0..1 scale (positive: the taken route is better; ``None``: no data)."""
    metric = METRICS[name]
    values: dict[int, float | None] = {}
    for i, row in enumerate(record["values"]):
        value = _number(row.get(name))
        values[i] = value
    picked, selected = record["picked"], record["selected"]
    if values[picked] is None or values[selected] is None:
        return None
    good = goodness(values, metric)
    return good[picked] - good[selected]


def summarise(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """How often the recommended route was taken and, when another one was, what its rider got.

    ``lean`` lists the metrics by how much better the taken route was than the recommended one
    (mean difference of the optimiser's 0..1 goodness, over the picks that took another route and
    where both routes have data); a positive number means people who left the recommendation went
    for more of that. ``enough`` says whether there are picks to read a lean from at all.
    """
    items = list(records)
    others = [r for r in items if r["picked"] != r["selected"]]
    lean: list[dict[str, Any]] = []
    for name, metric in METRICS.items():
        gains = [g for r in others if (g := _gain(r, name)) is not None]
        if gains:
            lean.append(
                {
                    "metric": name,
                    "label": metric.label,
                    "mean_gain": round(sum(gains) / len(gains), 3),
                    "picks": len(gains),
                }
            )
    lean.sort(key=lambda row: (-row["mean_gain"], row["metric"]))
    by_bike = Counter(str(r.get("bike_type") or "unknown") for r in items)
    return {
        "total": len(items),
        "kept_recommendation": len(items) - len(others),
        "took_another": len(others),
        "enough": len(items) >= MIN_PICKS and len(others) >= MIN_PICKS // 4,
        "min_picks": MIN_PICKS,
        "by_bike_type": dict(by_bike.most_common()),
        "lean": lean,
    }
