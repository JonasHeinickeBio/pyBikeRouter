"""Domain models of the POI feature.

Kept free of imports from ``bike_routing_agent.models`` so the request/response
models there can embed these without a cycle.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

QID_RE = re.compile(r"^Q[1-9]\d{0,12}\Z")
# "de:Schloss Neuschwanstein" -- the OSM `wikipedia` tag format.
WIKIPEDIA_TAG_RE = re.compile(r"^([a-z]{2,3}(?:-[a-z]{2,8})?):(.{1,300})\Z")
LANG_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2,8})?\Z")


class Poi(BaseModel):
    """One point of interest, as found on the map."""

    id: str  # "node/123" -- the OSM element, also the stable key in the UI
    name: str | None = None
    category: str
    kind: Literal["sight", "service"]
    lon: float
    lat: float
    # Links into the open knowledge graph; each is what the OSM mapper tagged.
    wikidata: str | None = None
    wikipedia: str | None = None
    website: str | None = None
    opening_hours: str | None = None
    # How many Wikimedia language editions describe it (Wikidata sitelinks): a
    # measure of how well known it is. None means *not looked up / no Wikidata
    # item*, which is not the same as obscure.
    fame: int | None = Field(default=None, ge=0)
    # Relation to the route it was searched along (None for a bounding-box search).
    distance_from_route_m: float | None = Field(default=None, ge=0)
    along_route_km: float | None = Field(default=None, ge=0)


class PoiLink(BaseModel):
    kind: str  # wikipedia | wikivoyage | commons | wikidata | osm | website
    label: str
    url: str


class PoiInfo(BaseModel):
    """What the open services say about one POI (``GET /v1/pois/info``)."""

    title: str | None = None
    description: str | None = None  # Wikidata's one-line description
    extract: str | None = None  # the opening of the Wikipedia article
    language: str | None = None  # language the text is in
    thumbnail_url: str | None = None
    sitelinks: int | None = None
    links: list[PoiLink] = Field(default_factory=list)
    # The data is CC0 (Wikidata), CC BY-SA (Wikipedia/Commons) and ODbL (OSM).
    attribution: list[str] = Field(default_factory=list)


MAX_ROUTE_POINTS = 20_000


class PoiAlongRouteRequest(BaseModel):
    """Body of ``POST /v1/pois/along-route``: a route shape and what to look for."""

    # [lon, lat] pairs, e.g. a candidate's geometry_geojson coordinates.
    coordinates: list[list[float]] = Field(min_length=2, max_length=MAX_ROUTE_POINTS)
    categories: list[str] | None = None
    buffer_m: float | None = Field(default=None, gt=0)
    limit_per_category: int | None = Field(default=None, ge=1, le=100)

    @field_validator("coordinates")
    @classmethod
    def _valid_positions(cls, value: list[list[float]]) -> list[list[float]]:
        for position in value:
            if len(position) < 2 or not (-180 <= position[0] <= 180 and -90 <= position[1] <= 90):
                raise ValueError("coordinates must be [lon, lat] pairs within range")
        return value


class PoiSearchResponse(BaseModel):
    pois: list[Poi]
    # A per-category limit dropped some results.
    truncated: bool = False
    # How the fame ranking went: ok | partial | unavailable | skipped.
    fame_status: str = "ok"
