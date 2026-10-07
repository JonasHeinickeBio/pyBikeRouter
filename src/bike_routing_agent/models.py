"""Provider-neutral domain models, validated with Pydantic v2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from bike_routing_agent.config import resolve_surface_tokens
from bike_routing_agent.weather.models import RouteWeather

MAX_VIA_POINTS = 10
# Upper bound for the request's max_alternatives (issue #24).
MAX_ALTERNATIVES = 5
# How far ahead a departure time may be (Open-Meteo forecasts reach 16 days;
# the last days are too uncertain to present as a plan).
MAX_FORECAST_DAYS = 14
PlaceString = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]

# Sweep direction of a synthesized loop around its origin (issue #5); the
# geometry lives in loops.py, this is just the shared vocabulary.
LoopDirection = Literal["clockwise", "counterclockwise"]


class BikeType(StrEnum):
    ROAD = "road"
    GRAVEL = "gravel"
    TOURING = "touring"
    MOUNTAIN = "mountain"
    CITY = "city"
    EBIKE = "ebike"
    COMMUTER = "commuter"
    RECUMBENT = "recumbent"


class Coordinate(BaseModel):
    model_config = ConfigDict(frozen=True)

    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)


class RouteConstraints(BaseModel):
    bike_type: BikeType = BikeType.GRAVEL
    target_distance_km: float | None = Field(default=None, gt=0, le=1000)
    max_distance_km: float | None = Field(default=None, gt=0, le=1000)
    max_ascent_m: float | None = Field(default=None, ge=0, le=10000)
    prefer_surfaces: list[str] = Field(default_factory=list)
    avoid_surfaces: list[str] = Field(default_factory=list)
    avoid_high_traffic_roads: bool = True
    avoid_ferries: bool = True
    return_to_origin: bool = False
    loop_direction: LoopDirection = "clockwise"

    @model_validator(mode="after")
    def _check_distance_consistency(self) -> RouteConstraints:
        if (
            self.target_distance_km is not None
            and self.max_distance_km is not None
            and self.target_distance_km > self.max_distance_km
        ):
            raise ValueError(
                "target_distance_km cannot exceed max_distance_km "
                f"({self.target_distance_km} > {self.max_distance_km})"
            )
        overlap = set(self.prefer_surfaces) & set(self.avoid_surfaces)
        if overlap:
            raise ValueError(f"surfaces listed in both prefer and avoid: {sorted(overlap)}")
        # The same check at the granularity scoring works at: "asphalt" and
        # "concrete" are different tags but one category, so preferring one
        # and avoiding the other would contradict itself.
        preferred, _ = resolve_surface_tokens(self.prefer_surfaces)
        avoided, _ = resolve_surface_tokens(self.avoid_surfaces)
        if preferred & avoided:
            raise ValueError(
                "prefer_surfaces and avoid_surfaces resolve to the same surface "
                f"categories: {sorted(preferred & avoided)}"
            )
        if self.return_to_origin and self.target_distance_km is None:
            raise ValueError(
                "return_to_origin requires target_distance_km to size the loop "
                "(there is no silently invented loop length)"
            )
        return self


class RoutingRequest(BaseModel):
    origin: Coordinate
    destination: Coordinate
    via: list[Coordinate] = Field(default_factory=list, max_length=MAX_VIA_POINTS)
    constraints: RouteConstraints


class RouteMetrics(BaseModel):
    distance_m: float = Field(ge=0)
    duration_s: float | None = Field(default=None, ge=0)
    ascent_m: float | None = Field(default=None, ge=0)
    descent_m: float | None = Field(default=None, ge=0)
    surface_coverage: dict[str, float] = Field(default_factory=dict)
    unknown_surface_fraction: float | None = Field(default=None, ge=0, le=1)


class RouteCandidate(BaseModel):
    provider: str
    provider_profile: str
    geometry_geojson: dict[str, Any]
    metrics: RouteMetrics
    score: float | None = None
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    # Ranking (issue #24): 1-based position in the returned list (null on
    # candidates that have not been through the score node), a facts-only
    # sentence comparing it to rank 1, and the near-identical-geometry
    # bookkeeping -- `duplicates` on a kept route, `duplicate_of` on a
    # candidate that is itself a near-copy of a better-scored one.
    # Forecast for this route at the requested departure (issue: weather);
    # null when weather is disabled, unavailable, or does not cover the ride.
    weather: RouteWeather | None = None
    rank: int | None = Field(default=None, ge=1)
    rank_rationale: str | None = None
    duplicate_of: str | None = None
    duplicates: list[str] = Field(default_factory=list)
    raw_provider_response: dict[str, Any] | None = Field(default=None, repr=False)


class GeocodeCandidate(BaseModel):
    label: str
    coordinate: Coordinate
    confidence: float = Field(ge=0, le=1)
    source: str


class PlaceInput(BaseModel):
    """A place given either as free text or as an already-known coordinate."""

    query: str | None = None
    coordinate: Coordinate | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> PlaceInput:
        if (self.query is None) == (self.coordinate is None):
            raise ValueError("exactly one of query or coordinate must be set")
        if self.query is not None and not self.query.strip():
            raise ValueError("query must not be blank")
        return self


class RoutePlanAPIRequest(BaseModel):
    """Top-level request body for POST /v1/route/plan."""

    origin: PlaceString | Coordinate
    destination: PlaceString | Coordinate | None = None
    via: list[PlaceString | Coordinate] = Field(default_factory=list, max_length=MAX_VIA_POINTS)
    constraints: RouteConstraints = Field(default_factory=RouteConstraints)
    # Number of distinct alternatives wanted (issue #24). Omitted: every
    # scored candidate is returned, ranked and annotated, exactly as before.
    # Set: near-identical routes are dropped and the list is capped. It only
    # has an effect when more than one routing engine is configured.
    max_alternatives: int | None = Field(default=None, ge=1, le=MAX_ALTERNATIVES)
    # When the ride starts, for the weather forecast. Omitted: now. A time without
    # a UTC offset is read as UTC. Forecasts beyond MAX_FORECAST_DAYS ahead are
    # too uncertain to be useful and are rejected.
    departure_time: datetime | None = None

    @field_validator("departure_time")
    @classmethod
    def _check_departure_in_forecast_range(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if aware > datetime.now(UTC) + timedelta(days=MAX_FORECAST_DAYS):
            raise ValueError(
                f"departure_time is more than {MAX_FORECAST_DAYS} days ahead; "
                "weather forecasts do not reach that far"
            )
        return aware

    @model_validator(mode="after")
    def _check_loop_contract(self) -> RoutePlanAPIRequest:
        """A loop is a single-origin request (issue #5)."""
        if self.constraints.return_to_origin:
            if self.destination is not None:
                raise ValueError(
                    "destination must be omitted when return_to_origin is set "
                    "(a loop starts and ends at the origin)"
                )
        elif self.destination is None:
            raise ValueError("destination is required unless return_to_origin is set")
        return self


PlanStatus = Literal[
    "ready",
    "awaiting_clarification",
    "invalid",
    "provider_failure",
    "no_route",
]


class ClarificationOption(BaseModel):
    field: str
    candidates: list[GeocodeCandidate]
    hint: str | None = None


class RoutePlanResponse(BaseModel):
    status: PlanStatus
    route: RouteCandidate | None = None
    # All scored candidates for the request (issue #6): in multi-engine mode
    # (ROUTING_PROVIDER="all") this lets clients compare providers side by
    # side. `route` is the selected (highest-scoring) entry, repeated here.
    # Sorted by score, best first; null-scored candidates last.
    candidates: list[RouteCandidate] = Field(default_factory=list)
    explanation: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    clarification: list[ClarificationOption] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    # Id of the recorded history entry (issue #7), usable with
    # GET /v1/history/plans/{plan_id}; null when history is not configured
    # or recording failed (recording never fails a plan).
    plan_id: str | None = None
    # How the weather part went (null when weather is switched off):
    # "ok", "unavailable" (all providers failed), "not_covered" (the forecast
    # does not reach the ride) or "skipped" (nothing to forecast).
    weather_status: str | None = None
