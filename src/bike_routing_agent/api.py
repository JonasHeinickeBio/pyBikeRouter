"""FastAPI application exposing POST /v1/route/plan."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from bike_routing_agent.config import settings
from bike_routing_agent.graph import build_graph
from bike_routing_agent.models import (
    ClarificationOption,
    Coordinate,
    RouteCandidate,
    RoutePlanAPIRequest,
    RoutePlanResponse,
)
from bike_routing_agent.providers.geocoder import NominatimGeocoder
from bike_routing_agent.providers.ors import OpenRouteServiceAdapter

_SAFE_FILENAME = re.compile(r"^[0-9a-f]{32}\.(geojson|gpx)$")

app = FastAPI(title="bike-routing-agent", version="0.1.0")

_export_dir = Path(settings.export_dir)

_geocode_provider = NominatimGeocoder(
    base_url=settings.geocoder_base_url,
    user_agent=settings.geocoder_user_agent,
    timeout_s=settings.geocoder_timeout_s,
    cache_ttl_s=settings.geocoder_cache_ttl_s,
)
_routing_provider = OpenRouteServiceAdapter(
    api_key=settings.ors_api_key,
    base_url=settings.ors_base_url,
    timeout_s=settings.ors_timeout_s,
    max_retries=settings.ors_max_retries,
)

_graph = build_graph(
    geocode_provider=_geocode_provider,
    routing_provider=_routing_provider,
    export_dir=_export_dir,
    ambiguity_margin=settings.geocoder_ambiguity_margin,
    min_confidence=settings.geocoder_min_confidence,
)


def _place_to_raw(value: str | Coordinate) -> str | dict:
    return value if isinstance(value, str) else value.model_dump(mode="json")


@app.post("/v1/route/plan", response_model=RoutePlanResponse)
async def plan_route(request: RoutePlanAPIRequest) -> RoutePlanResponse:
    raw_input = {
        "origin": _place_to_raw(request.origin),
        "destination": _place_to_raw(request.destination),
        "via": [_place_to_raw(v) for v in request.via],
        "constraints": request.constraints.model_dump(mode="json"),
    }

    final_state = await _graph.ainvoke({"raw_input": raw_input})

    status = final_state.get("status", "provider_failure")
    route = None
    explanation = None
    artifacts: dict[str, str] = {}
    clarification = [
        ClarificationOption.model_validate(c) for c in final_state.get("clarification", [])
    ]

    if status == "ready":
        route = RouteCandidate.model_validate(final_state["selected_candidate"]).model_copy(
            update={"raw_provider_response": None}
        )
        explanation = final_state.get("explanation")
        exported = final_state.get("artifacts", {})
        if "geojson_file" in exported:
            artifacts["geojson_url"] = f"/v1/routes/{exported['geojson_file']}"
        if "gpx_file" in exported:
            artifacts["gpx_url"] = f"/v1/routes/{exported['gpx_file']}"

    return RoutePlanResponse(
        status=status,
        route=route,
        explanation=explanation,
        artifacts=artifacts,
        clarification=clarification,
        errors=final_state.get("errors", []),
    )


@app.get("/v1/routes/{filename}")
async def get_route_artifact(filename: str) -> FileResponse:
    if not _SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=404, detail="artifact not found")
    path = _export_dir / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(path)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
