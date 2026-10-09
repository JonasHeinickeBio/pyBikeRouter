"""Central configuration and provider-mapping tables.

Provider-specific strings (e.g. ORS profile names) are kept here so graph
nodes and scoring code stay provider-neutral.
"""

from __future__ import annotations

from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The public openrouteservice does not serve the Pelias geocoding endpoints
# (they 404, same as /v2/health), so Pelias-backed geocoding needs another
# base URL. Note a stock self-hosted ORS has no geocoder either: something
# must serve a Pelias API at <ORS_BASE_URL>/pelias/v1 (docs/geocoding.md).
PUBLIC_ORS_BASE_URLS = frozenset({"https://api.openrouteservice.org"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ors_api_key: str = ""
    ors_base_url: str = "https://api.openrouteservice.org"
    ors_timeout_s: float = 10.0
    ors_max_retries: int = 2

    # "nominatim" (default): OSM Nominatim search API, works with the public
    # ORS. "pelias": Pelias search served by a self-hosted ORS instance;
    # rejected at startup when ors_base_url points at the public API.
    geocoder_provider: Literal["nominatim", "pelias"] = "nominatim"
    geocoder_base_url: str = "https://nominatim.openstreetmap.org"
    geocoder_timeout_s: float = 5.0
    geocoder_user_agent: str = "bike-routing-agent/0.1"
    geocoder_cache_ttl_s: int = 3600
    geocoder_ambiguity_margin: float = 0.05
    geocoder_min_confidence: float = 0.3

    valhalla_base_url: str = "http://localhost:8002"
    valhalla_timeout_s: float = 30.0
    valhalla_max_retries: int = 1

    # Routing engine selection. "brouter" routes against a local/self-hosted
    # BRouter RouteServer (see docker/compose.yaml, profile "brouter") and
    # "valhalla" against a self-hosted Valhalla meili server; "all" queries
    # ors + brouter + valhalla in parallel and lets scoring pick the best
    # candidate. No automatic fallback between engines is attempted.
    routing_provider: Literal["ors", "brouter", "valhalla", "all"] = "ors"
    brouter_base_url: str = "http://127.0.0.1:17777"
    # Also ask BRouter for the alternative profiles above (one extra request each).
    brouter_alternatives: bool = True
    # How long the alternatives may take; whatever is done by then is used, the rest dropped.
    brouter_alternatives_timeout_s: float = 8.0
    brouter_timeout_s: float = 30.0
    brouter_max_retries: int = 1

    # OSM surface enrichment (issue #3). Off by default: the public Overpass
    # API is rate-limited and the PostGIS pipeline that should serve this at
    # production scale is not in place yet (docs/enrichment.md).
    osm_enrichment_enabled: bool = False
    overpass_base_url: str = "https://overpass-api.de/api/interpreter"
    overpass_timeout_s: float = 20.0
    overpass_max_retries: int = 1
    # Half-width of the corridor queried around the route; way matching then
    # accepts polylines within 2x this distance of a segment midpoint.
    overpass_buffer_m: float = 25.0
    overpass_cache_ttl_s: int = 86_400

    # Shared cache for geocode and Overpass results (issue #29). "memory" is
    # per-process (the default); "redis" shares it across API instances and
    # needs the `cache` extra. A Redis outage only makes lookups uncached.
    cache_backend: Literal["memory", "redis"] = "memory"
    cache_redis_url: str | None = None  # e.g. redis://127.0.0.1:6379/0
    cache_key_prefix: str = "bike-routing"
    cache_redis_timeout_s: float = 2.0

    # Two candidates whose routes stay within this many metres of each other
    # (discrete Frechet distance) are one alternative, not two (issue #24).
    # Deliberately small: engines snapping to the same streets differ by a
    # few metres, while a genuinely different route differs by blocks.
    alternative_dedup_threshold_m: float = 50.0

    # Free-text requests ("a 50 km gravel loop from Braunschweig, tomorrow at 8")
    # parsed by an LLM into the structured request (issue #30). Off by default;
    # needs the `llm` extra. The model has no default on purpose: pick one
    # deliberately (docs/llm-parser.md). The API key is read from the standard
    # ANTHROPIC_API_KEY environment variable (or the SDK's own credential chain)
    # and is never logged.
    llm_parser_enabled: bool = False
    llm_model: str | None = None
    # "anthropic" (Messages API) or "openai" (any OpenAI-compatible
    # /chat/completions server, e.g. Helmholtz Blablador, vLLM, Ollama).
    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    llm_base_url: str | None = None  # required for llm_provider=openai
    llm_api_key: SecretStr | None = None  # bearer token for llm_provider=openai
    anthropic_api_key: SecretStr | None = None
    llm_timeout_s: float = 30.0
    llm_max_output_tokens: int = 8000

    # Weather along the route (free, keyless providers). "auto" tries Open-Meteo
    # (global, hourly, gusts/UV/probability) and falls back to MET Norway;
    # "none" switches the feature off (no request leaves the service). Route
    # coordinates are sent to the chosen provider.
    weather_provider: Literal["auto", "dwd", "open-meteo", "met-no", "none"] = "auto"
    weather_open_meteo_url: str = "https://api.open-meteo.com/v1/forecast"
    # Fill what the first provider lacks (UV, feels-like, ...) from the others.
    weather_merge: bool = True
    weather_dwd_url: str = "https://api.brightsky.dev/weather"
    weather_met_no_url: str = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
    # MET Norway rejects anonymous/generic clients: say who you are.
    weather_user_agent: str = "bike-routing-agent/0.1 github.com/JonasHeinickeBio/pyBikeRouter"
    weather_timeout_s: float = 8.0
    weather_cache_ttl_s: float = 1800.0
    # Compare departures this many hours before/after the requested one (0 = off).
    weather_option_hours_before: int = 3
    weather_option_hours_after: int = 6
    weather_max_samples: int = 5
    weather_sample_spacing_km: float = 10.0

    # Points of interest (issue #55): found with Overpass, ranked and described with
    # Wikidata and Wikipedia (keyless; Wikimedia requires an identifying User-Agent).
    # Searched areas and route shapes are sent to those services; false switches it off.
    poi_enabled: bool = True
    # Comma separated, tried in order: public Overpass instances are often overloaded.
    poi_overpass_urls: str = "https://overpass-api.de/api/interpreter"
    poi_wikidata_url: str = "https://www.wikidata.org/w/api.php"
    # `{lang}` is replaced by the article language ("en", "de", ...).
    poi_wikipedia_url: str = "https://{lang}.wikipedia.org"
    poi_user_agent: str = (
        "bike-routing-agent/0.1 (+https://github.com/JonasHeinickeBio/pyBikeRouter)"
    )
    poi_timeout_s: float = 25.0
    # Extra rounds over the instances after a failure (public Overpass often answers 504 once).
    poi_max_retries: int = 1
    poi_cache_ttl_s: float = 86_400.0
    # How far from the route a sight may be (services are capped at 500 m regardless).
    poi_default_buffer_m: float = 1500.0
    poi_max_buffer_m: float = 5000.0
    poi_per_category_limit: int = 40

    # Readiness endpoint (issue #25): each component is probed at most once
    # per TTL, with a hard per-probe timeout. The geocoder gets a longer TTL
    # because the default one is the public Nominatim (usage policy).
    health_probe_timeout_s: float = 5.0
    health_cache_ttl_s: float = 30.0
    health_geocoder_cache_ttl_s: float = 300.0

    export_dir: str = "exports"

    # Persistence (issue #7). With a DATABASE_URL (PostgreSQL + PostGIS) every
    # answered plan is recorded in the route history and the /v1/history
    # endpoints are enabled. artifact_backend "database" additionally keeps
    # the GeoJSON/GPX exports in the database so several API instances can
    # share them; "local" (default) keeps them under EXPORT_DIR.
    database_url: str | None = None
    database_pool_max_size: int = 5
    # Apply pending schema migrations on first database use (issue #28). Turn
    # off to migrate in a release step with `bike-router db migrate`; the app
    # then only warns when migrations are pending.
    auto_migrate: bool = True
    artifact_backend: Literal["local", "database", "s3"] = "local"

    # S3-compatible artifact storage (issue #27; the `s3` extra). Credentials
    # are never settings: boto3 reads AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
    # (or a profile / instance role) itself.
    s3_bucket: str | None = None
    s3_prefix: str = ""
    s3_endpoint_url: str | None = None  # e.g. http://127.0.0.1:8333 (compose `s3` profile)
    s3_region: str | None = None
    s3_path_style: bool = False  # most self-hosted S3 servers need True
    # Unset: GET /v1/routes/{file} streams the bytes itself. Set: it redirects
    # to a presigned URL valid for this many seconds (the bucket must then be
    # reachable by clients).
    s3_presigned_url_ttl_s: int | None = None

    # Retention (issue #27): unset keeps everything forever (the default).
    # Applied by `bike-router retention prune`, never implicitly.
    retention_max_age_days: int | None = None
    retention_orphan_grace_hours: int = 24
    retention_batch_size: int = 500

    log_level: str = "INFO"

    @model_validator(mode="after")
    def _check_database_settings(self) -> Settings:
        if self.artifact_backend == "database" and not self.database_url:
            raise ValueError("artifact_backend='database' requires DATABASE_URL to be set")
        if self.artifact_backend == "s3" and not self.s3_bucket:
            raise ValueError("artifact_backend='s3' requires S3_BUCKET to be set")
        ttl = self.s3_presigned_url_ttl_s
        if ttl is not None and not 1 <= ttl <= 604800:
            raise ValueError("s3_presigned_url_ttl_s must be between 1 and 604800 seconds")
        if self.retention_max_age_days is not None and self.retention_max_age_days < 1:
            raise ValueError(
                f"retention_max_age_days must be >= 1 (got {self.retention_max_age_days})"
            )
        if self.retention_orphan_grace_hours < 0 or self.retention_batch_size < 1:
            raise ValueError(
                "retention_orphan_grace_hours must be >= 0 and retention_batch_size >= 1"
            )
        if self.database_url and self.database_pool_max_size < 1:
            raise ValueError(
                f"database_pool_max_size must be >= 1 (got {self.database_pool_max_size})"
            )
        return self

    @model_validator(mode="after")
    def _check_pelias_requires_self_hosted_ors(self) -> Settings:
        if (
            self.geocoder_provider == "pelias"
            and self.ors_base_url.rstrip("/") in PUBLIC_ORS_BASE_URLS
        ):
            raise ValueError(
                "geocoder_provider='pelias' requires a self-hosted openrouteservice "
                "backend: the public api.openrouteservice.org does not expose the "
                "Pelias geocoding endpoints (they return 404). Use "
                "geocoder_provider='nominatim' (the default) for the public API."
            )
        return self

    @model_validator(mode="after")
    def _check_brouter_settings_when_active(self) -> Settings:
        if self.routing_provider in ("brouter", "all"):
            if self.brouter_timeout_s <= 0:
                raise ValueError(
                    "brouter_timeout_s must be > 0 when routing_provider='brouter' "
                    "or 'all' (got "
                    f"{self.brouter_timeout_s})"
                )
            if self.brouter_max_retries < 0:
                raise ValueError(
                    "brouter_max_retries must be >= 0 when routing_provider='brouter' "
                    "or 'all' (got "
                    f"{self.brouter_max_retries})"
                )
        return self

    @model_validator(mode="after")
    def _check_osm_enrichment_settings_when_enabled(self) -> Settings:
        if self.osm_enrichment_enabled:
            if self.overpass_timeout_s <= 0:
                raise ValueError(
                    "overpass_timeout_s must be > 0 when osm_enrichment_enabled "
                    f"(got {self.overpass_timeout_s})"
                )
            if self.overpass_max_retries < 0:
                raise ValueError(
                    "overpass_max_retries must be >= 0 when osm_enrichment_enabled "
                    f"(got {self.overpass_max_retries})"
                )
            if self.overpass_buffer_m <= 0:
                raise ValueError(
                    "overpass_buffer_m must be > 0 when osm_enrichment_enabled "
                    f"(got {self.overpass_buffer_m})"
                )
        return self

    @model_validator(mode="after")
    def _check_alternative_dedup_threshold(self) -> Settings:
        if self.alternative_dedup_threshold_m <= 0:
            raise ValueError(
                "alternative_dedup_threshold_m must be > 0 "
                f"(got {self.alternative_dedup_threshold_m})"
            )
        return self

    @model_validator(mode="after")
    def _check_cache_settings(self) -> Settings:
        if self.cache_backend == "redis" and not self.cache_redis_url:
            raise ValueError("cache_backend='redis' requires CACHE_REDIS_URL to be set")
        if self.cache_redis_timeout_s <= 0:
            raise ValueError(
                f"cache_redis_timeout_s must be > 0 (got {self.cache_redis_timeout_s})"
            )
        if not self.cache_key_prefix:
            raise ValueError("cache_key_prefix must not be empty")
        return self

    @model_validator(mode="after")
    def _check_llm_settings(self) -> Settings:
        if self.llm_parser_enabled and not (self.llm_model and self.llm_model.strip()):
            raise ValueError(
                "llm_parser_enabled requires LLM_MODEL (choose a model id for your provider; "
                "see docs/llm-parser.md)"
            )
        if self.llm_parser_enabled and self.llm_provider == "openai":
            if not (self.llm_base_url and self.llm_base_url.strip()):
                raise ValueError(
                    "llm_provider=openai requires LLM_BASE_URL (the server's /v1 root; "
                    "see docs/llm-parser.md)"
                )
            if not self.llm_base_url.startswith(("http://", "https://")):
                raise ValueError("llm_base_url must start with http:// or https://")
        if self.llm_timeout_s <= 0:
            raise ValueError(f"llm_timeout_s must be > 0 (got {self.llm_timeout_s})")
        if not 256 <= self.llm_max_output_tokens <= 64000:
            raise ValueError(
                "llm_max_output_tokens must be between 256 and 64000 "
                f"(got {self.llm_max_output_tokens})"
            )
        return self

    @model_validator(mode="after")
    def _check_poi_settings(self) -> Settings:
        if self.poi_timeout_s <= 0:
            raise ValueError(f"poi_timeout_s must be > 0 (got {self.poi_timeout_s})")
        if self.poi_cache_ttl_s < 0:
            raise ValueError("poi_cache_ttl_s must be >= 0")
        if self.poi_max_retries < 0:
            raise ValueError("poi_max_retries must be >= 0")
        if not 0 < self.poi_default_buffer_m <= self.poi_max_buffer_m:
            raise ValueError(
                "poi_default_buffer_m must be > 0 and no larger than poi_max_buffer_m "
                f"(got {self.poi_default_buffer_m} / {self.poi_max_buffer_m})"
            )
        if self.poi_per_category_limit < 1:
            raise ValueError("poi_per_category_limit must be >= 1")
        if not [u for u in self.poi_overpass_urls.split(",") if u.strip()]:
            raise ValueError("poi_overpass_urls must list at least one URL")
        if "{lang}" not in self.poi_wikipedia_url:
            raise ValueError("poi_wikipedia_url must contain the {lang} placeholder")
        return self

    @model_validator(mode="after")
    def _check_weather_settings(self) -> Settings:
        if self.weather_timeout_s <= 0:
            raise ValueError(f"weather_timeout_s must be > 0 (got {self.weather_timeout_s})")
        if self.weather_cache_ttl_s < 0:
            raise ValueError("weather_cache_ttl_s must be >= 0")
        for name in ("weather_option_hours_before", "weather_option_hours_after"):
            if not 0 <= getattr(self, name) <= 12:
                raise ValueError(f"{name} must be between 0 and 12 (got {getattr(self, name)})")
        if not 2 <= self.weather_max_samples <= 10:
            raise ValueError(
                f"weather_max_samples must be between 2 and 10 (got {self.weather_max_samples})"
            )
        if self.weather_sample_spacing_km <= 0:
            raise ValueError(
                f"weather_sample_spacing_km must be > 0 (got {self.weather_sample_spacing_km})"
            )
        if (
            self.weather_provider in ("auto", "dwd", "met-no")
            and not self.weather_user_agent.strip()
        ):
            raise ValueError(
                "weather_user_agent must identify your application (MET Norway, Bright Sky)"
            )
        return self

    @model_validator(mode="after")
    def _check_health_settings(self) -> Settings:
        if self.health_probe_timeout_s <= 0:
            raise ValueError(
                f"health_probe_timeout_s must be > 0 (got {self.health_probe_timeout_s})"
            )
        if self.health_cache_ttl_s < 0 or self.health_geocoder_cache_ttl_s < 0:
            raise ValueError("health cache TTLs must be >= 0")
        return self

    @model_validator(mode="after")
    def _check_valhalla_settings_when_active(self) -> Settings:
        if self.routing_provider in ("valhalla", "all"):
            if self.valhalla_timeout_s <= 0:
                raise ValueError(
                    "valhalla_timeout_s must be > 0 when routing_provider='valhalla' "
                    f"(got {self.valhalla_timeout_s})"
                )
            if self.valhalla_max_retries < 0:
                raise ValueError(
                    "valhalla_max_retries must be >= 0 when routing_provider='valhalla' "
                    f"(got {self.valhalla_max_retries})"
                )
        return self


settings = Settings()

# Internal bike type -> ORS cycling profile. Kept configurable and isolated
# here because engine profiles are an approximation of real-world bike
# categories (gravel riding in particular has no dedicated ORS profile).
# ORS 9.x ships exactly four cycling profiles (regular/mountain/road/electric;
# the former cycling-recreational was removed), so several bike types share a
# profile here. ebike is the only new type with dedicated ORS support;
# commuter and recumbent are approximated with cycling-regular on ORS and get
# their distinct behaviour from the stock BRouter profiles below.
ORS_PROFILE_MAP: dict[str, str] = {
    "road": "cycling-road",
    "gravel": "cycling-regular",
    "touring": "cycling-regular",
    "mountain": "cycling-mountain",
    "city": "cycling-regular",
    "ebike": "cycling-electric",
    "commuter": "cycling-regular",
    "recumbent": "cycling-regular",
}

# Internal bike type -> BRouter profile name (the profile= request parameter).
# A "custom_" prefix is BRouter's convention: the server resolves
# "custom_<name>" to the file <name>.brf under its CUSTOMPROFILESPATH mount,
# which we point at docker/brouter/profiles/ so the gravel and touring
# profiles are versioned in this repository (see docs/providers.md).
# Non-prefixed entries are stock profiles shipped with the BRouter image.
BROUTER_PROFILE_MAP: dict[str, str] = {
    "road": "fastbike",
    "gravel": "custom_gravel-v2",
    "touring": "custom_touring-v1",
    "mountain": "mtb",
    "city": "trekking",
    # Stock profiles shipped with the BRouter image (misc/profiles2 in the
    # upstream repo). BRouter's cost model has no e-assist term, so ebike
    # rides use the fastbike profile (faster target speed, cycle-infrastructure
    # preference). The commuter profile is our own (commuter-v1: trekking with
    # traffic estimates on; the stock fastbike-verylowtraffic it replaced put
    # 63 % of 14 test routes on unprotected main roads, docs/profile-evaluation.md);
    # vm-forum-liegerad-schnell is the recumbent (Liegerad) forum profile.
    "ebike": "fastbike",
    "commuter": "custom_commuter-v1",
    "recumbent": "vm-forum-liegerad-schnell",
}

# Other BRouter profiles worth showing next to the one a bike type maps to: the
# same trip priced differently (calmer, more direct, more off-road), so the web
# UI can compare real alternatives with one engine. They are suggestions, not
# promises: each result is described by facts about that route, not by the
# profile's name (docs/profile-evaluation.md has what each profile does).
BROUTER_ALTERNATIVE_PROFILES: dict[str, tuple[str, ...]] = {
    "road": ("custom_commuter-v1",),
    "gravel": ("custom_touring-v1", "mtb"),
    "touring": ("fastbike", "custom_gravel-v2"),
    "mountain": ("custom_gravel-v2", "custom_touring-v1"),
    "city": ("fastbike",),
    "ebike": ("custom_commuter-v1",),
    "commuter": ("fastbike",),
    "recumbent": ("custom_touring-v1",),
}

# BRouter profile -> the profile variable that turns traffic avoidance on. BRouter
# accepts a profile variable per request as ``profile:<name>=<number>`` (numbers
# only: 1 = true, 0 = false), which is how ``avoid_high_traffic_roads`` is applied
# per request instead of being baked into the profile file. Profiles not listed
# have no such setting (mtb, the recumbent profile) and the adapter says so.
# Measured over 14 routes (docs/profile-evaluation.md): on vs off, mean share on main
# roads without a bike lane 13 vs 22 % (touring), 14 vs 21 % (trekking/city), 14 vs 24 %
# (commuter), 56 vs 76 % (fastbike), at most +3 % time.
BROUTER_TRAFFIC_SWITCHES: dict[str, str] = {
    "custom_gravel-v1": "consider_traffic_estimate",
    "custom_gravel-v2": "consider_traffic_estimate",
    "custom_touring-v1": "consider_traffic",
    "custom_commuter-v1": "consider_traffic",
    "trekking": "consider_traffic",
    "fastbike": "consider_traffic",
    "fastbike-verylowtraffic": "consider_traffic",
}

# Internal bike type -> Valhalla costing model. Current Valhalla bicycle
# costing has no per-bike-type option (unlike ORS/BRouter profiles), so all
# types map to the single "bicycle" costing; bike-type differentiation comes
# from the other engines and the scoring step. Kept as a map so a future
# Valhalla costing (e.g. e-assist) can be wired in here only.
VALHALLA_PROFILE_MAP: dict[str, str] = {
    "road": "bicycle",
    "gravel": "bicycle",
    "touring": "bicycle",
    "mountain": "bicycle",
    "city": "bicycle",
    "ebike": "bicycle",
    "commuter": "bicycle",
    "recumbent": "bicycle",
}

# OSM ``surface`` tag value -> surface-quality category (issue #3). The
# enrichment data-quality policy (enrichment/quality.py) and RouteMetrics
# surface_coverage keys share this vocabulary; values outside the map are
# treated as unknown, so adding a value here is the only way to make it
# count. Colon-suffixed values (paving_stones:30) normalise to their base.
SURFACE_TAXONOMY: dict[str, str] = {
    # Hard, bound surfaces.
    "asphalt": "paved",
    "paved": "paved",
    "concrete": "paved",
    "paving_stones": "masonry",
    "sett": "masonry",
    "cobblestone": "masonry",
    "blockstone": "masonry",
    "metal": "masonry",
    "wood": "masonry",
    # Firm but unbound: rideable by almost everyone, still loose material.
    "compacted": "compacted",
    "fine_gravel": "compacted",
    "gravel": "loose",
    "grit": "loose",
    "pebblestone": "loose",
    "unhewn_cobblestone": "loose",
    # Soft natural material.
    "ground": "natural_soft",
    "dirt": "natural_soft",
    "earth": "natural_soft",
    "sand": "natural_soft",
    "grass": "natural_soft",
    "grass_paver": "natural_soft",
    "mud": "natural_soft",
    "rock": "natural_soft",
}


def surface_category(token: str) -> str | None:
    """Surface-quality category for a caller-supplied surface token.

    Accepts the category names themselves (``paved``, ``loose``, ...) and raw
    OSM ``surface`` values (``asphalt``, ``paving_stones:30``); anything else
    -- a typo, an engine-specific word like ``unpaved`` -- is ``None``, i.e.
    unknown, never guessed.
    """
    normalized = token.strip().lower().split(":", 1)[0]
    if normalized in SURFACE_TAXONOMY.values():
        return normalized
    return SURFACE_TAXONOMY.get(normalized)


def resolve_surface_tokens(tokens: list[str]) -> tuple[frozenset[str], list[str]]:
    """Split surface tokens into (resolved categories, unrecognised tokens)."""
    categories: set[str] = set()
    unrecognised: list[str] = []
    for token in tokens:
        category = surface_category(token)
        if category is None:
            unrecognised.append(token)
        else:
            categories.add(category)
    return frozenset(categories), unrecognised


# OSM ``tracktype`` grade -> category. Grades are an ordered OSM vocabulary
# (grade1 hard-packed/paved .. grade5 impassable for cars), so the mapping
# is fixed rather than configurable.
TRACKTYPE_TAXONOMY: dict[str, str] = {
    "grade1": "paved",
    "grade2": "compacted",
    "grade3": "loose",
    "grade4": "natural_soft",
    "grade5": "natural_soft",
}
