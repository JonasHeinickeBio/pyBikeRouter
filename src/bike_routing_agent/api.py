"""FastAPI application exposing POST /v1/route/plan."""

from __future__ import annotations

import logging
import os
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
from pydantic import BaseModel, Field, StringConstraints, ValidationError

from bike_routing_agent.chat.models import (
    ChatRequest,
    PlanError,
    SightsUnavailableError,
    TextUnavailableError,
)
from bike_routing_agent.chat.service import ChatService
from bike_routing_agent.config import PUBLIC_ORS_BASE_URLS, Settings, settings
from bike_routing_agent.enrichment.base import SurfaceEnricher
from bike_routing_agent.enrichment.overpass import OverpassEnricher
from bike_routing_agent.errors import ProviderError
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
from bike_routing_agent.poi.categories import CATEGORIES, resolve_categories
from bike_routing_agent.poi.fetcher import OverpassPoiFetcher
from bike_routing_agent.poi.models import (
    LANG_RE,
    QID_RE,
    WIKIPEDIA_TAG_RE,
    Poi,
    PoiAlongRouteRequest,
    PoiInfo,
    PoiSearchResponse,
)
from bike_routing_agent.poi.service import PoiService
from bike_routing_agent.poi.wikimedia import WikimediaResolver
from bike_routing_agent.poi.wikipedia_languages import WIKIPEDIA_LANGUAGES
from bike_routing_agent.providers.base import (
    CacheBackend,
    GeocodeProvider,
    InMemoryTTLCache,
    NamespacedCache,
    RoutingProvider,
)
from bike_routing_agent.providers.brouter import BRouterAdapter
from bike_routing_agent.providers.brouter_downloads import (
    MAX_SEGMENTS_PER_JOB,
    SEGMENT_NAME_RE,
    DownloadBusyError,
    DownloadJob,
    SegmentDownloader,
    SegmentInfo,
)
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
from bike_routing_agent.weather.dwd import DwdProvider
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
    # Germany first: DWD is the authoritative source there and open for commercial
    # use; it declines routes outside Germany, so the others answer those.
    if cfg.weather_provider in ("auto", "dwd"):
        providers.append(
            DwdProvider(
                base_url=cfg.weather_dwd_url,
                timeout_s=cfg.weather_timeout_s,
                user_agent=cfg.weather_user_agent,
            )
        )
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
        merge=cfg.weather_merge,
    )


def build_segment_downloader(cfg: Settings) -> SegmentDownloader | None:
    """The BRouter tile downloader, or ``None`` unless ``BROUTER_SEGMENTS_DIR`` names the folder
    BRouter reads its tiles from (and this process can write to)."""
    if not cfg.brouter_segments_dir:
        return None
    directory = Path(cfg.brouter_segments_dir)
    if not directory.is_dir() or not os.access(directory, os.W_OK):
        logger.warning(
            "BROUTER_SEGMENTS_DIR %s is not a writable directory; tile downloads are off", directory
        )
        return None
    return SegmentDownloader(
        directory,
        base_url=cfg.brouter_segments_url,
        max_bytes=cfg.brouter_segments_max_mb * 2**20,
    )


def build_optional_routing_providers(
    cfg: Settings, configured: list[RoutingProvider]
) -> list[RoutingProvider]:
    """Engines a single request may pick instead of the configured ones.

    Offered: BRouter always, openrouteservice when it can work (an API key, or a self-hosted
    base URL). Valhalla only when configured -- its default address answers nothing. Engines
    that are already configured are not repeated.
    """
    have = {p.name for p in configured}
    optional: list[RoutingProvider] = []
    if "ors" not in have and (
        cfg.ors_api_key or cfg.ors_base_url.rstrip("/") not in PUBLIC_ORS_BASE_URLS
    ):
        optional.append(
            OpenRouteServiceAdapter(
                api_key=cfg.ors_api_key,
                base_url=cfg.ors_base_url,
                timeout_s=cfg.ors_timeout_s,
                max_retries=cfg.ors_max_retries,
            )
        )
    if "brouter" not in have:
        optional.append(
            BRouterAdapter(
                base_url=cfg.brouter_base_url,
                timeout_s=cfg.brouter_timeout_s,
                max_retries=cfg.brouter_max_retries,
                alternatives=cfg.brouter_alternatives,
                alternatives_timeout_s=cfg.brouter_alternatives_timeout_s,
                max_concurrency=cfg.brouter_max_concurrency,
            )
        )
    return optional


def build_poi_service(cfg: Settings, *, cache: CacheBackend | None = None) -> PoiService | None:
    """The POI search/resolve service, or ``None`` when ``POI_ENABLED`` is false (issue #55)."""
    if not cfg.poi_enabled:
        return None
    poi_cache = NamespacedCache(cache, "poi") if cache is not None else None
    return PoiService(
        OverpassPoiFetcher(
            base_urls=[u.strip() for u in cfg.poi_overpass_urls.split(",") if u.strip()],
            timeout_s=cfg.poi_timeout_s,
            max_retries=cfg.poi_max_retries,
            user_agent=cfg.poi_user_agent,
            cache=poi_cache,
            cache_ttl_s=cfg.poi_cache_ttl_s,
        ),
        WikimediaResolver(
            wikidata_url=cfg.poi_wikidata_url,
            wikipedia_url=cfg.poi_wikipedia_url,
            user_agent=cfg.poi_user_agent,
            timeout_s=min(cfg.poi_timeout_s, 10.0),
            cache=poi_cache,
        ),
        per_category_limit=cfg.poi_per_category_limit,
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
                alternatives=cfg.brouter_alternatives,
                alternatives_timeout_s=cfg.brouter_alternatives_timeout_s,
                max_concurrency=cfg.brouter_max_concurrency,
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
            alternatives=cfg.brouter_alternatives,
            alternatives_timeout_s=cfg.brouter_alternatives_timeout_s,
            max_concurrency=cfg.brouter_max_concurrency,
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
_poi_service = build_poi_service(settings, cache=_cache)
_segment_downloader = build_segment_downloader(settings)
_optional_routing_providers = build_optional_routing_providers(settings, _routing_providers)

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
    weather_option_hours=(
        settings.weather_option_hours_before,
        settings.weather_option_hours_after,
    ),
    poi_service=_poi_service,
    optional_routing_providers=_optional_routing_providers,
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
        weather_option_hours=(cfg.weather_option_hours_before, cfg.weather_option_hours_after),
        poi_service=build_poi_service(cfg, cache=cache),
        optional_routing_providers=build_optional_routing_providers(cfg, routing_providers),
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
        "poi_stops": (
            request.poi_stops.model_dump(mode="json") if request.poi_stops is not None else None
        ),
        "routing_engines": request.routing_engines,
    }

    final_state = await _graph.ainvoke({"raw_input": raw_input})
    if raw_input["poi_stops"] and final_state.get("poi_stops"):
        final_state = await _without_stops_if_unroutable(raw_input, final_state)
    return await _plan_response(final_state)


async def _without_stops_if_unroutable(
    raw_input: dict[str, Any], final_state: dict[str, Any]
) -> dict[str, Any]:
    """Plan again without the famous-POI stops when the engines could not route through them.

    A POI is a point on the map, not on a road: an engine may refuse to snap it (a summit, a
    pedestrian-only old town) or find no way to it. The ride is still wanted, so it is
    planned without the stops and the response says so (``poi_stops_status: "dropped"``).
    """
    if final_state.get("status") not in ("provider_failure", "no_route"):
        return final_state
    logger.info(
        "routing through the POI stops %s failed (%s); planning without them",
        [p.get("name") or p.get("id") for p in final_state.get("poi_stops", [])],
        final_state.get("errors"),
    )
    retry = await _graph.ainvoke({"raw_input": {**raw_input, "poi_stops": None}})
    if retry.get("status") == "ready":
        retry["poi_stops"] = []
        retry["poi_stops_status"] = "dropped"
        return retry
    return final_state


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


class _ApiPlanner:
    """The chat's planner: the same code path as ``POST /v1/route/plan`` and ``/plan-text``."""

    async def plan(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            model = RoutePlanAPIRequest.model_validate(request)
        except ValidationError as exc:
            raise PlanError(_validation_message(exc)) from exc
        response = await plan_route(model)
        return response.model_dump(mode="json")

    async def plan_text(self, text: str, timezone: str | None) -> dict[str, Any]:
        if _llm_parser is None:
            raise TextUnavailableError
        try:
            model = PlanTextRequest(text=text, timezone=timezone)
        except ValidationError as exc:
            raise PlanError(_validation_message(exc)) from exc
        response = await plan_route_from_text(model)
        if response.status == "invalid" and response.errors:
            raise PlanError(str(response.errors[0].get("message") or "I could not read that."))
        return response.model_dump(mode="json")


class _ApiSights:
    """Well-known places along a route for the chat, from the POI service."""

    async def along(self, line: list[list[float]], limit: int) -> list[dict[str, Any]]:
        if _poi_service is None:
            raise SightsUnavailableError
        from bike_routing_agent.poi.categories import SIGHT_KEYS

        try:
            found = await _poi_service.along_route(
                [(p[0], p[1]) for p in line],
                list(SIGHT_KEYS),
                buffer_m=settings.poi_default_buffer_m,
                linked_only=True,
            )
        except ProviderError as exc:
            raise SightsUnavailableError from exc
        ranked = [p for p in found.pois if p.kind == "sight" and p.fame is not None]
        return [p.model_dump(mode="json") for p in ranked[:limit]]


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(
        (f"{'.'.join(str(p) for p in e['loc'])}: " if e["loc"] else "") + str(e["msg"])
        for e in exc.errors()[:3]
    )


def build_chat_service(cfg: Settings) -> ChatService | None:
    """The chat, or ``None`` when ``CHAT_ENABLED`` is false."""
    if not cfg.chat_enabled:
        return None
    return ChatService(
        _ApiPlanner(),
        sights=_ApiSights() if _poi_service is not None else None,
        text_enabled=_llm_parser is not None,
        max_sessions=cfg.chat_max_sessions,
    )


_chat_service = build_chat_service(settings)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    suggestions: list[str] = Field(default_factory=list)
    # A plan to draw this turn (a new route, or the alternatives of the last one).
    plan: RoutePlanResponse | None = None
    focus_rank: int | None = None
    intent: str | None = None
    # "place_choice": an ambiguous place waits for an answer; "guided": a question is open.
    awaiting: str | None = None


@app.post("/v1/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """One chat turn: plan a route (one line, free text or step by step), change it, compare
    the alternatives, ask about it, look for sights, get the files. Returns the reply, quick
    answers and, when there is one to draw, the plan. See docs/chat.md."""
    if _chat_service is None:
        raise HTTPException(status_code=503, detail="the chat is switched off (CHAT_ENABLED)")
    reply = await _chat_service.send(
        request.message, session_id=request.session_id, timezone=request.timezone
    )
    return ChatResponse(
        session_id=reply.session_id,
        reply=reply.reply,
        suggestions=reply.suggestions,
        plan=RoutePlanResponse.model_validate(reply.plan) if reply.plan else None,
        focus_rank=reply.focus_rank,
        intent=reply.intent,
        awaiting=reply.awaiting,
    )


@app.get("/v1/capabilities")
async def capabilities() -> dict[str, Any]:
    """Which optional features this instance has, for clients to adapt to.

    Booleans for features; ``engines`` lists the routing engines a plan request may name in
    ``routing_engines`` (the configured ones and the optional ones the server offers).
    """
    return {
        "text_planning": _llm_parser is not None,
        "weather": _weather_service is not None,
        "history": _history is not None,
        "pois": _poi_service is not None,
        "segment_downloads": _segment_downloader is not None,
        "chat": _chat_service is not None,
        "engines": [p.name for p in [*_routing_providers, *_optional_routing_providers]],
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
        poi_stops=(
            [Poi.model_validate(p) for p in final_state["poi_stops"]]
            if final_state.get("poi_stops") is not None
            else None
        ),
        poi_stops_status=final_state.get("poi_stops_status"),
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


def _require_segment_downloads() -> SegmentDownloader:
    if _segment_downloader is None:
        raise HTTPException(
            status_code=503,
            detail="map downloads are off (set BROUTER_SEGMENTS_DIR to BRouter's tile folder)",
        )
    return _segment_downloader


class SegmentDownloadRequest(BaseModel):
    segments: list[Annotated[str, StringConstraints(pattern=SEGMENT_NAME_RE.pattern)]] = Field(
        min_length=1, max_length=MAX_SEGMENTS_PER_JOB
    )


class SegmentsResponse(BaseModel):
    segments: list[SegmentInfo]
    free_bytes: int


@app.get("/v1/routing/segments", response_model=SegmentsResponse)
async def routing_segments(
    names: Annotated[str, Query(description="comma separated tile names, e.g. W5_N50")],
) -> SegmentsResponse:
    """Whether BRouter map tiles are present and how big each is at the source, so the form
    can tell the user before anything is downloaded."""
    downloader = _require_segment_downloads()
    try:
        infos = await downloader.inspect([n for n in names.split(",") if n])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SegmentsResponse(segments=infos, free_bytes=downloader.free_bytes())


@app.post("/v1/routing/segments/download", response_model=DownloadJob, status_code=202)
async def start_segment_download(request: SegmentDownloadRequest) -> DownloadJob:
    """Start downloading BRouter map tiles (large: ~100-200 MB each). Poll the job."""
    downloader = _require_segment_downloads()
    try:
        return downloader.start(request.segments)
    except DownloadBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/routing/segments/download/{job_id}", response_model=DownloadJob)
async def segment_download_status(job_id: str) -> DownloadJob:
    job = _require_segment_downloads().job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such download")
    return job


def _require_pois() -> PoiService:
    if _poi_service is None:
        raise HTTPException(
            status_code=503, detail="points of interest are switched off (POI_ENABLED)"
        )
    return _poi_service


def _poi_categories(raw: list[str] | None) -> list[str] | None:
    try:
        resolve_categories(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return raw


def _provider_unavailable(exc: ProviderError) -> HTTPException:
    return HTTPException(status_code=502, detail=exc.message)


@app.get("/v1/pois/categories")
async def poi_categories() -> list[dict[str, str]]:
    """The categories the POI endpoints accept, for building a filter."""
    return [{"key": c.key, "label": c.label, "kind": c.kind} for c in CATEGORIES]


@app.post("/v1/pois/along-route", response_model=PoiSearchResponse)
async def pois_along_route(request: PoiAlongRouteRequest) -> PoiSearchResponse:
    """POIs near a route, the best-known first, each with its distance from the route and
    its position along it. Informational: nothing here changes a route or its score."""
    service = _require_pois()
    categories = _poi_categories(request.categories)
    buffer_m = min(request.buffer_m or settings.poi_default_buffer_m, settings.poi_max_buffer_m)
    line = [(p[0], p[1]) for p in request.coordinates]
    try:
        result = await service.along_route(
            line,
            categories,
            buffer_m=buffer_m,
            per_category_limit=request.limit_per_category,
        )
    except ProviderError as exc:
        raise _provider_unavailable(exc) from exc
    return PoiSearchResponse(
        pois=result.pois, truncated=result.truncated, fame_status=result.fame_status
    )


# A map view larger than this is too much for one Overpass request.
_MAX_BBOX_DEGREES = 0.5


@app.get("/v1/pois/in-bbox", response_model=PoiSearchResponse)
async def pois_in_bbox(
    bbox: Annotated[str, Query(description="min_lon,min_lat,max_lon,max_lat")],
    categories: Annotated[str | None, Query(description="comma separated keys")] = None,
    limit_per_category: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> PoiSearchResponse:
    """POIs inside a map view (for browsing without a route)."""
    service = _require_pois()
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in bbox.split(","))
    except ValueError:
        raise HTTPException(
            status_code=422, detail="bbox must be min_lon,min_lat,max_lon,max_lat"
        ) from None
    if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
        raise HTTPException(status_code=422, detail="bbox is out of range or empty")
    if max_lon - min_lon > _MAX_BBOX_DEGREES or max_lat - min_lat > _MAX_BBOX_DEGREES:
        raise HTTPException(
            status_code=422,
            detail=f"bbox is too large (at most {_MAX_BBOX_DEGREES} degrees wide); zoom in",
        )
    keys = _poi_categories([c for c in categories.split(",") if c] if categories else None)
    try:
        result = await service.in_bbox(
            (min_lon, min_lat, max_lon, max_lat), keys, per_category_limit=limit_per_category
        )
    except ProviderError as exc:
        raise _provider_unavailable(exc) from exc
    return PoiSearchResponse(
        pois=result.pois, truncated=result.truncated, fame_status=result.fame_status
    )


@app.get("/v1/pois/info", response_model=PoiInfo)
async def poi_info(
    wikidata: Annotated[str | None, Query()] = None,
    wikipedia: Annotated[str | None, Query(description="OSM style, e.g. de:Title")] = None,
    osm_id: Annotated[str | None, Query(description="node/123")] = None,
    website: Annotated[str | None, Query()] = None,
    lang: Annotated[str, Query(description="language of the text, e.g. en")] = "en",
) -> PoiInfo:
    """What Wikipedia, Wikidata and Wikivoyage say about a POI, with links to read more.
    Best effort: parts the open services cannot deliver right now are left out."""
    service = _require_pois()
    lang = lang.lower()
    if not LANG_RE.fullmatch(lang):
        raise HTTPException(status_code=422, detail="lang must be a language code like en or de")
    if lang not in WIKIPEDIA_LANGUAGES:
        lang = "en"  # a well-formed code Wikipedia does not have: read it in English
    if wikidata is not None and not QID_RE.match(wikidata):
        raise HTTPException(status_code=422, detail="wikidata must look like Q42")
    if wikipedia is not None and not WIKIPEDIA_TAG_RE.match(wikipedia):
        raise HTTPException(status_code=422, detail="wikipedia must look like de:Title")
    if osm_id is not None and not re.match(r"^(node|way|relation)/\d+$", osm_id):
        raise HTTPException(status_code=422, detail="osm_id must look like node/123")
    if website is not None and not website.startswith(("http://", "https://")):
        website = None
    return await service.info(
        wikidata=wikidata, wikipedia=wikipedia, osm_id=osm_id, website=website, lang=lang
    )


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
