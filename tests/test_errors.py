"""Structured error contract tests: graph nodes serialize these dicts, so
their shape is API-visible and must stay stable."""

from bike_routing_agent.errors import (
    GeocodingAmbiguousError,
    GeocodingError,
    GeocodingNotFoundError,
    ProviderBadResponseError,
    ProviderError,
    ProviderNoRouteError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def test_provider_error_to_dict_has_full_shape():
    error = ProviderError("boom", provider="ors", detail={"status_code": 500})

    assert error.to_dict() == {
        "code": "provider_error",
        "provider": "ors",
        "message": "boom",
        "detail": {"status_code": 500},
    }


def test_provider_error_detail_defaults_to_empty_dict():
    assert ProviderError("boom", provider="ors").to_dict()["detail"] == {}


def test_provider_error_code_per_class():
    codes = {
        ProviderTimeoutError: "provider_timeout",
        ProviderRateLimitError: "provider_rate_limited",
        ProviderUnavailableError: "provider_unavailable",
        ProviderBadResponseError: "provider_bad_response",
        ProviderNoRouteError: "no_route",
    }
    for cls, code in codes.items():
        assert cls("x", provider="p").to_dict()["code"] == code


def test_geocoding_error_to_dict_uses_query_not_provider():
    error = GeocodingNotFoundError("no results", query="Springfield")

    assert error.to_dict() == {
        "code": "geocoding_not_found",
        "query": "Springfield",
        "message": "no results",
        "detail": {},
    }


def test_geocoding_ambiguous_error_carries_candidates():
    candidates = [{"label": "Springfield, IL"}, {"label": "Springfield, MO"}]
    error = GeocodingAmbiguousError("too many matches", query="Springfield", candidates=candidates)

    assert isinstance(error, GeocodingError)
    assert error.to_dict()["code"] == "geocoding_ambiguous"
    assert error.candidates == candidates


def test_no_route_is_a_provider_error_subclass():
    # The route node relies on this ordering: NoRoute checked before generic.
    assert issubclass(ProviderNoRouteError, ProviderError)
