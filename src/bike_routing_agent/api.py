"""FastAPI application exposing POST /v1/route/plan."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from bike_routing_agent.config import Settings, settings
from bike_routing_agent.enrichment.base import SurfaceEnricher
from bike_routing_agent.enrichment.overpass import OverpassEnricher
from bike_routing_agent.graph import build_graph
from bike_routing_agent.models import (
    ClarificationOption,
    Coordinate,
    RouteCandidate,
    RoutePlanAPIRequest,
    RoutePlanResponse,
)
from bike_routing_agent.providers.base import (
    CacheBackend,
    GeocodeProvider,
    InMemoryTTLCache,
    RoutingProvider,
)
from bike_routing_agent.providers.brouter import BRouterAdapter
from bike_routing_agent.providers.geocoder import NominatimGeocoder
from bike_routing_agent.providers.ors import OpenRouteServiceAdapter
from bike_routing_agent.providers.ors_client import OpenRouteServiceClient
from bike_routing_agent.providers.pelias import PeliasGeocoder
from bike_routing_agent.providers.valhalla import ValhallaAdapter

_SAFE_FILENAME = re.compile(r"^[0-9a-f]{32}\.(geojson|gpx)$")

app = FastAPI(title="bike-routing-agent", version="0.1.0")


def build_providers(cfg: Settings) -> tuple[GeocodeProvider, list[RoutingProvider]]:
    """Instantiate the geocode + routing providers for a configuration.

    With ``geocoder_provider == "pelias"`` the geocoder is served by the
    same self-hosted ORS instance as routing, so both share one
    :class:`OpenRouteServiceClient` (connection reuse, single retry and
    timeout policy). ``config.Settings`` rejects "pelias" for the public
    ORS base URL at construction time. With ``routing_provider`` set to
    another engine, the Pelias geocoder still uses the shared ORS client but
    routing goes through :func:`build_routing_providers`.
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
        if cfg.routing_provider == "ors":
            # Same self-hosted ORS instance serves geocoding and routing;
            # share one client. Any other engine comes from the selector.
            routers: list[RoutingProvider] = [
                OpenRouteServiceAdapter(
                    api_key=cfg.ors_api_key,
                    base_url=cfg.ors_base_url,
                    timeout_s=cfg.ors_timeout_s,
                    max_retries=cfg.ors_max_retries,
                    ors_client=ors_client,
                )
            ]
        else:
            routers = build_routing_providers(cfg)
        return geocoder, routers

    return (
        NominatimGeocoder(
            base_url=cfg.geocoder_base_url,
            user_agent=cfg.geocoder_user_agent,
            timeout_s=cfg.geocoder_timeout_s,
            cache_ttl_s=cfg.geocoder_cache_ttl_s,
        ),
        build_routing_providers(cfg),
    )


def build_routing_providers(cfg: Settings) -> list[RoutingProvider]:
    """Routing engines for a configuration, per ``cfg.routing_provider``.

    Normally a single-entry list; ``routing_provider="all"`` returns ORS,
    BRouter and Valhalla so the route node queries them in parallel and
    scoring picks the best candidate (issue #2). There is no automatic
    fallback between engines: a request only fails when every returned
    provider fails.
    """
    if cfg.routing_provider == "brouter":
        return [
            BRouterAdapter(
                base_url=cfg.brouter_base_url,
                timeout_s=cfg.brouter_timeout_s,
                max_retries=cfg.brouter_max_retries,
            )
        ]
    if cfg.routing_provider == "valhalla":
        return [
            ValhallaAdapter(
                base_url=cfg.valhalla_base_url,
                timeout_s=cfg.valhalla_timeout_s,
                max_retries=cfg.valhalla_max_retries,
            )
        ]
    ors_provider: RoutingProvider = OpenRouteServiceAdapter(
        api_key=cfg.ors_api_key,
        base_url=cfg.ors_base_url,
        timeout_s=cfg.ors_timeout_s,
        max_retries=cfg.ors_max_retries,
    )
    if cfg.routing_provider != "all":
        return [ors_provider]
    return [
        ors_provider,
        BRouterAdapter(
            base_url=cfg.brouter_base_url,
            timeout_s=cfg.brouter_timeout_s,
            max_retries=cfg.brouter_max_retries,
        ),
        ValhallaAdapter(
            base_url=cfg.valhalla_base_url,
            timeout_s=cfg.valhalla_timeout_s,
            max_retries=cfg.valhalla_max_retries,
        ),
    ]


def build_surface_enricher(
    cfg: Settings, *, cache: CacheBackend | None = None
) -> SurfaceEnricher | None:
    """Surface enricher for a configuration, or ``None`` when disabled.

    ``osm_enrichment_enabled`` stays off by default: the public Overpass
    instance is rate-limited and shared, and the PostGIS pipeline meant to
    serve this at production scale is still a design (docs/enrichment.md).
    """
    if not cfg.osm_enrichment_enabled:
        return None
    return OverpassEnricher(
        base_url=cfg.overpass_base_url,
        timeout_s=cfg.overpass_timeout_s,
        max_retries=cfg.overpass_max_retries,
        buffer_m=cfg.overpass_buffer_m,
        cache=cache if cache is not None else InMemoryTTLCache(),
        cache_ttl_s=cfg.overpass_cache_ttl_s,
    )


_export_dir = Path(settings.export_dir)

_geocode_provider, _routing_providers = build_providers(settings)

_graph = build_graph(
    geocode_provider=_geocode_provider,
    routing_providers=_routing_providers,
    export_dir=_export_dir,
    ambiguity_margin=settings.geocoder_ambiguity_margin,
    min_confidence=settings.geocoder_min_confidence,
    surface_enricher=build_surface_enricher(settings),
)


def build_graph_for_settings(cfg: Settings) -> Any:
    """Build a fresh graph from the given settings (used by the CLI)."""
    geocode_provider, routing_providers = build_providers(cfg)
    return build_graph(
        geocode_provider=geocode_provider,
        routing_providers=routing_providers,
        export_dir=Path(cfg.export_dir),
        ambiguity_margin=cfg.geocoder_ambiguity_margin,
        min_confidence=cfg.geocoder_min_confidence,
        surface_enricher=build_surface_enricher(cfg),
    )


def _place_to_raw(value: str | Coordinate | None) -> str | dict | None:
    """Loop requests carry no destination (issue #5) -- None passes through
    so the parse node records it as absent rather than the string "None"."""
    if value is None:
        return None
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
