"""FastAPI application exposing POST /v1/route/plan."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from bike_routing_agent.config import Settings, settings
from bike_routing_agent.enrichment.base import SurfaceEnricher
from bike_routing_agent.enrichment.overpass import OverpassEnricher
from bike_routing_agent.graph import build_graph
from bike_routing_agent.health import build_health_monitor
from bike_routing_agent.llm.backends import OpenAICompatBackend
from bike_routing_agent.llm.parser import RouteRequestParser
from bike_routing_agent.models import (
    MAX_TEXT_CHARS,
    ClarificationOption,
    Coordinate,
    Interpretation,
    PlanStatus,
    PlanTextRequest,
    RouteCandidate,
    RoutePlanAPIRequest,
    RoutePlanResponse,
)
from bike_routing_agent.providers.base import (
    CacheBackend,
    GeocodeProvider,
    InMemoryTTLCache,
    NamespacedCache,
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
    HistoryStats,
    PlanFilter,
    PlanRecord,
    PlanSummary,
    RouteHistory,
    StatsFilter,
    record_from_state,
)
from bike_routing_agent.weather.base import WeatherProvider
from bike_routing_agent.weather.met_no import MetNoProvider
from bike_routing_agent.weather.open_meteo import OpenMeteoProvider
from bike_routing_agent.weather.service import WeatherService

_SAFE_FILENAME = re.compile(r"^[0-9a-f]{32}\.(geojson|gpx)$")
_PLAN_ID = re.compile(r"^[0-9a-f]{32}$")

logger = logging.getLogger(__name__)

app = FastAPI(title="bike-routing-agent", version="0.1.0")


def build_cache(cfg: Settings) -> CacheBackend:
    """The cache geocoding and Overpass results go through (issue #29).

    ``memory`` is process-local; ``redis`` is shared by every API instance and
    degrades to uncached lookups when Redis is unreachable.
    """
    if cfg.cache_backend == "redis":
        from bike_routing_agent.providers.redis_cache import RedisCacheBackend

        return RedisCacheBackend(
            str(cfg.cache_redis_url),
            prefix=cfg.cache_key_prefix,
            timeout_s=cfg.cache_redis_timeout_s,
        )
    return InMemoryTTLCache()


def build_llm_parser(
    cfg: Settings, *, now: Callable[[], datetime] | None = None
) -> RouteRequestParser | None:
    """The free-text parser for a configuration, or ``None`` when it is off."""
    if not cfg.llm_parser_enabled:
        return None
    backend = None
    if cfg.llm_provider == "openai":
        backend = OpenAICompatBackend(
            base_url=str(cfg.llm_base_url),
            model=str(cfg.llm_model),
            api_key=cfg.llm_api_key.get_secret_value() if cfg.llm_api_key else None,
            timeout_s=cfg.llm_timeout_s,
            max_output_tokens=cfg.llm_max_output_tokens,
        )
    return RouteRequestParser(
        model=str(cfg.llm_model),
        api_key=cfg.anthropic_api_key.get_secret_value() if cfg.anthropic_api_key else None,
        timeout_s=cfg.llm_timeout_s,
        max_output_tokens=cfg.llm_max_output_tokens,
        max_input_chars=MAX_TEXT_CHARS,
        backend=backend,
        **({"now": now} if now is not None else {}),
    )


def build_weather_service(
    cfg: Settings, *, cache: CacheBackend | None = None
) -> WeatherService | None:
    """The weather lookup for a configuration, or ``None`` when it is switched off."""
    if cfg.weather_provider == "none":
        return None
    providers: list[WeatherProvider] = []
    if cfg.weather_provider in ("auto", "open-meteo"):
        providers.append(
            OpenMeteoProvider(base_url=cfg.weather_open_meteo_url, timeout_s=cfg.weather_timeout_s)
        )
    if cfg.weather_provider in ("auto", "met-no"):
        providers.append(
            MetNoProvider(
                user_agent=cfg.weather_user_agent,
                base_url=cfg.weather_met_no_url,
                timeout_s=cfg.weather_timeout_s,
            )
        )
    return WeatherService(
        providers,
        cache=NamespacedCache(cache, "weather") if cache is not None else None,
        cache_ttl_s=cfg.weather_cache_ttl_s,
    )


def build_providers(
    cfg: Settings, *, cache: CacheBackend | None = None
) -> tuple[GeocodeProvider, list[RoutingProvider]]:
    """Instantiate the geocode + routing providers for a configuration.

    With ``geocoder_provider == "pelias"`` the geocoder is served by the
    same self-hosted ORS instance as routing, so both share one
    :class:`OpenRouteServiceClient` (connection reuse, single retry and
    timeout policy). ``config.Settings`` rejects "pelias" for the public
    ORS base URL at construction time. With ``routing_provider`` set to
    another engine, the Pelias geocoder still uses the shared ORS client but
    routing goes through :func:`build_routing_providers`. ``cache`` is the
    shared geocode cache (a per-geocoder in-memory one when omitted).
    """
    geocode_cache = NamespacedCache(cache, "geocode") if cache is not None else None
    if cfg.geocoder_provider == "pelias":
        ors_client = OpenRouteServiceClient(
            api_key=cfg.ors_api_key,
            base_url=cfg.ors_base_url,
            timeout_s=cfg.ors_timeout_s,
            max_retries=cfg.ors_max_retries,
        )
        geocoder: GeocodeProvider = PeliasGeocoder(
            client=ors_client,
            cache=geocode_cache,
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
            cache=geocode_cache,
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
        cache=NamespacedCache(cache, "overpass") if cache is not None else InMemoryTTLCache(),
        cache_ttl_s=cfg.overpass_cache_ttl_s,
    )


def build_storage(cfg: Settings) -> tuple[ArtifactStore, RouteHistory | None]:
    """Artifact store and route history for a configuration (issues #7, #27).

    Without ``database_url`` no history is kept (the pre-#7 behavior); with it,
    plans are recorded in PostGIS. The exports live in ``artifact_backend``:
    ``local`` (files under ``export_dir``, the default), ``database`` (so
    several API instances can serve each other's artifacts, needs a database)
    or ``s3`` (any S3-compatible object store, independent of the database).
    """
    database: Any = None
    if cfg.database_url:
        # Imported here so a deployment without a database never needs psycopg.
        from bike_routing_agent.storage.postgres import PostgresDatabase

        database = PostgresDatabase(
            cfg.database_url,
            max_size=cfg.database_pool_max_size,
            auto_migrate=cfg.auto_migrate,
        )

    store: ArtifactStore
    if cfg.artifact_backend == "database":
        from bike_routing_agent.storage.postgres import PostgresArtifactStore

        store = PostgresArtifactStore(database)
    elif cfg.artifact_backend == "s3":
        from bike_routing_agent.storage.s3 import S3ArtifactStore

        store = S3ArtifactStore(
            bucket=str(cfg.s3_bucket),
            prefix=cfg.s3_prefix,
            endpoint_url=cfg.s3_endpoint_url,
            region=cfg.s3_region,
            path_style=cfg.s3_path_style,
        )
    else:
        store = LocalArtifactStore(Path(cfg.export_dir))

    if database is None:
        return store, None
    from bike_routing_agent.storage.postgres import PostgresRouteHistory

    return store, PostgresRouteHistory(database)


_export_dir = Path(settings.export_dir)

_cache = build_cache(settings)
_geocode_provider, _routing_providers = build_providers(settings, cache=_cache)
_artifact_store, _history = build_storage(settings)

_weather_service = build_weather_service(settings, cache=_cache)
_llm_parser = build_llm_parser(settings)

_health_monitor = build_health_monitor(
    geocoder=_geocode_provider,
    routing_providers=_routing_providers,
    artifact_store=_artifact_store,
    history=_history,
    cache=_cache,
    weather=_weather_service,
    # With the exports in the database a database outage fails plans, so it
    # gates readiness; otherwise history is best effort and only degrades.
    database_is_critical=settings.artifact_backend == "database",
    timeout_s=settings.health_probe_timeout_s,
    ttl_s=settings.health_cache_ttl_s,
    geocoder_ttl_s=settings.health_geocoder_cache_ttl_s,
)

_graph = build_graph(
    geocode_provider=_geocode_provider,
    routing_providers=_routing_providers,
    artifact_store=_artifact_store,
    ambiguity_margin=settings.geocoder_ambiguity_margin,
    min_confidence=settings.geocoder_min_confidence,
    surface_enricher=build_surface_enricher(settings, cache=_cache),
    alternative_dedup_threshold_m=settings.alternative_dedup_threshold_m,
    llm_parser=_llm_parser,
    weather_service=_weather_service,
    weather_max_samples=settings.weather_max_samples,
    weather_spacing_km=settings.weather_sample_spacing_km,
)


def build_graph_for_settings(cfg: Settings) -> Any:
    """Build a fresh graph from the given settings (used by the CLI)."""
    cache = build_cache(cfg)
    geocode_provider, routing_providers = build_providers(cfg, cache=cache)
    artifact_store, _ = build_storage(cfg)
    return build_graph(
        geocode_provider=geocode_provider,
        routing_providers=routing_providers,
        artifact_store=artifact_store,
        ambiguity_margin=cfg.geocoder_ambiguity_margin,
        min_confidence=cfg.geocoder_min_confidence,
        surface_enricher=build_surface_enricher(cfg, cache=cache),
        alternative_dedup_threshold_m=cfg.alternative_dedup_threshold_m,
        llm_parser=build_llm_parser(cfg),
        weather_service=build_weather_service(cfg, cache=cache),
        weather_max_samples=cfg.weather_max_samples,
        weather_spacing_km=cfg.weather_sample_spacing_km,
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
        "max_alternatives": request.max_alternatives,
        "departure_time": (
            request.departure_time.isoformat() if request.departure_time is not None else None
        ),
    }

    final_state = await _graph.ainvoke({"raw_input": raw_input})
    return await _plan_response(final_state)


@app.post("/v1/route/plan-text", response_model=RoutePlanResponse)
async def plan_route_from_text(request: PlanTextRequest) -> RoutePlanResponse:
    """Plan from a request written in plain words (issue #30).

    The text is turned into the same structured request ``/v1/route/plan``
    takes -- places stay strings for the geocoder, which asks for clarification
    when they are ambiguous -- and the response says how it was understood
    (``interpretation``). Needs ``LLM_PARSER_ENABLED``.
    """
    if _llm_parser is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "free-text planning is not enabled: set LLM_PARSER_ENABLED=true and LLM_MODEL "
                "(see docs/llm-parser.md)"
            ),
        )
    raw_input = {
        "text": request.text,
        "timezone": request.timezone,
        "max_alternatives": request.max_alternatives,
    }
    final_state = await _graph.ainvoke({"raw_input": raw_input})
    return await _plan_response(final_state)


@app.get("/v1/capabilities")
async def capabilities() -> dict[str, bool]:
    """Which optional features this instance has, for clients to adapt to."""
    return {
        "text_planning": _llm_parser is not None,
        "weather": _weather_service is not None,
        "history": _history is not None,
    }


async def _plan_response(final_state: dict[str, Any]) -> RoutePlanResponse:
    status = final_state.get("status", "provider_failure")
    route = None
    candidates: list[RouteCandidate] = []
    explanation = None
    artifacts: dict[str, str] = {}
    clarification = [
        ClarificationOption.model_validate(c) for c in final_state.get("clarification", [])
    ]

    if status == "ready":
        route = RouteCandidate.model_validate(final_state["selected_candidate"]).model_copy(
            update={"raw_provider_response": None}
        )
        # Every scored candidate (issue #6) so the web UI can compare
        # providers side by side; the selected candidate is included.
        # The score node already ranked them (issue #24), best first, so
        # index 0 is `route`; states without ranks fall back to score order
        # (null-scored last).
        candidates = sorted(
            (
                RouteCandidate.model_validate(c).model_copy(update={"raw_provider_response": None})
                for c in final_state.get("candidates", [])
            ),
            key=lambda c: (
                c.rank if c.rank is not None else 10**9,
                -(c.score if c.score is not None else float("-inf")),
            ),
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
        candidates=candidates,
        explanation=explanation,
        artifacts=artifacts,
        clarification=clarification,
        errors=final_state.get("errors", []),
        plan_id=await _record_plan(final_state),
        weather_status=final_state.get("weather_status"),
        interpretation=(
            Interpretation.model_validate(final_state["interpretation"])
            if final_state.get("interpretation")
            else None
        ),
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
    # Opt-in (S3_PRESIGNED_URL_TTL_S): hand the client a short-lived link to
    # the object store instead of streaming the bytes through this process.
    ttl = settings.s3_presigned_url_ttl_s
    presign = getattr(_artifact_store, "presigned_url", None)
    if ttl is not None and presign is not None:
        url = await run_in_threadpool(presign, filename, ttl)
        return RedirectResponse(url, status_code=307)
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


@app.get("/v1/history/stats", response_model=HistoryStats)
async def history_stats(
    bike_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> HistoryStats:
    """Evaluation aggregates over recorded plans (issue #7 dashboards)."""
    history = _require_history()
    stats_filter = StatsFilter(bike_type=bike_type, since=since, until=until)
    return await run_in_threadpool(history.stats, stats_filter)


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
    """Liveness: the process answers. Says nothing about upstream services."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> JSONResponse:
    """Readiness: can this instance plan right now? (issue #25)

    200 when ready (``ok`` or ``degraded``), 503 when not. Probe results are
    cached, so polling this endpoint is cheap for upstream services.
    """
    report = await _health_monitor.check()
    return JSONResponse(report.as_dict(), status_code=200 if report.ready else 503)


_frontend_dir = Path(__file__).parent / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
