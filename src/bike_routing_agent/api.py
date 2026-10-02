"""FastAPI application exposing POST /v1/route/plan."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from bike_routing_agent.config import Settings, settings
from bike_routing_agent.enrichment.base import SurfaceEnricher
from bike_routing_agent.enrichment.overpass import OverpassEnricher
from bike_routing_agent.graph import build_graph
from bike_routing_agent.models import (
    ClarificationOption,
    Coordinate,
    PlanStatus,
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
from bike_routing_agent.storage.artifacts import (
    ArtifactStore,
    LocalArtifactStore,
    media_type_for,
)
from bike_routing_agent.storage.history import (
    BBox,
    PlanFilter,
    PlanRecord,
    PlanSummary,
    RouteHistory,
    record_from_state,
)

_SAFE_FILENAME = re.compile(r"^[0-9a-f]{32}\.(geojson|gpx)$")
_PLAN_ID = re.compile(r"^[0-9a-f]{32}$")

logger = logging.getLogger(__name__)

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


def build_storage(cfg: Settings) -> tuple[ArtifactStore, RouteHistory | None]:
    """Artifact store and route history for a configuration (issue #7).

    Without ``database_url`` the exports stay under ``export_dir`` and no
    history is kept (the pre-#7 behavior). With it, plans are recorded in
    PostGIS; ``artifact_backend="database"`` moves the exports there too so
    several API instances can serve each other's artifacts.
    """
    if not cfg.database_url:
        return LocalArtifactStore(Path(cfg.export_dir)), None

    # Imported here so a deployment without a database never needs psycopg.
    from bike_routing_agent.storage.postgres import (
        PostgresArtifactStore,
        PostgresDatabase,
        PostgresRouteHistory,
    )

    database = PostgresDatabase(cfg.database_url, max_size=cfg.database_pool_max_size)
    store: ArtifactStore = (
        PostgresArtifactStore(database)
        if cfg.artifact_backend == "database"
        else LocalArtifactStore(Path(cfg.export_dir))
    )
    return store, PostgresRouteHistory(database)


_export_dir = Path(settings.export_dir)

_geocode_provider, _routing_providers = build_providers(settings)
_artifact_store, _history = build_storage(settings)

_graph = build_graph(
    geocode_provider=_geocode_provider,
    routing_providers=_routing_providers,
    artifact_store=_artifact_store,
    ambiguity_margin=settings.geocoder_ambiguity_margin,
    min_confidence=settings.geocoder_min_confidence,
    surface_enricher=build_surface_enricher(settings),
)


def build_graph_for_settings(cfg: Settings) -> Any:
    """Build a fresh graph from the given settings (used by the CLI)."""
    geocode_provider, routing_providers = build_providers(cfg)
    artifact_store, _ = build_storage(cfg)
    return build_graph(
        geocode_provider=geocode_provider,
        routing_providers=routing_providers,
        artifact_store=artifact_store,
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
        plan_id=await _record_plan(final_state),
    )


async def _record_plan(final_state: dict[str, Any]) -> str | None:
    """Record a finished plan in the route history, if one is configured.

    Best effort by design: the plan was already computed, so a history
    outage is logged and reported as ``plan_id: null`` rather than turned
    into a failed request.
    """
    if _history is None:
        return None
    plan_id = final_state.get("route_id") or uuid.uuid4().hex
    try:
        await run_in_threadpool(_history.save, record_from_state(plan_id, final_state))
    except Exception:
        logger.exception("failed to record plan %s in route history", plan_id)
        return None
    return str(plan_id)


@app.get("/v1/routes/{filename}")
async def get_route_artifact(filename: str) -> Response:
    if not _SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=404, detail="artifact not found")
    content = await run_in_threadpool(_artifact_store.get, filename)
    if content is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return Response(content=content, media_type=media_type_for(filename))


def _require_history() -> RouteHistory:
    if _history is None:
        raise HTTPException(
            status_code=503,
            detail="route history is not configured (set DATABASE_URL)",
        )
    return _history


def _parse_bbox(raw: str | None) -> BBox | None:
    if raw is None:
        return None
    try:
        x0, y0, x1, y1 = (float(part) for part in raw.split(","))
    except ValueError:
        raise HTTPException(
            status_code=422, detail="bbox must be 'min_lon,min_lat,max_lon,max_lat'"
        ) from None
    if not (-180 <= x0 <= x1 <= 180 and -90 <= y0 <= y1 <= 90):
        raise HTTPException(status_code=422, detail="bbox is out of range or min > max")
    return (x0, y0, x1, y1)


@app.get("/v1/history/plans", response_model=list[PlanSummary])
async def list_history(
    provider: str | None = None,
    profile: str | None = None,
    status: PlanStatus | None = None,
    bike_type: str | None = None,
    selected_only: bool = False,
    since: datetime | None = None,
    until: datetime | None = None,
    bbox: Annotated[str | None, Query(description="min_lon,min_lat,max_lon,max_lat")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PlanSummary]:
    """Past plans, newest first, filtered by provenance (issue #7)."""
    history = _require_history()
    plan_filter = PlanFilter(
        provider=provider,
        profile=profile,
        status=status,
        bike_type=bike_type,
        selected_only=selected_only,
        since=since,
        until=until,
        bbox=_parse_bbox(bbox),
        limit=limit,
        offset=offset,
    )
    return await run_in_threadpool(history.query, plan_filter)


@app.get("/v1/history/plans/{plan_id}", response_model=PlanRecord)
async def get_history_plan(plan_id: str) -> PlanRecord:
    history = _require_history()
    if not _PLAN_ID.match(plan_id):
        raise HTTPException(status_code=404, detail="plan not found")
    record = await run_in_threadpool(history.get, plan_id)
    if record is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return record


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


_frontend_dir = Path(__file__).parent / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
