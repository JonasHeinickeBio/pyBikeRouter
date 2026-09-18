"""Provider-neutral domain models, validated with Pydantic v2."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MAX_VIA_POINTS = 10
PlaceString = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class BikeType(StrEnum):
    ROAD = "road"
    GRAVEL = "gravel"
    TOURING = "touring"
    MOUNTAIN = "mountain"
    CITY = "city"


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
    destination: PlaceString | Coordinate
    via: list[PlaceString | Coordinate] = Field(default_factory=list, max_length=MAX_VIA_POINTS)
    constraints: RouteConstraints = Field(default_factory=RouteConstraints)


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
    explanation: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    clarification: list[ClarificationOption] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
