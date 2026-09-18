"""Settings validation and geocoder-provider wiring tests."""

import pytest
from pydantic import ValidationError

from bike_routing_agent.api import build_providers
from bike_routing_agent.config import BROUTER_PROFILE_MAP, ORS_PROFILE_MAP, Settings
from bike_routing_agent.models import BikeType
from bike_routing_agent.providers.brouter import BRouterAdapter
from bike_routing_agent.providers.geocoder import NominatimGeocoder
from bike_routing_agent.providers.pelias import PeliasGeocoder

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


def test_brouter_settings_unchecked_for_other_providers():
    settings = Settings(
        _env_file=None,
        routing_provider="ors",
        brouter_timeout_s=0,
        brouter_max_retries=-1,
    )
    assert settings.routing_provider == "ors"


# ----------------------------------------------------------------------
# Profile maps
# ----------------------------------------------------------------------


def test_profile_maps_cover_every_bike_type():
    values = {bike_type.value for bike_type in BikeType}
    assert set(ORS_PROFILE_MAP) == values
    assert set(BROUTER_PROFILE_MAP) == values


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
    geocoder, router = build_providers(cfg)
    assert isinstance(geocoder, NominatimGeocoder)
    assert geocoder.name == "nominatim"
    assert router.name == "ors"


def test_build_providers_pelias_shares_ors_client():
    cfg = Settings(
        _env_file=None,
        geocoder_provider="pelias",
        ors_base_url="https://ors.internal.example.org",
        ors_api_key="secret",
    )
    geocoder, router = build_providers(cfg)
    assert isinstance(geocoder, PeliasGeocoder)
    assert geocoder.name == "pelias"
    assert router.name == "ors"
    # The routing adapter and the geocoder must share one ORS client.
    assert router._ors is geocoder._client


def test_build_providers_brouter_selects_brouter_adapter():
    cfg = Settings(_env_file=None, routing_provider="brouter")
    geocoder, router = build_providers(cfg)
    assert isinstance(geocoder, NominatimGeocoder)
    assert isinstance(router, BRouterAdapter)
    assert router.name == "brouter"


def test_build_providers_pelias_with_brouter_keeps_ors_client_for_geocoder_only():
    cfg = Settings(
        _env_file=None,
        geocoder_provider="pelias",
        ors_base_url="https://ors.internal.example.org",
        ors_api_key="secret",
        routing_provider="brouter",
    )
    geocoder, router = build_providers(cfg)
    assert isinstance(geocoder, PeliasGeocoder)
    assert isinstance(router, BRouterAdapter)
    assert router.name == "brouter"
