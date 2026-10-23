"""Where the time of a plan goes: one timed, logged step per graph node.

Every node of the planning graph is wrapped (``traced``) so a plan knows, for each step, how long
it took, what status it left the plan in and whether it raised. The steps ride in the graph state
(``RouteAgentState.trace``, a list that each step appends to) and come back in the response when
the caller asks (``?trace=true``), always in a ``Server-Timing`` header (the browser's network panel
shows it) and in the service log. This is what any speed-up needs to be measured against: it says
which node is slow *before* anyone guesses, and whether two nodes that look independent are worth
running side by side.

A step is a plain dict (state never holds anything else): ``{"node": "route_with_provider",
"ms": 1234.5, "status": "in_progress", "ok": true}``. A node that raises is logged with its time and
the exception is re-raised unchanged; there is no state to carry a step for it, so a failed plan is
explained by the log and the error.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

# Shown to a person while the plan runs (the stream endpoint and the page): what each step is doing.
STEP_LABELS: dict[str, str] = {
    "parse_request": "Reading the request",
    "validate_request": "Checking the request",
    "geocode_locations": "Finding the places",
    "select_poi_stops": "Choosing sights",
    "route_with_provider": "Routing",
    "enrich_candidates": "Reading the road data",
    "score_candidates": "Scoring the routes",
    "optimize_candidates": "Comparing the options",
    "weather_candidates": "Fetching the forecast",
    "annotate_alternatives": "Comparing the alternatives",
    "explain_and_export": "Writing the files",
}

NodeFn = Callable[[Any], Any]


def step(node: str, ms: float, status: str | None, ok: bool = True) -> dict[str, Any]:
    return {"node": node, "ms": round(ms, 1), "status": status, "ok": ok}


def traced(name: str, fn: NodeFn) -> NodeFn:
    """``fn`` (a graph node, sync or async) that also reports its own step.

    The returned update is ``fn``'s own plus ``{"trace": [step]}``; the state's ``trace`` field has
    a list-concatenating reducer, so the steps add up in order. A node that returns nothing
    (``None``) or something that is not a mapping is passed through untouched: tracing never changes
    what a node says.
    """

    async def wrapper(state: Any) -> Any:
        started = time.perf_counter()
        try:
            result = fn(state)
            if inspect.isawaitable(result):
                result = await result
        except BaseException:
            ms = (time.perf_counter() - started) * 1000
            logger.warning("plan node=%s ms=%.0f failed", name, ms)
            raise
        ms = (time.perf_counter() - started) * 1000
        status = result.get("status") if isinstance(result, dict) else None
        logger.info("plan node=%s ms=%.0f status=%s", name, ms, status)
        if not isinstance(result, dict):
            return result
        return {**result, "trace": [step(name, ms, status if isinstance(status, str) else None)]}

    wrapper.__name__ = getattr(fn, "__name__", name)
    wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
    return wrapper


def total_ms(trace: Sequence[dict[str, Any]]) -> float:
    return round(sum(float(s.get("ms") or 0.0) for s in trace), 1)


def server_timing(trace: Sequence[dict[str, Any]]) -> str:
    """The ``Server-Timing`` header value: one metric per step plus the total (milliseconds)."""
    parts = [f"{s['node']};dur={float(s.get('ms') or 0.0):.1f}" for s in trace if s.get("node")]
    if parts:
        parts.append(f"total;dur={total_ms(trace):.1f}")
    return ", ".join(parts)
