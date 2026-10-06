"""Readiness reporting (issue #25).

``/healthz`` says the process is alive. This module answers a different
question -- *can this instance serve plans right now?* -- by probing the
routing engines, the geocoder and the storage the service depends on.

Design rules:

* **Probes are cheap, bounded and cached.** Each component is probed at most
  once per TTL no matter how many callers poll ``/readyz``, probes run
  concurrently, and each has a hard timeout. Upstream public services
  (Nominatim's usage policy, rate-limited ORS) are never hammered by a
  load balancer's health checks.
* **Nothing leaks.** Adapter ``health()`` payloads can carry URLs or
  exception text; only the status vocabulary and a fixed phrase per status
  reach the response.
* **"Ready" means "can plan", not "everything is perfect".** With several
  engines one working engine is enough (the rest show up as degraded);
  history is best effort and never gates readiness unless the exports
  themselves live in the database.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)

ComponentStatusName = Literal["ok", "degraded", "unavailable", "unknown"]
OverallStatus = Literal["ok", "degraded", "unavailable"]
ComponentKind = Literal["routing", "geocoder", "database", "artifact_store", "cache"]

_VALID_STATUSES = ("ok", "degraded", "unavailable", "unknown")
# "unknown" is what the public ORS reports (it has no health endpoint): no
# evidence of a problem, so it does not make the instance unready.
_READY_STATUSES = ("ok", "unknown")

Probe = Callable[[], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Component:
    name: str
    kind: ComponentKind
    probe: Probe
    # A required component must be ready for the instance to be ready;
    # routing components are judged as a group (any one engine suffices).
    required: bool = True
    ttl_s: float | None = None


@dataclass(frozen=True)
class ComponentReport:
    name: str
    kind: ComponentKind
    required: bool
    status: ComponentStatusName
    latency_ms: int | None
    detail: str | None
    checked_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "required": self.required,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "detail": self.detail,
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass(frozen=True)
class HealthReport:
    status: OverallStatus
    ready: bool
    checked_at: datetime
    components: list[ComponentReport]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ready": self.ready,
            "checked_at": self.checked_at.isoformat(),
            "components": [c.as_dict() for c in self.components],
        }


def _detail_for(status: ComponentStatusName, raw: dict[str, Any]) -> str | None:
    """One fixed phrase per status -- never text taken from the adapter."""
    if status == "ok":
        return None
    if status == "unavailable":
        return "unreachable"
    if status == "degraded":
        code = raw.get("status_code")
        is_code = isinstance(code, int) and not isinstance(code, bool)
        return f"HTTP {code}" if is_code else "degraded"
    return "no health signal from this deployment"


class HealthMonitor:
    def __init__(
        self,
        components: Sequence[Component],
        *,
        timeout_s: float = 5.0,
        ttl_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._components = list(components)
        self._timeout_s = timeout_s
        self._ttl_s = ttl_s
        self._clock = clock
        self._cache: dict[str, tuple[float, ComponentReport]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _probe(self, component: Component) -> ComponentReport:
        lock = self._locks.setdefault(component.name, asyncio.Lock())
        ttl = self._ttl_s if component.ttl_s is None else component.ttl_s
        async with lock:
            cached = self._cache.get(component.name)
            if cached is not None and cached[0] > self._clock():
                return cached[1]
            started = time.monotonic()
            status: ComponentStatusName
            raw: dict[str, Any] = {}
            try:
                raw = await asyncio.wait_for(component.probe(), timeout=self._timeout_s)
                reported = raw.get("status") if isinstance(raw, dict) else None
                status = reported if reported in _VALID_STATUSES else "unknown"
                detail = _detail_for(status, raw if isinstance(raw, dict) else {})
            except TimeoutError:
                status, detail = "unavailable", "probe timed out"
            except Exception:
                logger.warning("health probe for %s failed", component.name, exc_info=True)
                status, detail = "unavailable", "probe failed"
            report = ComponentReport(
                name=component.name,
                kind=component.kind,
                required=component.required,
                status=status,
                latency_ms=round((time.monotonic() - started) * 1000),
                detail=detail,
                checked_at=datetime.now(UTC),
            )
            self._cache[component.name] = (self._clock() + ttl, report)
            return report

    async def check(self) -> HealthReport:
        reports = list(await asyncio.gather(*(self._probe(c) for c in self._components)))
        return summarize(reports)


def summarize(reports: Sequence[ComponentReport]) -> HealthReport:
    """Overall verdict from component reports.

    Ready when at least one routing engine is ready and every other required
    component is ready. ``ok``: nothing is wrong anywhere; ``degraded``:
    ready, but something (an engine, an optional dependency) is not;
    ``unavailable``: cannot plan.
    """
    routing = [r for r in reports if r.kind == "routing"]
    others = [r for r in reports if r.kind != "routing" and r.required]
    routing_ready = not routing or any(r.status in _READY_STATUSES for r in routing)
    ready = routing_ready and all(r.status in _READY_STATUSES for r in others)
    if not ready:
        overall: OverallStatus = "unavailable"
    elif all(r.status in _READY_STATUSES for r in reports):
        overall = "ok"
    else:
        overall = "degraded"
    return HealthReport(
        status=overall,
        ready=ready,
        checked_at=datetime.now(UTC),
        components=list(reports),
    )


def _call_health(obj: Any, attribute: str) -> Probe:
    """Wrap ``obj.<attribute>()`` -- sync or async -- as a probe.

    Sync callables (filesystem and database pings) run in a worker thread so
    a slow one cannot stall the event loop; an object without the attribute
    simply has no health signal ("unknown"), which is not a failure.
    """
    method = getattr(obj, attribute, None)

    async def probe() -> dict[str, Any]:
        if method is None:
            return {"status": "unknown"}
        if inspect.iscoroutinefunction(method):
            result = await method()
        else:
            result = await asyncio.to_thread(method)
        return result if isinstance(result, dict) else {"status": "unknown"}

    return probe


def build_health_monitor(
    *,
    geocoder: Any,
    routing_providers: Sequence[Any],
    artifact_store: Any,
    history: Any | None,
    cache: Any | None = None,
    database_is_critical: bool,
    timeout_s: float,
    ttl_s: float,
    geocoder_ttl_s: float,
) -> HealthMonitor:
    """Components for the objects the API is actually running with."""
    # With several engines any one suffices, so none of them is individually
    # required; a lone engine is.
    components: list[Component] = [
        Component(
            p.name, "routing", _call_health(p, "health"), required=len(routing_providers) == 1
        )
        for p in routing_providers
    ]
    components.append(
        Component(
            geocoder.name,
            "geocoder",
            _call_health(geocoder, "health"),
            ttl_s=max(ttl_s, geocoder_ttl_s),
        )
    )
    components.append(
        Component("artifact_store", "artifact_store", _call_health(artifact_store, "ping"))
    )
    if history is not None:
        # History is recorded best effort, so a database outage only degrades
        # the instance -- unless the exports themselves are in the database.
        components.append(
            Component(
                "database",
                "database",
                _call_health(history, "ping"),
                required=database_is_critical,
            )
        )
    if cache is not None and hasattr(cache, "ping"):
        # The cache is an optimisation: a Redis outage degrades the instance
        # (every lookup goes upstream) but never makes it unready.
        components.append(Component("cache", "cache", _call_health(cache, "ping"), required=False))
    return HealthMonitor(components, timeout_s=timeout_s, ttl_s=ttl_s)
