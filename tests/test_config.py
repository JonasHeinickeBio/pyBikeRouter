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
        Settings(
            _env_file=None, database_url="postgresql://x/y", database_pool_max_size=0
        )


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
