"""select_poi_stops node: make the route pass the best-known sights (issue #55).

Runs after geocoding, before routing. It looks for sights in a corridor around the
straight line origin -> (vias) -> destination, takes the most famous ones and inserts
them as via points in travel order. Best effort: when POIs are unavailable the plan
continues without stops and ``poi_stops_status`` says why -- it never fails the plan.
POIs only add via points; they do not enter the score.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from bike_routing_agent.errors import ProviderError
from bike_routing_agent.models import MAX_VIA_POINTS, PoiStopsRequest
from bike_routing_agent.poi.geo import LonLat, cumulative_lengths_m, project_onto_line
from bike_routing_agent.poi.service import PoiService
from bike_routing_agent.poi.stops import select_stops
from bike_routing_agent.state import RouteAgentState

logger = logging.getLogger(__name__)

PoiStopsNodeFn = Callable[[RouteAgentState], Awaitable[dict[str, Any]]]


def _lonlat(coordinate: dict[str, Any]) -> LonLat:
    return (float(coordinate["lon"]), float(coordinate["lat"]))


def build_poi_stops_node(*, service: PoiService | None) -> PoiStopsNodeFn:
    async def select_poi_stops(state: RouteAgentState) -> dict[str, Any]:
        raw = state.get("poi_stops_request")
        if not raw:
            return {}
        if state.get("constraints", {}).get("return_to_origin"):
            return {"poi_stops": [], "poi_stops_status": "unsupported"}
        if service is None:
            return {"poi_stops": [], "poi_stops_status": "unavailable"}

        request = PoiStopsRequest.model_validate(raw)
        origin = state.get("resolved_origin")
        destination = state.get("resolved_destination")
        if origin is None or destination is None:
            return {"poi_stops": [], "poi_stops_status": "unavailable"}
        vias = list(state.get("resolved_via", []))
        room = MAX_VIA_POINTS - len(vias)
        if room < 1:
            return {"poi_stops": [], "poi_stops_status": "none_found"}

        # The caller's own via points shape the line the stops must lie along.
        line = _line(origin, vias, destination)
        if len(line) < 2 or line[0] == line[-1]:
            return {"poi_stops": [], "poi_stops_status": "none_found"}
        try:
            result = await service.along_route(
                line,
                request.categories,
                buffer_m=request.corridor_km * 1000,
                linked_only=True,
            )
        except ProviderError as exc:
            logger.warning("POI stop search failed: %s", exc.message)
            return {"poi_stops": [], "poi_stops_status": "unavailable"}
        if result.fame_status == "unavailable":
            # Without fame there is no "most famous"; do not pick at random.
            return {"poi_stops": [], "poi_stops_status": "unavailable"}

        length_km = cumulative_lengths_m(line)[-1] / 1000
        stops = select_stops(
            result.pois,
            count=min(request.count, room),
            line_length_km=length_km,
            min_fame=request.min_fame,
            avoid=[_lonlat(origin), _lonlat(destination), *(_lonlat(v) for v in vias)],
        )
        if not stops:
            return {"poi_stops": [], "poi_stops_status": "none_found"}

        # Merge with the caller's vias by position along the line.
        cumulative = cumulative_lengths_m(line)
        ordered: list[tuple[float, dict[str, Any]]] = [
            (project_onto_line(_lonlat(v), line, cumulative)[1], v) for v in vias
        ]
        ordered += [
            (float(s.along_route_km or 0.0) * 1000, {"lon": s.lon, "lat": s.lat}) for s in stops
        ]
        ordered.sort(key=lambda item: item[0])
        return {
            "resolved_via": [coordinate for _, coordinate in ordered],
            "poi_stops": [s.model_dump(mode="json") for s in stops],
            "poi_stops_status": "ok",
        }

    return select_poi_stops


def _line(
    origin: dict[str, Any], vias: list[dict[str, Any]], destination: dict[str, Any]
) -> list[LonLat]:
    return [_lonlat(origin), *(_lonlat(v) for v in vias), _lonlat(destination)]
