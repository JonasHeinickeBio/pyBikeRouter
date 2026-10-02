"""Route history: what was planned, by which engine, and how it scored (issue #7).

Every plan the API answers -- successful or not -- can be recorded as a
:class:`PlanRecord`: the request as received, the resolved endpoints, the
final status and errors, and (for ``ready`` plans) every scored candidate
with its provenance. :class:`RouteHistory` is the seam; the in-memory
implementation here serves tests and single-process dev, the PostGIS one
(``storage/postgres.py``) is the production backend.

Raw provider payloads are never recorded -- the API contract already
guarantees they never leave the server, and history is built from the same
neutralised candidates the API returns.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field

from bike_routing_agent.models import Coordinate, PlanStatus, RouteCandidate

# bbox: (min_lon, min_lat, max_lon, max_lat)
BBox = tuple[float, float, float, float]

MAX_QUERY_LIMIT = 200


class StoredCandidate(BaseModel):
    candidate: RouteCandidate
    selected: bool
    # 1-based position by score, best first (null scores last).
    rank: int = Field(ge=1)


class CandidateSummary(BaseModel):
    """A candidate without its geometry -- enough to compare and filter."""

    provider: str
    provider_profile: str
    score: float | None
    distance_m: float
    duration_s: float | None
    ascent_m: float | None
    selected: bool
    rank: int


class PlanSummary(BaseModel):
    plan_id: str
    created_at: datetime
    status: PlanStatus
    bike_type: str | None
    origin: Coordinate | None
    destination: Coordinate | None
    candidates: list[CandidateSummary] = Field(default_factory=list)


class PlanRecord(BaseModel):
    plan_id: str
    created_at: datetime
    status: PlanStatus
    request: dict[str, Any]
    constraints: dict[str, Any]
    origin: Coordinate | None = None
    destination: Coordinate | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    explanation: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    candidates: list[StoredCandidate] = Field(default_factory=list)

    @property
    def bike_type(self) -> str | None:
        value = self.constraints.get("bike_type")
        return str(value) if value is not None else None

    def summary(self) -> PlanSummary:
        return PlanSummary(
            plan_id=self.plan_id,
            created_at=self.created_at,
            status=self.status,
            bike_type=self.bike_type,
            origin=self.origin,
            destination=self.destination,
            candidates=[
                CandidateSummary(
                    provider=s.candidate.provider,
                    provider_profile=s.candidate.provider_profile,
                    score=s.candidate.score,
                    distance_m=s.candidate.metrics.distance_m,
                    duration_s=s.candidate.metrics.duration_s,
                    ascent_m=s.candidate.metrics.ascent_m,
                    selected=s.selected,
                    rank=s.rank,
                )
                for s in self.candidates
            ],
        )


class PlanFilter(BaseModel):
    """Provenance query: which plans did which engine/profile produce, when.

    ``provider``/``profile`` match *any* recorded candidate unless
    ``selected_only`` is set, in which case only the candidate that was
    actually returned to the caller counts. ``bbox`` matches candidates whose
    geometry bounding box intersects it (PostGIS ``&&`` semantics).
    """

    provider: str | None = None
    profile: str | None = None
    status: PlanStatus | None = None
    bike_type: str | None = None
    selected_only: bool = False
    since: datetime | None = None
    until: datetime | None = None
    bbox: BBox | None = None
    limit: int = Field(default=50, ge=1, le=MAX_QUERY_LIMIT)
    offset: int = Field(default=0, ge=0)


class RouteHistory(Protocol):
    def save(self, record: PlanRecord) -> None:
        """Persist a plan; saving an existing ``plan_id`` replaces it."""
        ...

    def get(self, plan_id: str) -> PlanRecord | None: ...

    def query(self, plan_filter: PlanFilter) -> list[PlanSummary]:
        """Matching plans, newest first."""
        ...


def _geometry_bbox(geometry: dict[str, Any]) -> BBox | None:
    """Bounding box of a (Multi)LineString/Point GeoJSON geometry, ignoring Z."""
    points: list[Any] = []

    def walk(node: Any) -> None:
        if node and isinstance(node[0], (int, float)):
            points.append(node)
        else:
            for child in node:
                walk(child)

    walk(geometry.get("coordinates") or [])
    if not points:
        return None
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return (min(lons), min(lats), max(lons), max(lats))


def _bboxes_intersect(a: BBox, b: BBox) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


class InMemoryRouteHistory:
    """Process-local history with the same filter semantics as the SQL one."""

    def __init__(self) -> None:
        self._records: dict[str, PlanRecord] = {}

    def save(self, record: PlanRecord) -> None:
        self._records[record.plan_id] = record

    def get(self, plan_id: str) -> PlanRecord | None:
        return self._records.get(plan_id)

    def query(self, plan_filter: PlanFilter) -> list[PlanSummary]:
        matches = [r for r in self._records.values() if self._matches(r, plan_filter)]
        matches.sort(key=lambda r: r.created_at, reverse=True)
        page = matches[plan_filter.offset : plan_filter.offset + plan_filter.limit]
        return [r.summary() for r in page]

    @staticmethod
    def _matches(record: PlanRecord, f: PlanFilter) -> bool:
        if f.status is not None and record.status != f.status:
            return False
        if f.bike_type is not None and record.bike_type != f.bike_type:
            return False
        if f.since is not None and record.created_at < f.since:
            return False
        if f.until is not None and record.created_at >= f.until:
            return False
        if f.provider is None and f.profile is None and f.bbox is None and not f.selected_only:
            return True
        return any(
            (not f.selected_only or s.selected)
            and (f.provider is None or s.candidate.provider == f.provider)
            and (f.profile is None or s.candidate.provider_profile == f.profile)
            and (
                f.bbox is None
                or (
                    (box := _geometry_bbox(s.candidate.geometry_geojson)) is not None
                    and _bboxes_intersect(box, f.bbox)
                )
            )
            for s in record.candidates
        )


def _coordinate(raw: Any) -> Coordinate | None:
    return Coordinate.model_validate(raw) if raw else None


def record_from_state(
    plan_id: str,
    final_state: dict[str, Any],
    *,
    created_at: datetime | None = None,
) -> PlanRecord:
    """Build the history record for a finished graph run.

    Candidates are recorded only for ``ready`` plans (that is where scoring
    has produced a verdict). The selected candidate is the one the graph
    chose; ranking follows the same best-score-first order the API reports.
    """
    status = final_state.get("status", "provider_failure")
    stored: list[StoredCandidate] = []
    if status == "ready":
        selected = final_state.get("selected_candidate")
        candidates = [
            RouteCandidate.model_validate(c).model_copy(update={"raw_provider_response": None})
            for c in final_state.get("candidates", [])
        ]
        candidates.sort(
            key=lambda c: c.score if c.score is not None else float("-inf"), reverse=True
        )
        selected_index = _selected_index(candidates, selected)
        stored = [
            StoredCandidate(candidate=c, selected=i == selected_index, rank=i + 1)
            for i, c in enumerate(candidates)
        ]
    return PlanRecord(
        plan_id=plan_id,
        created_at=created_at or datetime.now(UTC),
        status=status,
        request=final_state.get("raw_input", {}),
        constraints=final_state.get("constraints", {}),
        origin=_coordinate(final_state.get("resolved_origin")),
        destination=_coordinate(final_state.get("resolved_destination")),
        errors=final_state.get("errors", []),
        explanation=final_state.get("explanation"),
        artifacts=final_state.get("artifacts", {}),
        candidates=stored,
    )


def _selected_index(candidates: list[RouteCandidate], selected: dict[str, Any] | None) -> int:
    """Index of the graph's pick among the (sorted) candidates; 0 as fallback."""
    if selected is None:
        return 0
    chosen = RouteCandidate.model_validate(selected)
    for i, c in enumerate(candidates):
        if (
            c.provider == chosen.provider
            and c.provider_profile == chosen.provider_profile
            and c.geometry_geojson == chosen.geometry_geojson
        ):
            return i
    return 0
