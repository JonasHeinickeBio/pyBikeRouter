"""Valhalla routing adapter for a self-hosted Valhalla service.

Talks to the HTTP meili server (``POST /route``) of any stock Valhalla
deployment (``valhalla/valhalla`` image, valhalla-service package, ...):

    POST /route  {"locations":[{"lon":..,"lat":..}], "costing":"bicycle", ...}

Valhalla quirks this adapter absorbs:

- Route geometry is a *polyline6 string* (Google polyline algorithm at
  1e-6 precision) carried as plain ASCII in each leg's ``shape`` field,
  not GeoJSON coordinates.
- ``trip.summary.length`` is in the response units (kilometers by
  default here), while the domain model wants meters.
- Elevation is only returned when ``elevation_interval`` is requested and
  the tiles were built with elevation data; ascent/descent are derived from
  the per-leg ``elevation`` arrays and stay ``None`` otherwise.
- Routing failures are 4xx responses with a JSON ``status_message``
  ("No path could be found for input" and friends).
- The bicycle costing has no per-bike-type option in current Valhalla, so
  every internal bike type maps to ``costing: "bicycle"``; bike-type
  differentiation is left to the other engines and the scoring step.
"""

from __future__ import annotations

import asyncio
import json
import struct

import httpx

from bike_routing_agent.config import VALHALLA_PROFILE_MAP
from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderNoRouteError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.models import RouteCandidate, RouteMetrics, RoutingRequest

# Fragments Valhalla puts in its JSON ``status_message`` when the request
# was well-formed but no path exists (HTTP 400 for meili/odin errors,
# HTTP 442/499 for thor-internal codes).
_NO_ROUTE_MARKERS = (
    "no path could be found",
    "location is unreachable",
    "no suitable edges near location",
    "no data found for location",
    "unconnected regions",
    "cannot reach destination",
)

_MAX_ERROR_BODY_CHARS = 500


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _error_message_from_body(body: str) -> str | None:
    """Extract the message from a Valhalla JSON error body.

    meili's HTTP errors carry ``{"statusCode": .., "status": .., "message":
    ".."}``; older/loki-style bodies may use ``status_message`` instead.
    """
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    for key in ("message", "status_message", "status"):
        value = parsed.get(key)
        if isinstance(value, str) and value:
            return value
    return None

# 30 m matches the resolution of Valhalla's default elevation data source.
_ELEVATION_INTERVAL_M = 30


class ValhallaAdapter:
    """RoutingProvider backed by a self-hosted Valhalla meili server."""

    name = "valhalla"

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

    def _costing_for(self, bike_type: str) -> str:
        try:
            return VALHALLA_PROFILE_MAP[bike_type]
        except KeyError as exc:
            raise ProviderBadResponseError(
                f"no Valhalla costing mapped for bike type '{bike_type}'", provider=self.name
            ) from exc

    def _build_payload(self, request: RoutingRequest) -> dict:
        costing = self._costing_for(request.constraints.bike_type.value)
        options: dict[str, float] = {}
        if request.constraints.avoid_ferries:
            options["use_ferry"] = 0.0
        if request.constraints.avoid_high_traffic_roads:
            options["use_highways"] = 0.0
        points = [request.origin, *request.via, request.destination]
        return {
            "locations": [{"lon": p.lon, "lat": p.lat} for p in points],
            "costing": costing,
            "costing_options": {costing: options},
            "units": "kilometers",
            "elevation_interval": _ELEVATION_INTERVAL_M,
        }

    def _build_warnings(self, request: RoutingRequest) -> list[str]:
        constraints = request.constraints
        warnings: list[str] = []
        surfaces = [*constraints.prefer_surfaces, *constraints.avoid_surfaces]
        if surfaces:
            warnings.append(
                "surface preferences are not applied per request by Valhalla bicycle "
                f"costing ({surfaces} were not applied)"
            )
        if constraints.bike_type.value == "ebike":
            warnings.append(
                "Valhalla has no e-assist costing: ebike rides use the standard "
                "bicycle costing without motor assist terms"
            )
        if not constraints.avoid_ferries:
            warnings.append(
                "avoid_ferries=False keeps Valhalla's default ferry willingness (0.5); "
                "ferries may appear on the route"
            )
        return warnings

    async def _post(self, payload: dict) -> httpx.Response:
        attempt = 0
        while True:
            try:
                if self._client is not None:
                    response = await self._client.post(
                        f"{self._base_url}/route", json=payload, timeout=self._timeout_s
                    )
                else:
                    async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                        response = await client.post(f"{self._base_url}/route", json=payload)
            except httpx.TimeoutException as exc:
                if attempt >= self._max_retries:
                    raise ProviderTimeoutError(
                        "Valhalla request timed out",
                        provider=self.name,
                        detail={"attempts": attempt + 1},
                    ) from exc
                attempt += 1
                await asyncio.sleep(0.5 * attempt)
                continue
            except httpx.HTTPError as exc:
                raise ProviderUnavailableError(
                    "Valhalla request failed", provider=self.name, detail={"error": str(exc)}
                ) from exc

            if response.status_code >= 500:
                if attempt >= self._max_retries:
                    raise ProviderUnavailableError(
                        f"Valhalla returned HTTP {response.status_code}",
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
        costing = self._costing_for(request.constraints.bike_type.value)
        response = await self._post(self._build_payload(request))

        if response.status_code == 429:
            raise ProviderRateLimitError(
                "Valhalla rate limited the request",
                provider=self.name,
                detail={"status_code": 429},
            )
        if response.status_code >= 400:
            self._raise_for_error(response)

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderBadResponseError(
                "Valhalla returned invalid JSON",
                provider=self.name,
                detail={"body": response.text[:_MAX_ERROR_BODY_CHARS]},
            ) from exc

        candidate = self._normalize(payload, costing=costing)
        request_warnings = self._build_warnings(request)
        if request_warnings:
            candidate = candidate.model_copy(
                update={"warnings": [*request_warnings, *candidate.warnings]}
            )
        return candidate

    @staticmethod
    def _raise_for_error(response: httpx.Response) -> None:
        body = response.text[:_MAX_ERROR_BODY_CHARS]
        ValhallaAdapter._raise_for_status_message(
            response.status_code, body, _error_message_from_body(body)
        )

    @staticmethod
    def _raise_for_status_message(
        status_code: int, body: str, message: str | None = None
    ) -> None:
        haystack = f"{message or ''} {body}".lower()
        if any(marker in haystack for marker in _NO_ROUTE_MARKERS):
            raise ProviderNoRouteError(
                "Valhalla could not find a route between the given points",
                provider="valhalla",
                detail={"status_code": status_code, "body": body},
            )
        raise ProviderBadResponseError(
            f"Valhalla rejected the request (HTTP {status_code})",
            provider="valhalla",
            detail={"status_code": status_code, "body": body},
        )

    def _normalize(self, payload: object, *, costing: str) -> RouteCandidate:
        if not isinstance(payload, dict) or not isinstance(payload.get("trip"), dict):
            raise ProviderBadResponseError(
                "malformed Valhalla route response",
                provider=self.name,
                detail={"payload_keys": list(payload) if isinstance(payload, dict) else None},
            )
        trip = payload["trip"]

        # HTTP 200 with a non-zero trip status is the legacy/meili error
        # channel ("No path could be found for input" and friends).
        status = trip.get("status")
        if isinstance(status, int) and status != 0:
            status_message = trip.get("status_message")
            self._raise_for_status_message(
                status,
                json.dumps({"trip_status": status, "status_message": status_message}),
                status_message if isinstance(status_message, str) else None,
            )

        summary = trip.get("summary")
        if not isinstance(summary, dict):
            raise ProviderBadResponseError(
                "Valhalla trip is missing 'summary'",
                provider=self.name,
                detail={"trip_keys": list(trip)},
            )

        length_km = _as_float(summary.get("length"))
        if length_km is None:
            raise ProviderBadResponseError(
                "Valhalla trip summary is missing 'length'",
                provider=self.name,
                detail={"summary_keys": list(summary)},
            )

        coordinates = self._decode_geometry(trip)
        ascent_m, descent_m = self._elevation_totals(trip)

        warnings = [
            warning["text"]
            for warning in trip.get("warnings", [])
            if isinstance(warning, dict) and isinstance(warning.get("text"), str)
        ]
        if summary.get("has_ferry"):
            warnings.append("route includes ferry segments (Valhalla reported has_ferry=true)")
        if summary.get("has_highway"):
            warnings.append("route includes highway segments (Valhalla reported has_highway=true)")

        return RouteCandidate(
            provider=self.name,
            provider_profile=costing,
            geometry_geojson={"type": "LineString", "coordinates": coordinates},
            metrics=RouteMetrics(
                distance_m=length_km * 1000.0,
                duration_s=_as_float(summary.get("time")),
                ascent_m=ascent_m,
                descent_m=descent_m,
            ),
            provenance={"provider": self.name, "profile": costing},
            warnings=warnings,
            raw_provider_response=payload,
        )

    def _decode_geometry(self, trip: dict) -> list[list[float]]:
        """Concatenate per-leg polyline6 shapes into one LineString.

        Stock Valhalla carries a direct polyline6 string in each
        ``trip.legs[].shape``; concatenating the legs (minus the shared
        junction point) reproduces the whole-trip geometry.
        """
        legs = trip.get("legs")
        if not isinstance(legs, list) or not legs:
            raise ProviderNoRouteError("Valhalla returned no route legs", provider=self.name)

        coordinates: list[list[float]] = []
        for leg in legs:
            if not isinstance(leg, dict):
                raise ProviderBadResponseError(
                    "malformed Valhalla trip leg", provider=self.name
                )
            encoded = leg.get("shape")
            if not isinstance(encoded, str):
                raise ProviderBadResponseError(
                    "Valhalla leg is missing an encoded shape",
                    provider=self.name,
                    detail={"leg_keys": list(leg)},
                )
            leg_coordinates = decode_polyline6(encoded, provider=self.name)
            if coordinates and leg_coordinates and coordinates[-1] == leg_coordinates[0]:
                leg_coordinates = leg_coordinates[1:]
            coordinates.extend(leg_coordinates)

        if len(coordinates) < 2:
            raise ProviderNoRouteError(
                "Valhalla route geometry is degenerate", provider=self.name
            )
        return coordinates

    @staticmethod
    def _elevation_totals(trip: dict) -> tuple[float | None, float | None]:
        """Ascent/descent from the per-leg elevation arrays, None when the
        service did not return elevation data."""
        legs = trip.get("legs")
        if not isinstance(legs, list):
            return None, None
        ascent = 0.0
        descent = 0.0
        seen = False
        for leg in legs:
            elevation = leg.get("elevation") if isinstance(leg, dict) else None
            if not isinstance(elevation, list) or len(elevation) < 2:
                continue
            seen = True
            previous = _as_float(elevation[0])
            for sample in elevation[1:]:
                current = _as_float(sample)
                if previous is None or current is None:
                    break
                delta = current - previous
                if delta > 0:
                    ascent += delta
                else:
                    descent -= delta
                previous = current
        if not seen:
            return None, None
        return ascent, descent

    async def health(self) -> dict:
        """Liveness probe against ``GET /status``.

        Valhalla's meili answers 200 with build metadata (and 503 when
        still loading tiles), making /status a true readiness signal.
        """
        try:
            if self._client is not None:
                response = await self._client.get(
                    f"{self._base_url}/status", timeout=self._timeout_s
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.get(f"{self._base_url}/status")
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            return {"status": "unavailable", "error": str(exc)}

        if response.status_code == 200:
            return {"status": "ok"}
        return {"status": "degraded", "status_code": response.status_code}


def decode_polyline6(encoded: str, *, provider: str = "valhalla") -> list[list[float]]:
    """Decode a Valhalla polyline6 string at 1e-6 precision.

    This is Valhalla's trip-shape encoding: the standard Google polyline
    variable length encoding (5 bits per character, continuation bit,
    zig-zag) applied to lat/lon deltas scaled by 1e6, as plain ASCII --
    each character is one 5-bit group, not a base64 payload.
    """
    try:
        data = encoded.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ProviderBadResponseError(
            "Valhalla returned an undecodable route shape",
            provider=provider,
            detail={"error": str(exc)},
        ) from exc

    coordinates: list[list[float]] = []
    index = 0
    lat = 0
    lon = 0
    length = len(data)
    while index < length:
        shift = 0
        result = 0
        while True:
            if index >= length:
                raise ProviderBadResponseError(
                    "Valhalla route shape truncated", provider=provider
                )
            chunk = data[index] - 63
            index += 1
            result |= (chunk & 0x1F) << shift
            shift += 5
            if chunk < 0x20:
                break
        lat += ~(result >> 1) if result & 1 else result >> 1

        shift = 0
        result = 0
        while True:
            if index >= length:
                raise ProviderBadResponseError(
                    "Valhalla route shape truncated", provider=provider
                )
            chunk = data[index] - 63
            index += 1
            result |= (chunk & 0x1F) << shift
            shift += 5
            if chunk < 0x20:
                break
        lon += ~(result >> 1) if result & 1 else result >> 1

        coordinates.append([lon / 1e6, lat / 1e6])

    # Round away float artifacts from the 1e-6 scaling (==2.0 != 2.0 cases).
    return [[struct.unpack("<d", struct.pack("<d", c))[0] for c in pair] for pair in coordinates]
