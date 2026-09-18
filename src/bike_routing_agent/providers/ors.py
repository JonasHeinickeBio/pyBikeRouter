"""openrouteservice routing adapter."""

from __future__ import annotations

import httpx

from bike_routing_agent.config import ORS_PROFILE_MAP
from bike_routing_agent.errors import ProviderBadResponseError, ProviderNoRouteError
from bike_routing_agent.models import RouteCandidate, RouteMetrics, RoutingRequest
from bike_routing_agent.providers.ors_client import OpenRouteServiceClient

# avoid_features accepted by ORS's cycling-* profiles. "highways" and
# "tollways" are only valid for driving profiles -- sending them for a
# cycling profile is a hard 400 from ORS, not a soft no-op. Every profile
# we currently route through is a cycling profile (see ORS_PROFILE_MAP).
_CYCLING_AVOID_FEATURES = {"ferries", "fords", "steps"}


class OpenRouteServiceAdapter:
    """RoutingProvider backed by the openrouteservice directions API.

    Built on :class:`OpenRouteServiceClient`, which exposes the full ORS
    API surface (isochrones, matrix, snapping, export, elevation, POIs,
    Vroom, Pelias geocoding). Only the directions call is needed here.
    """

    name = "ors"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_s: float,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
        ors_client: OpenRouteServiceClient | None = None,
    ) -> None:
        if ors_client is None:
            ors_client = OpenRouteServiceClient(
                api_key=api_key,
                base_url=base_url,
                timeout_s=timeout_s,
                max_retries=max_retries,
                http_client=client,
            )
        self._ors = ors_client

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

        payload = await self._ors.directions(profile, body=body, format="geojson")
        if not isinstance(payload, dict):
            raise ProviderBadResponseError(
                "ORS directions endpoint returned a non-JSON payload",
                provider=self.name,
            )
        candidate = self._normalize(payload, profile=profile)
        if build_warnings:
            candidate = candidate.model_copy(
                update={"warnings": [*build_warnings, *candidate.warnings]}
            )
        return candidate

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
        return await self._ors.health()
