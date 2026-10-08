"""Settings validation and geocoder-provider wiring tests."""

import pytest
from pydantic import ValidationError

from bike_routing_agent.api import (
    build_providers,
    build_routing_providers,
    build_surface_enricher,
)
from bike_routing_agent.config import (
    BROUTER_PROFILE_MAP,
    ORS_PROFILE_MAP,
    VALHALLA_PROFILE_MAP,
    Settings,
)
from bike_routing_agent.enrichment.overpass import OverpassEnricher
from bike_routing_agent.models import BikeType
from bike_routing_agent.providers.brouter import BRouterAdapter
from bike_routing_agent.providers.geocoder import NominatimGeocoder
from bike_routing_agent.providers.ors import OpenRouteServiceAdapter
from bike_routing_agent.providers.pelias import PeliasGeocoder
from bike_routing_agent.providers.valhalla import ValhallaAdapter

# ----------------------------------------------------------------------
# Settings validation
# ----------------------------------------------------------------------


def test_default_geocoder_provider_is_nominatim():
    settings = Settings(_env_file=None)
    assert settings.geocoder_provider == "nominatim"


def test_pelias_geocoder_rejected_for_public_ors():
    with pytest.raises(ValidationError, match="self-hosted"):
        Settings(_env_file=None, geocoder_provider="pelias")


def test_pelias_geocoder_accepted_for_self_hosted_ors():
    settings = Settings(
        _env_file=None,
        geocoder_provider="pelias",
        ors_base_url="https://ors.internal.example.org",
    )
    assert settings.geocoder_provider == "pelias"


def test_unknown_geocoder_provider_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, geocoder_provider="geocodio")  # type: ignore[arg-type]


def test_brouter_timeout_must_be_positive_when_brouter_active():
    with pytest.raises(ValidationError, match="brouter_timeout_s"):
        Settings(_env_file=None, routing_provider="brouter", brouter_timeout_s=0)


def test_brouter_retries_must_not_be_negative_when_brouter_active():
    with pytest.raises(ValidationError, match="brouter_max_retries"):
        Settings(_env_file=None, routing_provider="brouter", brouter_max_retries=-1)


def test_brouter_timeout_must_be_positive_when_all_includes_brouter():
    with pytest.raises(ValidationError, match="brouter_timeout_s"):
        Settings(_env_file=None, routing_provider="all", brouter_timeout_s=0)


def test_brouter_retries_must_not_be_negative_when_all_includes_brouter():
    with pytest.raises(ValidationError, match="brouter_max_retries"):
        Settings(_env_file=None, routing_provider="all", brouter_max_retries=-1)


def test_brouter_settings_unchecked_for_other_providers():
    settings = Settings(
        _env_file=None,
        routing_provider="ors",
        brouter_timeout_s=0,
        brouter_max_retries=-1,
    )
    assert settings.routing_provider == "ors"


def test_valhalla_timeout_must_be_positive_when_valhalla_active():
    with pytest.raises(ValidationError, match="valhalla_timeout_s"):
        Settings(_env_file=None, routing_provider="valhalla", valhalla_timeout_s=0)


def test_valhalla_retries_must_not_be_negative_when_all_includes_valhalla():
    with pytest.raises(ValidationError, match="valhalla_max_retries"):
        Settings(_env_file=None, routing_provider="all", valhalla_max_retries=-1)


def test_valhalla_settings_unchecked_for_other_providers():
    settings = Settings(
        _env_file=None,
        routing_provider="ors",
        valhalla_timeout_s=0,
        valhalla_max_retries=-1,
    )
    assert settings.routing_provider == "ors"


# ----------------------------------------------------------------------
# Profile maps
# ----------------------------------------------------------------------


def test_profile_maps_cover_every_bike_type():
    values = {bike_type.value for bike_type in BikeType}
    assert set(ORS_PROFILE_MAP) == values
    assert set(BROUTER_PROFILE_MAP) == values
    assert set(VALHALLA_PROFILE_MAP) == values


def test_valhalla_maps_every_bike_type_to_the_single_bicycle_costing():
    # Valhalla has no per-bike-type bicycle costing; the map exists to make
    # that constraint explicit and future per-type options easy to add.
    assert set(VALHALLA_PROFILE_MAP.values()) == {"bicycle"}


def test_ebike_uses_dedicated_ors_electric_profile():
    assert ORS_PROFILE_MAP["ebike"] == "cycling-electric"


def test_commuter_and_recumbent_use_distinct_brouter_stock_profiles():
    assert BROUTER_PROFILE_MAP["commuter"] == "fastbike-verylowtraffic"
    assert BROUTER_PROFILE_MAP["recumbent"] == "vm-forum-liegerad-schnell"


# ----------------------------------------------------------------------
# build_providers wiring
# ----------------------------------------------------------------------


def test_build_providers_default_returns_nominatim():
    cfg = Settings(_env_file=None)
    geocoder, routers = build_providers(cfg)
    assert isinstance(geocoder, NominatimGeocoder)
    assert geocoder.name == "nominatim"
    assert [r.name for r in routers] == ["ors"]


def test_build_providers_pelias_shares_ors_client():
    cfg = Settings(
        _env_file=None,
        geocoder_provider="pelias",
        ors_base_url="https://ors.internal.example.org",
        ors_api_key="secret",
    )
    geocoder, routers = build_providers(cfg)
    assert isinstance(geocoder, PeliasGeocoder)
    assert geocoder.name == "pelias"
    assert [r.name for r in routers] == ["ors"]
    # The routing adapter and the geocoder must share one ORS client.
    assert routers[0]._ors is geocoder._client


def test_build_providers_brouter_selects_brouter_adapter():
    cfg = Settings(_env_file=None, routing_provider="brouter")
    geocoder, routers = build_providers(cfg)
    assert isinstance(geocoder, NominatimGeocoder)
    assert [type(r) for r in routers] == [BRouterAdapter]
    assert routers[0].name == "brouter"


def test_build_providers_pelias_with_brouter_keeps_ors_client_for_geocoder_only():
    cfg = Settings(
        _env_file=None,
        geocoder_provider="pelias",
        ors_base_url="https://ors.internal.example.org",
        ors_api_key="secret",
        routing_provider="brouter",
    )
    geocoder, routers = build_providers(cfg)
    assert isinstance(geocoder, PeliasGeocoder)
    assert [type(r) for r in routers] == [BRouterAdapter]
    assert routers[0].name == "brouter"


def test_build_routing_providers_valhalla_selects_valhalla_adapter():
    cfg = Settings(_env_file=None, routing_provider="valhalla")
    routers = build_routing_providers(cfg)
    assert [type(r) for r in routers] == [ValhallaAdapter]
    assert routers[0].name == "valhalla"


def test_build_routing_providers_all_returns_the_three_engines_in_scoring_order():
    cfg = Settings(
        _env_file=None,
        routing_provider="all",
        ors_api_key="secret",
        geocoder_provider="pelias",
        ors_base_url="https://ors.internal.example.org",
    )
    routers = build_routing_providers(cfg)
    assert [r.name for r in routers] == ["ors", "brouter", "valhalla"]


def test_build_providers_all_routes_through_all_three_engines():
    cfg = Settings(_env_file=None, routing_provider="all", ors_api_key="secret")
    geocoder, routers = build_providers(cfg)
    assert isinstance(geocoder, NominatimGeocoder)
    assert [r.name for r in routers] == ["ors", "brouter", "valhalla"]
    assert isinstance(routers[0], OpenRouteServiceAdapter)


# ----------------------------------------------------------------------
# OSM surface enrichment settings (issue #3)
# ----------------------------------------------------------------------


def test_osm_enrichment_is_disabled_by_default():
    cfg = Settings(_env_file=None)
    assert cfg.osm_enrichment_enabled is False
    assert cfg.overpass_base_url == "https://overpass-api.de/api/interpreter"
    assert cfg.overpass_timeout_s == pytest.approx(20.0)
    assert cfg.overpass_buffer_m == pytest.approx(25.0)


def test_enabling_osm_enrichment_requires_sane_overpass_settings():
    with pytest.raises(ValidationError, match="overpass_timeout_s"):
        Settings(_env_file=None, osm_enrichment_enabled=True, overpass_timeout_s=0)
    with pytest.raises(ValidationError, match="overpass_max_retries"):
        Settings(_env_file=None, osm_enrichment_enabled=True, overpass_max_retries=-1)
    with pytest.raises(ValidationError, match="overpass_buffer_m"):
        Settings(_env_file=None, osm_enrichment_enabled=True, overpass_buffer_m=0)


def test_invalid_overpass_values_tolerated_while_enrichment_is_disabled():
    cfg = Settings(
        _env_file=None,
        overpass_timeout_s=0,
        overpass_max_retries=-1,
        overpass_buffer_m=0,
    )
    assert cfg.osm_enrichment_enabled is False


def test_build_surface_enricher_none_while_disabled():
    cfg = Settings(_env_file=None)
    assert build_surface_enricher(cfg) is None


def test_build_surface_enricher_wires_settings_when_enabled():
    cfg = Settings(
        _env_file=None,
        osm_enrichment_enabled=True,
        overpass_base_url="http://overpass.internal.example/interpreter",
        overpass_timeout_s=7.5,
        overpass_max_retries=2,
        overpass_buffer_m=40.0,
        overpass_cache_ttl_s=60,
    )
    enricher = build_surface_enricher(cfg)
    assert isinstance(enricher, OverpassEnricher)
    assert enricher.name == "overpass"
    assert enricher._base_url == "http://overpass.internal.example/interpreter"
    assert enricher._timeout_s == pytest.approx(7.5)
    assert enricher._max_retries == 2
    assert enricher._buffer_m == pytest.approx(40.0)


# ----------------------------------------------------------------------
# Persistence settings and storage wiring (issue #7)
# ----------------------------------------------------------------------


def test_persistence_is_off_by_default():
    cfg = Settings(_env_file=None)

    assert cfg.database_url is None
    assert cfg.artifact_backend == "local"


def test_database_artifact_backend_requires_a_database_url():
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(_env_file=None, artifact_backend="database")


def test_database_pool_size_must_be_positive():
    with pytest.raises(ValidationError, match="database_pool_max_size"):
        Settings(_env_file=None, database_url="postgresql://x/y", database_pool_max_size=0)


def test_build_storage_without_a_database_is_local_and_stateless(tmp_path):
    from bike_routing_agent.api import build_storage
    from bike_routing_agent.storage.artifacts import LocalArtifactStore

    store, history = build_storage(Settings(_env_file=None, export_dir=str(tmp_path)))

    assert isinstance(store, LocalArtifactStore)
    assert store.directory == tmp_path
    assert history is None


def test_build_storage_with_a_database_keeps_local_artifacts_by_default(tmp_path):
    pytest.importorskip("psycopg")
    from bike_routing_agent.api import build_storage
    from bike_routing_agent.storage.artifacts import LocalArtifactStore
    from bike_routing_agent.storage.postgres import PostgresRouteHistory

    # The pool is lazy: building the storage never connects.
    store, history = build_storage(
        Settings(_env_file=None, export_dir=str(tmp_path), database_url="postgresql://x/y")
    )

    assert isinstance(store, LocalArtifactStore)
    assert isinstance(history, PostgresRouteHistory)


def test_build_storage_can_move_artifacts_into_the_database():
    pytest.importorskip("psycopg")
    from bike_routing_agent.api import build_storage
    from bike_routing_agent.storage.postgres import PostgresArtifactStore, PostgresRouteHistory

    store, history = build_storage(
        Settings(_env_file=None, database_url="postgresql://x/y", artifact_backend="database")
    )

    assert isinstance(store, PostgresArtifactStore)
    assert isinstance(history, PostgresRouteHistory)


def test_alternative_dedup_threshold_default_and_validation():
    assert Settings().alternative_dedup_threshold_m == 50.0
    assert Settings(alternative_dedup_threshold_m=120).alternative_dedup_threshold_m == 120
    with pytest.raises(ValidationError, match="alternative_dedup_threshold_m"):
        Settings(alternative_dedup_threshold_m=0)


def test_surface_category_resolves_categories_and_osm_values():
    from bike_routing_agent.config import resolve_surface_tokens, surface_category

    assert surface_category("paved") == "paved"
    assert surface_category(" Asphalt ") == "paved"
    assert surface_category("paving_stones:30") == "masonry"
    assert surface_category("unpaved") is None
    categories, unknown = resolve_surface_tokens(["gravel", "asphalt", "unpaved"])
    assert categories == {"loose", "paved"}
    assert unknown == ["unpaved"]


def test_health_settings_defaults_and_validation():
    s = Settings(_env_file=None)
    assert (s.health_probe_timeout_s, s.health_cache_ttl_s, s.health_geocoder_cache_ttl_s) == (
        5.0,
        30.0,
        300.0,
    )
    with pytest.raises(ValidationError, match="health_probe_timeout_s"):
        Settings(_env_file=None, health_probe_timeout_s=0)
    with pytest.raises(ValidationError, match="health cache TTLs"):
        Settings(_env_file=None, health_cache_ttl_s=-1)


def test_s3_and_retention_settings_validation():
    s = Settings(_env_file=None)
    assert (s.artifact_backend, s.retention_max_age_days, s.s3_presigned_url_ttl_s) == (
        "local",
        None,
        None,
    )
    ok = Settings(_env_file=None, artifact_backend="s3", s3_bucket="b", retention_max_age_days=90)
    assert ok.s3_bucket == "b" and ok.retention_max_age_days == 90
    with pytest.raises(ValidationError, match="S3_BUCKET"):
        Settings(_env_file=None, artifact_backend="s3")
    with pytest.raises(ValidationError, match="retention_max_age_days"):
        Settings(_env_file=None, retention_max_age_days=0)
    with pytest.raises(ValidationError, match="retention_orphan_grace_hours"):
        Settings(_env_file=None, retention_batch_size=0)
    with pytest.raises(ValidationError, match="s3_presigned_url_ttl_s"):
        Settings(_env_file=None, s3_presigned_url_ttl_s=0)
    with pytest.raises(ValidationError, match="s3_presigned_url_ttl_s"):
        Settings(_env_file=None, s3_presigned_url_ttl_s=10**7)


def test_build_storage_selects_the_s3_backend_with_or_without_a_database():
    pytest.importorskip("botocore")
    from bike_routing_agent.api import build_storage
    from bike_routing_agent.storage.s3 import S3ArtifactStore

    cfg = Settings(
        _env_file=None,
        artifact_backend="s3",
        s3_bucket="exports",
        s3_prefix="bike",
        s3_endpoint_url="http://127.0.0.1:9",
        s3_path_style=True,
    )
    store, history = build_storage(cfg)
    assert isinstance(store, S3ArtifactStore) and history is None

    store, history = build_storage(cfg.model_copy(update={"database_url": "postgresql://x/y"}))
    assert isinstance(store, S3ArtifactStore)
    assert type(history).__name__ == "PostgresRouteHistory"


def test_auto_migrate_defaults_on_and_reaches_the_database_object():
    from bike_routing_agent.api import build_storage

    assert Settings(_env_file=None).auto_migrate is True
    cfg = Settings(_env_file=None, database_url="postgresql://x/y", auto_migrate=False)
    _, history = build_storage(cfg)
    assert history is not None and history._db._auto_migrate is False
    _, default_history = build_storage(cfg.model_copy(update={"auto_migrate": True}))
    assert default_history is not None and default_history._db._auto_migrate is True


def test_cache_settings_defaults_and_validation():
    s = Settings(_env_file=None)
    assert (s.cache_backend, s.cache_redis_url, s.cache_key_prefix, s.cache_redis_timeout_s) == (
        "memory",
        None,
        "bike-routing",
        2.0,
    )
    with pytest.raises(ValidationError, match="CACHE_REDIS_URL"):
        Settings(_env_file=None, cache_backend="redis")
    with pytest.raises(ValidationError, match="cache_redis_timeout_s"):
        Settings(_env_file=None, cache_redis_timeout_s=0)
    with pytest.raises(ValidationError, match="cache_key_prefix"):
        Settings(_env_file=None, cache_key_prefix="")


def test_build_cache_selects_the_backend():
    pytest.importorskip("redis")
    from bike_routing_agent.api import build_cache
    from bike_routing_agent.providers.base import InMemoryTTLCache
    from bike_routing_agent.providers.redis_cache import RedisCacheBackend

    assert isinstance(build_cache(Settings(_env_file=None)), InMemoryTTLCache)
    shared = build_cache(
        Settings(
            _env_file=None,
            cache_backend="redis",
            cache_redis_url="redis://127.0.0.1:6379/0",
            cache_key_prefix="bikes",
        )
    )
    assert isinstance(shared, RedisCacheBackend)
    assert shared._key("k") == "bikes:v1:k"


def test_one_shared_cache_serves_the_geocoder_and_the_enricher_under_separate_namespaces():
    from bike_routing_agent.api import build_providers, build_surface_enricher
    from bike_routing_agent.providers.base import InMemoryTTLCache, NamespacedCache

    cache = InMemoryTTLCache()
    geocoder, _ = build_providers(Settings(_env_file=None), cache=cache)
    assert isinstance(geocoder._cache, NamespacedCache)
    assert geocoder._cache._backend is cache and geocoder._cache._namespace == "geocode:"

    enriching = Settings(_env_file=None, osm_enrichment_enabled=True)
    enricher = build_surface_enricher(enriching, cache=cache)
    assert isinstance(enricher._cache, NamespacedCache)
    assert enricher._cache._backend is cache and enricher._cache._namespace == "overpass:"


def test_without_a_shared_cache_each_component_keeps_its_own_default():
    from bike_routing_agent.api import build_providers

    geocoder, _ = build_providers(Settings(_env_file=None))
    assert geocoder._cache.__class__.__name__ == "InMemoryTTLCache"


def test_weather_settings_defaults_and_validation(monkeypatch):
    monkeypatch.delenv("WEATHER_PROVIDER", raising=False)  # tests/conftest.py switches it off
    s = Settings(_env_file=None)
    assert s.weather_provider == "auto" and s.weather_max_samples == 5
    assert s.weather_user_agent.endswith("JonasHeinickeBio/pyBikeRouter")
    for bad, match in [
        ({"weather_timeout_s": 0}, "weather_timeout_s"),
        ({"weather_cache_ttl_s": -1}, "weather_cache_ttl_s"),
        ({"weather_max_samples": 1}, "weather_max_samples"),
        ({"weather_max_samples": 11}, "weather_max_samples"),
        ({"weather_sample_spacing_km": 0}, "weather_sample_spacing_km"),
        ({"weather_user_agent": "  "}, "weather_user_agent"),
    ]:
        with pytest.raises(ValidationError, match=match):
            Settings(_env_file=None, **bad)
    # an empty User-Agent only matters when MET Norway can be used
    assert Settings(_env_file=None, weather_provider="open-meteo", weather_user_agent=" ")


def test_build_weather_service_follows_the_provider_setting(monkeypatch):
    monkeypatch.delenv("WEATHER_PROVIDER", raising=False)
    from bike_routing_agent.api import build_weather_service
    from bike_routing_agent.providers.base import InMemoryTTLCache, NamespacedCache

    assert build_weather_service(Settings(_env_file=None, weather_provider="none")) is None
    names = {
        "auto": ["dwd", "open-meteo", "met-no"],
        "dwd": ["dwd"],
        "open-meteo": ["open-meteo"],
        "met-no": ["met-no"],
    }
    for setting, expected in names.items():
        service = build_weather_service(Settings(_env_file=None, weather_provider=setting))
        assert service is not None and service.provider_names == expected

    assert build_weather_service(Settings(_env_file=None))._merge is True
    assert build_weather_service(Settings(_env_file=None, weather_merge=False))._merge is False

    cache = InMemoryTTLCache()
    shared = build_weather_service(Settings(_env_file=None), cache=cache)
    assert isinstance(shared._cache, NamespacedCache) and shared._cache._namespace == "weather:"
    assert shared._cache_ttl_s == 1800.0


def test_the_met_no_provider_gets_the_configured_user_agent_and_urls():
    from bike_routing_agent.api import build_weather_service

    service = build_weather_service(
        Settings(
            _env_file=None,
            weather_provider="met-no",
            weather_user_agent="my-app/2 me@example.org",
            weather_met_no_url="https://met.example/x",
        )
    )
    provider = service._providers[0]
    assert provider._user_agent == "my-app/2 me@example.org"
    assert provider._base_url == "https://met.example/x"


def test_llm_settings_defaults_and_validation():
    s = Settings(_env_file=None)
    assert (s.llm_parser_enabled, s.llm_model, s.anthropic_api_key) == (False, None, None)
    for bad, match in [
        ({"llm_parser_enabled": True}, "LLM_MODEL"),
        ({"llm_parser_enabled": True, "llm_model": "  "}, "LLM_MODEL"),
        ({"llm_timeout_s": 0}, "llm_timeout_s"),
        ({"llm_max_output_tokens": 10}, "llm_max_output_tokens"),
        ({"llm_max_output_tokens": 10**6}, "llm_max_output_tokens"),
    ]:
        with pytest.raises(ValidationError, match=match):
            Settings(_env_file=None, **bad)
    ok = Settings(_env_file=None, llm_parser_enabled=True, llm_model="claude-test")
    assert ok.llm_model == "claude-test"


def test_the_api_key_never_appears_in_the_settings_repr():
    s = Settings(_env_file=None, anthropic_api_key="sk-ant-very-secret")
    assert "very-secret" not in repr(s) and "very-secret" not in str(s.model_dump())
    assert s.anthropic_api_key.get_secret_value() == "sk-ant-very-secret"


def test_build_llm_parser_follows_the_setting():
    pytest.importorskip("anthropic", reason="the llm extra is not installed")
    from bike_routing_agent.api import build_llm_parser
    from bike_routing_agent.llm.parser import RouteRequestParser

    assert build_llm_parser(Settings(_env_file=None)) is None
    parser = build_llm_parser(
        Settings(
            _env_file=None,
            llm_parser_enabled=True,
            llm_model="claude-test",
            anthropic_api_key="sk-ant-test",
            llm_max_output_tokens=4000,
        )
    )
    assert isinstance(parser, RouteRequestParser)
    assert parser._model == "claude-test" and parser._backend._max_output_tokens == 4000
    assert parser._backend._client.api_key == "sk-ant-test"


def test_openai_compatible_provider_needs_a_base_url():
    base = {"_env_file": None, "llm_parser_enabled": True, "llm_model": "m"}
    assert Settings(**base).llm_provider == "anthropic"
    for bad in (None, "  ", "ftp://x"):
        with pytest.raises(ValueError, match="LLM_BASE_URL|http"):
            Settings(**base, llm_provider="openai", llm_base_url=bad)
    ok = Settings(**base, llm_provider="openai", llm_base_url="http://localhost:11434/v1")
    assert ok.llm_api_key is None  # local servers need no key
    # not enabled: nothing to validate
    Settings(_env_file=None, llm_provider="openai")


def test_build_llm_parser_uses_the_openai_backend_and_hides_its_key():
    from bike_routing_agent.api import build_llm_parser
    from bike_routing_agent.llm.backends import OpenAICompatBackend

    cfg = Settings(
        _env_file=None,
        llm_parser_enabled=True,
        llm_model="alias-large",
        llm_provider="openai",
        llm_base_url="https://llm.example/v1/",
        llm_api_key="sk-secret-key",
    )
    parser = build_llm_parser(cfg)
    assert isinstance(parser._backend, OpenAICompatBackend)
    assert parser._backend._url == "https://llm.example/v1/chat/completions"
    assert "sk-secret-key" not in repr(cfg)
