"""FastAPI application exposing POST /v1/route/plan."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from bike_routing_agent.config import Settings, settings
from bike_routing_agent.graph import build_graph
from bike_routing_agent.models import (
    ClarificationOption,
    Coordinate,
    RouteCandidate,
    RoutePlanAPIRequest,
    RoutePlanResponse,
)
from bike_routing_agent.providers.base import GeocodeProvider, RoutingProvider
from bike_routing_agent.providers.geocoder import NominatimGeocoder
from bike_routing_agent.providers.ors import OpenRouteServiceAdapter
from bike_routing_agent.providers.ors_client import OpenRouteServiceClient
from bike_routing_agent.providers.pelias import PeliasGeocoder

_SAFE_FILENAME = re.compile(r"^[0-9a-f]{32}\.(geojson|gpx)$")

app = FastAPI(title="bike-routing-agent", version="0.1.0")


def build_providers(cfg: Settings) -> tuple[GeocodeProvider, RoutingProvider]:
    """Instantiate the geocode + routing providers for a configuration.

    With ``geocoder_provider == "pelias"`` the geocoder is served by the
    same self-hosted ORS instance as routing, so both share one
    :class:`OpenRouteServiceClient` (connection reuse, single retry and
    timeout policy). ``config.Settings`` rejects "pelias" for the public
    ORS base URL at construction time.
    """
    if cfg.geocoder_provider == "pelias":
        ors_client = OpenRouteServiceClient(
            api_key=cfg.ors_api_key,
            base_url=cfg.ors_base_url,
            timeout_s=cfg.ors_timeout_s,
            max_retries=cfg.ors_max_retries,
        )
        geocoder: GeocodeProvider = PeliasGeocoder(
            client=ors_client,
            cache_ttl_s=cfg.geocoder_cache_ttl_s,
        )
        router = OpenRouteServiceAdapter(
            api_key=cfg.ors_api_key,
            base_url=cfg.ors_base_url,
            timeout_s=cfg.ors_timeout_s,
            max_retries=cfg.ors_max_retries,
            ors_client=ors_client,
        )
        return geocoder, router

    return (
        NominatimGeocoder(
            base_url=cfg.geocoder_base_url,
            user_agent=cfg.geocoder_user_agent,
            timeout_s=cfg.geocoder_timeout_s,
            cache_ttl_s=cfg.geocoder_cache_ttl_s,
        ),
        OpenRouteServiceAdapter(
            api_key=cfg.ors_api_key,
            base_url=cfg.ors_base_url,
            timeout_s=cfg.ors_timeout_s,
            max_retries=cfg.ors_max_retries,
        ),
    )


_export_dir = Path(settings.export_dir)

_geocode_provider, _routing_provider = build_providers(settings)

_graph = build_graph(
    geocode_provider=_geocode_provider,
    routing_provider=_routing_provider,
    export_dir=_export_dir,
    ambiguity_margin=settings.geocoder_ambiguity_margin,
    min_confidence=settings.geocoder_min_confidence,
)


def build_graph_for_settings(cfg: Settings) -> Any:
    """Build a fresh graph from the given settings (used by the CLI)."""
    geocode_provider, routing_provider = build_providers(cfg)
    return build_graph(
        geocode_provider=geocode_provider,
        routing_provider=routing_provider,
        export_dir=Path(cfg.export_dir),
        ambiguity_margin=cfg.geocoder_ambiguity_margin,
        min_confidence=cfg.geocoder_min_confidence,
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


_frontend_dir = Path(__file__).parent / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
