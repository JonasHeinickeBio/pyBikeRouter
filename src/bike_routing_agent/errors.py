"""Structured errors for provider and geocoding failures.

These are raised by adapters and caught by graph nodes -- they must never
surface to callers as uncaught exceptions.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base class for routing/geocoding provider failures."""

    code = "provider_error"

    def __init__(self, message: str, *, provider: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "provider": self.provider,
            "message": self.message,
            "detail": self.detail,
        }


class ProviderTimeoutError(ProviderError):
    code = "provider_timeout"


class ProviderRateLimitError(ProviderError):
    code = "provider_rate_limited"


class ProviderUnavailableError(ProviderError):
    code = "provider_unavailable"


class ProviderBadResponseError(ProviderError):
    code = "provider_bad_response"


class ProviderNoRouteError(ProviderError):
    code = "no_route"


class GeocodingError(Exception):
    """Base class for geocoding failures."""

    code = "geocoding_error"

    def __init__(self, message: str, *, query: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.query = query
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "query": self.query,
            "message": self.message,
            "detail": self.detail,
        }


class GeocodingNotFoundError(GeocodingError):
    code = "geocoding_not_found"


class GeocodingAmbiguousError(GeocodingError):
    """Raised only by callers that want a hard failure; the geocode node
    normally treats ambiguity as a clarification requirement, not an
    exception."""

    code = "geocoding_ambiguous"

    def __init__(
        self, message: str, *, query: str, candidates: list[dict], detail: dict | None = None
    ) -> None:
        super().__init__(message, query=query, detail=detail)
        self.candidates = candidates
