"""openrouteservice routing adapter."""

from __future__ import annotations

import asyncio
import json

import httpx

from bike_routing_agent.config import ORS_PROFILE_MAP
from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderNoRouteError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.models import RouteCandidate, RouteMetrics, RoutingRequest

# ORS error codes that mean "no path could be found between the given
# points", as opposed to a malformed request or transient failure.
_ORS_NO_ROUTE_CODES = {2010, 2009}

# avoid_features accepted by ORS's cycling-* profiles. "highways" and
# "tollways" are only valid for driving profiles -- sending them for a
# cycling profile is a hard 400 from ORS, not a soft no-op. Every profile
# we currently route through is a cycling profile (see ORS_PROFILE_MAP).
_CYCLING_AVOID_FEATURES = {"ferries", "fords", "steps"}


class OpenRouteServiceAdapter:
    """RoutingProvider backed by the openrouteservice directions API."""

    name = "ors"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_s: float,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._client = client

    def _profile_for(self, bike_type: str) -> str:
        try:
            return ORS_PROFILE_MAP[bike_type]
        except KeyError as exc:
            raise ProviderBadResponseError(
                f"no ORS profile mapped for bike type '{bike_type}'", provider=self.name
            ) from exc

    def _build_body(self, request: RoutingRequest) -> tuple[dict, list[str]]:
        coordinates = [[request.origin.lon, request.origin.lat]]
        coordinates.extend([v.lon, v.lat] for v in request.via)
        coordinates.append([request.destination.lon, request.destination.lat])

        requested_avoid_features = []
        if request.constraints.avoid_high_traffic_roads:
            requested_avoid_features.append("highways")
        if request.constraints.avoid_ferries:
            requested_avoid_features.append("ferries")

        avoid_features = [f for f in requested_avoid_features if f in _CYCLING_AVOID_FEATURES]
        unsupported = [f for f in requested_avoid_features if f not in _CYCLING_AVOID_FEATURES]
        warnings = []
        if unsupported:
            warnings.append(
                f"requested avoid_features {unsupported} are not supported by ORS cycling "
                "profiles and were not applied"
            )

        body: dict = {
            "coordinates": coordinates,
            "elevation": True,
            "instructions": False,
        }
        if avoid_features:
            body["options"] = {"avoid_features": avoid_features}
        return body, warnings

    async def route(self, request: RoutingRequest) -> RouteCandidate:
        profile = self._profile_for(request.constraints.bike_type.value)
        body, build_warnings = self._build_body(request)
        url = f"{self._base_url}/v2/directions/{profile}/geojson"
        headers = {
            "Authorization": self._api_key,
            "Content-Type": "application/json",
        }

        payload = await self._post_with_retry(url, body, headers)
        candidate = self._normalize(payload, profile=profile)
        if build_warnings:
            candidate = candidate.model_copy(
                update={"warnings": [*build_warnings, *candidate.warnings]}
            )
        return candidate

    async def _post_with_retry(self, url: str, body: dict, headers: dict) -> dict:
        attempt = 0
        while True:
            try:
                if self._client is not None:
                    response = await self._client.post(
                        url, json=body, headers=headers, timeout=self._timeout_s
                    )
                else:
                    async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                        response = await client.post(url, json=body, headers=headers)
            except httpx.TimeoutException as exc:
                if attempt >= self._max_retries:
                    raise ProviderTimeoutError(
                        "ORS request timed out",
                        provider=self.name,
                        detail={"attempts": attempt + 1},
                    ) from exc
                attempt += 1
                await asyncio.sleep(0.5 * attempt)
                continue
            except httpx.HTTPError as exc:
                raise ProviderUnavailableError(
                    "ORS request failed", provider=self.name, detail={"error": str(exc)}
                ) from exc

            if response.status_code == 429:
                raise ProviderRateLimitError(
                    "ORS rate limit exceeded",
                    provider=self.name,
                    detail={"retry_after": response.headers.get("Retry-After")},
                )
            if response.status_code >= 500:
                if attempt >= self._max_retries:
                    raise ProviderUnavailableError(
                        f"ORS returned HTTP {response.status_code}",
                        provider=self.name,
                        detail={"status_code": response.status_code},
                    )
                attempt += 1
                await asyncio.sleep(0.5 * attempt)
                continue
            if response.status_code >= 400:
                return self._raise_for_client_error(response)

            try:
                return response.json()
            except json.JSONDecodeError as exc:
                raise ProviderBadResponseError(
                    "ORS returned invalid JSON", provider=self.name
                ) from exc

    def _raise_for_client_error(self, response: httpx.Response) -> dict:
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {}
        error_code = None
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            error_code = body["error"].get("code")
        if error_code in _ORS_NO_ROUTE_CODES:
            raise ProviderNoRouteError(
                "ORS could not find a route between the given points",
                provider=self.name,
                detail={"status_code": response.status_code, "ors_code": error_code},
            )
        raise ProviderBadResponseError(
            f"ORS rejected the request (HTTP {response.status_code})",
            provider=self.name,
            detail={"status_code": response.status_code, "body": body},
        )

    def _normalize(self, payload: dict, *, profile: str) -> RouteCandidate:
        try:
            features = payload["features"]
        except (KeyError, TypeError) as exc:
            raise ProviderBadResponseError(
                "malformed ORS directions response",
                provider=self.name,
                detail={"payload_keys": list(payload)},
            ) from exc

        if not features:
            raise ProviderNoRouteError("ORS returned no route features", provider=self.name)

        try:
            feature = features[0]
            geometry = feature["geometry"]
            summary = feature["properties"]["summary"]
            distance_m = float(summary["distance"])
            duration_s = float(summary["duration"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderBadResponseError(
                "malformed ORS directions response",
                provider=self.name,
                detail={"payload_keys": list(payload)},
            ) from exc

        properties = feature["properties"]
        ascent_m = properties.get("ascent")
        descent_m = properties.get("descent")

        warnings: list[str] = []
        for extra_key, extra_value in (properties.get("extras") or {}).items():
            summary_entries = extra_value.get("summary") if isinstance(extra_value, dict) else None
            if summary_entries:
                warnings.append(f"route includes varying {extra_key.replace('_', ' ')}")
        for warning in properties.get("warnings") or []:
            message = warning.get("message") if isinstance(warning, dict) else str(warning)
            if message:
                warnings.append(message)

        metrics = RouteMetrics(
            distance_m=distance_m,
            duration_s=duration_s,
            ascent_m=float(ascent_m) if ascent_m is not None else None,
            descent_m=float(descent_m) if descent_m is not None else None,
        )

        return RouteCandidate(
            provider=self.name,
            provider_profile=profile,
            geometry_geojson=geometry,
            metrics=metrics,
            warnings=warnings,
            provenance={"provider": self.name, "profile": profile},
            raw_provider_response=payload,
        )

    async def health(self) -> dict:
        """Note: /v2/health is exposed by self-hosted ORS backends but not by
        the public multi-tenant api.openrouteservice.org, which 404s here.
        That 404 is reported as "unknown", not "degraded" -- a permanently
        absent endpoint is not evidence the service itself is unhealthy."""
        url = f"{self._base_url}/v2/health"
        try:
            if self._client is not None:
                response = await self._client.get(url, timeout=self._timeout_s)
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.get(url)
        except httpx.HTTPError as exc:
            return {"status": "unavailable", "error": str(exc)}

        if response.status_code == 200:
            return {"status": "ok"}
        if response.status_code == 404:
            return {"status": "unknown", "detail": f"{url} not found on this ORS deployment"}
        return {"status": "degraded", "status_code": response.status_code}
