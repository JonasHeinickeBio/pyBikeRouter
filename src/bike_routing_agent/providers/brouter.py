"""BRouter routing adapter for a self-hosted BRouter RouteServer.

Talks to the plain HTTP server started by ``docker compose --profile
brouter up`` (or any stock ``abrensch/brouter`` deployment):

    GET /brouter?lonlats=lon,lat|lon,lat&profile=<name>&format=geojson

BRouter quirks this adapter absorbs:

- Error bodies are plain text, never JSON: routing failures answer 400,
  server-side problems (e.g. the ``maxRunningTime`` watchdog) answer 500.
- The GeoJSON summary values are strings (``"track-length": "12450"``).
- No descent figure is reported, so ``RouteMetrics.descent_m`` stays None.
- There is no health endpoint; ``GET /robots.txt`` is the only request
  that answers 200 without touching the routing engine.

Custom profiles are versioned in this repository (``docker/brouter/
profiles``), referenced through the ``custom_`` prefix in
``BROUTER_PROFILE_MAP``.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from bike_routing_agent.config import BROUTER_PROFILE_MAP
from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderNoRouteError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.models import RouteCandidate, RouteMetrics, RoutingRequest

# Fragments BRouter puts in its plain-text 400 body when the request was
# well-formed but no path exists (as opposed to a bad profile/coordinates).
_NO_ROUTE_MARKERS = ("not reachable", "no route", "no track")

_MAX_ERROR_BODY_CHARS = 500


class BRouterAdapter:
    """RoutingProvider backed by a local/self-hosted BRouter RouteServer."""

    name = "brouter"

    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float = 10.0,
        max_retries: int = 0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._client = client

    def _profile_for(self, bike_type: str) -> str:
        try:
            return BROUTER_PROFILE_MAP[bike_type]
        except KeyError as exc:
            raise ProviderBadResponseError(
                f"no BRouter profile mapped for bike type '{bike_type}'", provider=self.name
            ) from exc

    @staticmethod
    def _lonlats(request: RoutingRequest) -> str:
        points = [request.origin, *request.via, request.destination]
        return "|".join(f"{p.lon:.7f},{p.lat:.7f}" for p in points)

    def _build_warnings(self, request: RoutingRequest, profile: str) -> list[str]:
        warnings: list[str] = []
        surfaces = [*request.constraints.prefer_surfaces, *request.constraints.avoid_surfaces]
        if surfaces:
            warnings.append(
                "surface preferences are baked into the versioned BRouter profile and "
                f"cannot be applied per request ({surfaces} were not applied)"
            )
        if not request.constraints.avoid_ferries:
            warnings.append(
                "avoid_ferries=False is not supported per request; the mapped BRouter "
                "profile decides ferry handling"
            )
        elif profile == "custom_gravel-v1":
            # Stock gravel only penalises ferry segments (initialcost 20000),
            # it never forbids them; custom_touring-v1 sets allow_ferries=false
            # and stock profile behaviour is not inferred.
            warnings.append(
                "avoid_ferries=True is not enforced by custom_gravel-v1: the profile "
                "penalises ferry segments but may still route over them"
            )
        if request.constraints.avoid_high_traffic_roads and profile in (
            "custom_gravel-v1",
            "custom_touring-v1",
        ):
            warnings.append(
                f"avoid_high_traffic_roads=True is not applied per request by {profile}: "
                "its traffic-estimate switch is off by default, so traffic avoidance is "
                "only approximated by the profile's static cost structure"
            )
        return warnings

    async def _get(self, params: dict[str, str]) -> httpx.Response:
        attempt = 0
        while True:
            try:
                if self._client is not None:
                    response = await self._client.get(
                        f"{self._base_url}/brouter", params=params, timeout=self._timeout_s
                    )
                else:
                    async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                        response = await client.get(f"{self._base_url}/brouter", params=params)
            except httpx.TimeoutException as exc:
                if attempt >= self._max_retries:
                    raise ProviderTimeoutError(
                        "BRouter request timed out",
                        provider=self.name,
                        detail={"attempts": attempt + 1},
                    ) from exc
                attempt += 1
                await asyncio.sleep(0.5 * attempt)
                continue
            except httpx.HTTPError as exc:
                raise ProviderUnavailableError(
                    "BRouter request failed", provider=self.name, detail={"error": str(exc)}
                ) from exc

            if response.status_code >= 500:
                if attempt >= self._max_retries:
                    raise ProviderUnavailableError(
                        f"BRouter returned HTTP {response.status_code}",
                        provider=self.name,
                        detail={
                            "status_code": response.status_code,
                            "body": response.text[:_MAX_ERROR_BODY_CHARS],
                        },
                    )
                attempt += 1
                await asyncio.sleep(0.5 * attempt)
                continue
            return response

    async def route(self, request: RoutingRequest) -> RouteCandidate:
        profile = self._profile_for(request.constraints.bike_type.value)
        response = await self._get(
            {
                "lonlats": self._lonlats(request),
                "profile": profile,
                "alternativeidx": "0",
                "format": "geojson",
            }
        )

        if response.status_code == 400:
            self._raise_for_routing_error(response)
        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"BRouter rejected the request (HTTP {response.status_code})",
                provider=self.name,
                detail={
                    "status_code": response.status_code,
                    "body": response.text[:_MAX_ERROR_BODY_CHARS],
                },
            )

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderBadResponseError(
                "BRouter returned invalid JSON",
                provider=self.name,
                detail={"body": response.text[:_MAX_ERROR_BODY_CHARS]},
            ) from exc

        candidate = self._normalize(payload, profile=profile)
        build_warnings = self._build_warnings(request, profile)
        if build_warnings:
            candidate = candidate.model_copy(
                update={"warnings": [*build_warnings, *candidate.warnings]}
            )
        return candidate

    @staticmethod
    def _raise_for_routing_error(response: httpx.Response) -> None:
        body = response.text[:_MAX_ERROR_BODY_CHARS]
        if any(marker in body.lower() for marker in _NO_ROUTE_MARKERS):
            raise ProviderNoRouteError(
                "BRouter could not find a route between the given points",
                provider="brouter",
                detail={"status_code": response.status_code, "body": body},
            )
        raise ProviderBadResponseError(
            "BRouter rejected the request (HTTP 400)",
            provider="brouter",
            detail={"status_code": response.status_code, "body": body},
        )

    def _normalize(self, payload: object, *, profile: str) -> RouteCandidate:
        if not isinstance(payload, dict):
            raise ProviderBadResponseError(
                "BRouter returned a non-JSON payload", provider=self.name
            )

        features = payload.get("features")
        if features is None and payload.get("type") == "Feature":
            features = [payload]
        if not isinstance(features, list):
            raise ProviderBadResponseError(
                "malformed BRouter directions response",
                provider=self.name,
                detail={"payload_keys": list(payload)},
            )

        track = next(
            (
                feature
                for feature in features
                if isinstance(feature, dict)
                and isinstance(feature.get("geometry"), dict)
                and feature["geometry"].get("type") == "LineString"
            ),
            None,
        )
        if track is None:
            raise ProviderNoRouteError("BRouter returned no route geometry", provider=self.name)

        properties = track.get("properties") or {}
        distance_m = _as_float(properties.get("track-length"))
        if distance_m is None:
            raise ProviderBadResponseError(
                "BRouter route summary is missing 'track-length'",
                provider=self.name,
                detail={"property_keys": list(properties)},
            )

        metrics = RouteMetrics(
            distance_m=distance_m,
            duration_s=_as_float(properties.get("total-time")),
            ascent_m=_as_float(properties.get("filtered ascend")),
            # BRouter reports no descent; leave it None rather than guess.
        )

        return RouteCandidate(
            provider=self.name,
            provider_profile=profile,
            geometry_geojson=track["geometry"],
            metrics=metrics,
            provenance={"provider": self.name, "profile": profile},
            raw_provider_response=payload,
        )

    async def health(self) -> dict:
        """Liveness probe against ``GET /robots.txt``.

        The BRouter RouteServer has no health endpoint (everything except
        /brouter and /robots.txt answers 404); /robots.txt is served with
        200 by the request handler itself, without loading segments or a
        profile, which makes it the only usable liveness signal.
        """
        try:
            if self._client is not None:
                response = await self._client.get(
                    f"{self._base_url}/robots.txt", timeout=self._timeout_s
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.get(f"{self._base_url}/robots.txt")
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            return {"status": "unavailable", "error": str(exc)}

        if response.status_code == 200:
            return {"status": "ok"}
        return {"status": "degraded", "status_code": response.status_code}


def _as_float(value: object) -> float | None:
    """BRouter summary values arrive as strings ("12450"); None when absent
    or unparsable."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
