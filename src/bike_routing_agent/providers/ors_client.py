"""Full async client for the openrouteservice public API (Core 9.x).

Implements every endpoint of the public service (see
https://openrouteservice.org/dev/#/api-docs):

- Directions:      GET/POST /v2/directions/{profile}, /json, /gpx, /geojson
- Export:          POST /v2/export/{profile}, /topojson, /json
- Isochrones:      POST /v2/isochrones/{profile}
- Matrix:          POST /v2/matrix/{profile}
- Snapping:        POST /v2/snap/{profile}, /json, /geojson
- POIs:            POST /openpoiservice/v0/pois
- Optimization:    POST /vroom/v0 (Vroom vehicle routing)
- Elevation:       POST /openelevationservice/v0/line, GET/POST /openelevationservice/v0/point
- Geocoding:       GET /pelias/v1/search, /autocomplete, /search/structured, /reverse
- Health:          GET /v2/health

Public API conventions (and therefore this client's): coordinates are
``[lon, lat]`` pairs, timings are in seconds, distances in meters.

``OpenRouteServiceAdapter`` (the ``RoutingProvider``) is built on top of
this client for its directions call; everything else is available
directly here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any, Literal

import httpx

from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderNoRouteError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

# ORS error codes that mean "no path could be found between the given
# points", as opposed to a malformed request or transient failure.
_ORS_NO_ROUTE_CODES = {2010, 2009}

DirectionFormat = Literal["json", "gpx", "geojson"]
ExportFormat = Literal["json", "topojson"]
IsochroneFormat = Literal["geojson", "json"]
SnapFormat = Literal["json", "geojson"]
ElevationFormat = Literal["geojson", "polyline", "encodedpolyline5", "encodedpolyline6", "point"]
ElevationLineInput = Literal["geojson", "polyline", "encodedpolyline5", "encodedpolyline6"]
ElevationPointInput = Literal["geojson", "point"]
ParseMode = Literal["json", "text"]


class OpenRouteServiceClient:
    """Low-level async client covering the full openrouteservice API surface."""

    name = "ors"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_s: float,
        max_retries: int = 2,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure key, base URL, per-request timeout and retry budget.

        ``http_client`` may be an injected ``httpx.AsyncClient`` (tests,
        shared connection pools); otherwise one is created per request.
        """
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._client = http_client

    # ------------------------------------------------------------------
    # Transport layer
    # ------------------------------------------------------------------

    async def _send(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                    body: dict[str, Any] | None = None, retries: int | None = None,
                    raise_on_5xx: bool = True) -> httpx.Response:
        """Send one request with timeout and retry-on-5xx/timeout.

        Returns the raw response; status-code mapping is the caller's job.
        429 is NOT retried (it is mapped by ``_request``). Pass ``retries``
        to override the client-wide retry budget. Pass
        ``raise_on_5xx=False`` to return a 5xx response immediately instead
        of retrying/raising -- used by the health probe, where a 5xx answer
        must be surfaced as "degraded", not retried away.
        """
        url = f"{self._base_url}{path}"
        headers = {"Authorization": self._api_key}
        if body is not None:
            headers["Content-Type"] = "application/json"

        max_retries = self._max_retries if retries is None else retries
        attempt = 0
        while True:
            try:
                if self._client is not None:
                    response = await self._client.request(
                        method, url, params=params, json=body, headers=headers,
                        timeout=self._timeout_s,
                    )
                else:
                    async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                        response = await client.request(
                            method, url, params=params, json=body, headers=headers
                        )
            except httpx.TimeoutException as exc:
                if attempt >= max_retries:
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

            if response.status_code >= 500:
                if not raise_on_5xx:
                    return response
                if attempt >= max_retries:
                    raise ProviderUnavailableError(
                        f"ORS returned HTTP {response.status_code}",
                        provider=self.name,
                        detail={"status_code": response.status_code},
                    )
                attempt += 1
                await asyncio.sleep(0.5 * attempt)
                continue
            return response

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        parse: ParseMode = "json",
    ) -> dict[str, Any] | str:
        """Send a request and map status codes to structured errors."""
        response = await self._send(method, path, params=params, body=body)

        if response.status_code == 429:
            raise ProviderRateLimitError(
                "ORS rate limit exceeded",
                provider=self.name,
                detail={"retry_after": response.headers.get("Retry-After")},
            )
        if response.status_code >= 400:
            self._raise_for_client_error(response)

        if parse == "text":
            return response.text
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise ProviderBadResponseError(
                "ORS returned invalid JSON", provider=self.name
            ) from exc

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``_request`` for endpoints that always answer with JSON."""
        result = await self._request(method, path, params=params, body=body)
        if not isinstance(result, dict):
            raise ProviderBadResponseError(
                f"ORS endpoint {path} returned a non-JSON payload",
                provider=self.name,
                detail={"path": path},
            )
        return result

    @staticmethod
    def _raise_for_client_error(response: httpx.Response) -> None:
        """Map a 4xx response to a structured error, honouring ORS error
        codes from the JSON body (no-route codes raise
        :class:`ProviderNoRouteError`, anything else is a bad response)."""
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
                provider="ors",
                detail={"status_code": response.status_code, "ors_code": error_code},
            )
        raise ProviderBadResponseError(
            f"ORS rejected the request (HTTP {response.status_code})",
            provider="ors",
            detail={"status_code": response.status_code, "body": body},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_coordinates(coordinates: Sequence[Sequence[float]]) -> list[list[float]]:
        """Accept tuples or lists of [lon, lat] and return JSON-ready pairs."""
        return [[float(lon), float(lat)] for lon, lat in coordinates]

    # ------------------------------------------------------------------
    # Directions service
    # ------------------------------------------------------------------

    async def directions_basic(
        self,
        profile: str,
        start: Sequence[float],
        end: Sequence[float],
    ) -> dict[str, Any]:
        """``GET /v2/directions/{profile}`` -- the basic two-point form.

        Returns a GeoJSON response; no options other than the two
        coordinates can be sent.
        """
        params = {
            "coordinates": f"{start[0]},{start[1]};{end[0]},{end[1]}"
        }
        return await self._request_json("GET", f"/v2/directions/{profile}", params=params)

    async def directions(
        self,
        profile: str,
        coordinates: Sequence[Sequence[float]] | None = None,
        *,
        body: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        format: DirectionFormat = "geojson",
    ) -> dict[str, Any] | str:
        """``POST /v2/directions/{profile}`` and its format variants.

        Pass either ``coordinates`` (plus optional ``options``) or a fully
        built request ``body`` (which takes precedence). ``format="gpx"``
        returns the raw GPX XML as a string; the JSON/GeoJSON formats
        return the parsed payload.
        """
        if body is None:
            if coordinates is None:
                raise ValueError("either coordinates or a request body must be provided")
            body = {"coordinates": self._normalize_coordinates(coordinates)}
            if options:
                body["options"] = options

        suffix = {"json": "/json", "gpx": "/gpx", "geojson": "/geojson"}[format]
        parse: ParseMode = "text" if format == "gpx" else "json"
        return await self._request(
            "POST", f"/v2/directions/{profile}{suffix}", body=body, parse=parse
        )

    # ------------------------------------------------------------------
    # Export service
    # ------------------------------------------------------------------

    async def export(
        self,
        profile: str,
        bbox: Sequence[float],
        *,
        options: dict[str, Any] | None = None,
        format: ExportFormat = "json",
    ) -> dict[str, Any]:
        """``POST /v2/export/{profile}`` (+ ``/topojson``, ``/json``).

        ``bbox`` is ``[min_lon, min_lat, max_lon, max_lat]``. Returns points
        and edges (with weights) within the box, or their TopoJSON
        representation for ``format="topojson"``.
        """
        body: dict[str, Any] = {"bbox": [float(v) for v in bbox]}
        if options:
            body["options"] = options
        path = f"/v2/export/{profile}" if format == "json" else f"/v2/export/{profile}/{format}"
        return await self._request_json("POST", path, body=body)

    # ------------------------------------------------------------------
    # Isochrones service
    # ------------------------------------------------------------------

    async def isochrones(
        self,
        profile: str,
        locations: Sequence[Sequence[float]],
        *,
        ranges: Sequence[float],
        range_type: Literal["time", "distance"] = "time",
        options: dict[str, Any] | None = None,
        format: IsochroneFormat = "geojson",
    ) -> dict[str, Any]:
        """``POST /v2/isochrones/{profile}`` -- reachability areas.

        ``ranges`` is a list of exact isochrone values (seconds for
        ``range_type="time"``, meters for ``"distance"``); pass a single
        element for one contour.
        """
        body: dict[str, Any] = {
            "locations": self._normalize_coordinates(locations),
            "range_type": range_type,
            "range": [float(r) for r in ranges],
        }
        if options:
            body["options"] = options
        path = (
            f"/v2/isochrones/{profile}" if format == "geojson"
            else f"/v2/isochrones/{profile}/{format}"
        )
        return await self._request_json("POST", path, body=body)

    # ------------------------------------------------------------------
    # Matrix service
    # ------------------------------------------------------------------

    async def matrix(
        self,
        profile: str,
        locations: Sequence[Sequence[float]],
        *,
        sources: Sequence[int] | None = None,
        targets: Sequence[int] | None = None,
        outputs: Sequence[str] | None = None,
        fail_on_error: bool = False,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``POST /v2/matrix/{profile}`` -- one-to-many / many-to-many.

        ``sources``/``targets`` are indices into ``locations``; omit both
        for the default square matrix where every point pairs with every
        point. ``outputs`` selects which values to return (e.g.
        ``["duration", "distance", "summary_distance"]``).
        """
        body: dict[str, Any] = {
            "locations": self._normalize_coordinates(locations),
            "fail_on_error": fail_on_error,
        }
        if sources is not None:
            body["sources"] = [int(i) for i in sources]
        if targets is not None:
            body["targets"] = [int(i) for i in targets]
        if outputs:
            body["outputs"] = list(outputs)
        if options:
            body["options"] = options
        return await self._request_json("POST", f"/v2/matrix/{profile}", body=body)

    # ------------------------------------------------------------------
    # Snapping service
    # ------------------------------------------------------------------

    async def snap(
        self,
        profile: str,
        locations: Sequence[Sequence[float]],
        *,
        search_radius: float = 200.0,
        options: dict[str, Any] | None = None,
        format: SnapFormat = "geojson",
    ) -> dict[str, Any]:
        """``POST /v2/snap/{profile}`` (+ ``/json``, ``/geojson``).

        Snaps each location to the nearest edge of the routing graph;
        locations with no snap within ``search_radius`` come back as
        ``null`` (or are omitted from the GeoJSON features).
        """
        body: dict[str, Any] = {
            "locations": self._normalize_coordinates(locations),
            "options": {"search_radius": float(search_radius), **(options or {})},
        }
        path = f"/v2/snap/{profile}" if format == "geojson" else f"/v2/snap/{profile}/{format}"
        return await self._request_json("POST", path, body=body)

    # ------------------------------------------------------------------
    # POIs service
    # ------------------------------------------------------------------

    async def pois(
        self,
        bbox: Sequence[float],
        *,
        filter: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``POST /openpoiservice/v0/pois`` -- points of interest around a geometry.

        ``bbox`` is ``[min_lon, min_lat, max_lon, max_lat]``. ``filter``
        supports ``where`` (OSM tag query), ``category`` and ``tags``.
        """
        body: dict[str, Any] = {"bbox": [float(v) for v in bbox]}
        if filter:
            body["filter"] = filter
        if options:
            body["options"] = options
        return await self._request_json("POST", "/openpoiservice/v0/pois", body=body)

    # ------------------------------------------------------------------
    # Optimization service (Vroom)
    # ------------------------------------------------------------------

    async def optimize(self, payload: dict[str, Any]) -> dict[str, Any]:
        """``POST /vroom/v0`` -- vehicle routing problem solver.

        ``payload`` is a Vroom request (``source``, ``target``,
        ``vehicles``, ``locations``, ``profile``, ...) passed through
        unmodified; see the Vroom API documentation for the schema.
        """
        return await self._request_json("POST", "/vroom/v0", body=payload)

    # ------------------------------------------------------------------
    # Elevation service
    # ------------------------------------------------------------------

    @staticmethod
    def _elevation_parse(format_out: ElevationFormat) -> ParseMode:
        """Select the response parse mode for the requested output format."""
        return "json" if format_out in ("geojson",) else "text"

    async def elevation_line(
        self,
        geometry: dict[str, Any] | Sequence[Sequence[float]] | str,
        *,
        format_in: ElevationLineInput = "geojson",
        format_out: ElevationFormat = "geojson",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any] | str:
        """``POST /openelevationservice/v0/line`` -- enrich a line with elevation.

        ``geometry`` depends on ``format_in``: a GeoJSON dict, a list of
        ``[lon, lat]`` pairs (polyline), or an encoded polyline string
        (precision 5 or 6). ``format_out`` other than ``"geojson"``
        returns the encoded string.
        """
        body: dict[str, Any] = {
            "format_in": format_in,
            "format_out": format_out,
            "geometry": geometry,
        }
        if options:
            body["options"] = options
        return await self._request(
            "POST", "/openelevationservice/v0/line", body=body,
            parse=self._elevation_parse(format_out),
        )

    async def elevation_point(
        self,
        geometry: Sequence[float] | str | dict[str, Any],
        *,
        method: Literal["get", "post"] = "get",
        format_in: ElevationPointInput = "point",
        format_out: ElevationFormat = "point",
    ) -> dict[str, Any] | str:
        """``GET``/``POST /openelevationservice/v0/point`` -- enrich a point.

        ``method="get"`` sends ``?geometry=lon,lat`` and takes a coordinate
        pair (or an already-formatted string). ``method="post"`` sends a
        JSON body; ``geometry`` is then a GeoJSON dict (``format_in``
        ``"geojson"``) or a ``lon,lat`` string (``format_in`` ``"point"``).
        """
        if method == "get":
            if isinstance(geometry, str):
                point = geometry
            elif isinstance(geometry, dict):
                raise ValueError(
                    "GET elevation points accept a [lon, lat] pair or a formatted "
                    "string, not a GeoJSON dict (use method='post' for GeoJSON)"
                )
            else:
                point = f"{geometry[0]},{geometry[1]}"
            params = {"geometry": point}
            return await self._request(
                "GET", "/openelevationservice/v0/point", params=params,
                parse=self._elevation_parse(format_out),
            )
        body = {"format_in": format_in, "format_out": format_out, "geometry": geometry}
        return await self._request(
            "POST", "/openelevationservice/v0/point", body=body,
            parse=self._elevation_parse(format_out),
        )

    # ------------------------------------------------------------------
    # Geocoding service (Pelias)
    # ------------------------------------------------------------------

    @staticmethod
    def _pelias_params(params: dict[str, Any]) -> dict[str, Any]:
        """Drop ``None`` values so unset optional query params are omitted."""
        return {key: value for key, value in params.items() if value is not None}

    async def geocode_search(
        self,
        text: str,
        *,
        size: int = 5,
        boundary_countries: str | None = None,
        boundary_geometries: str | None = None,
        boundary_rect: str | None = None,
    ) -> dict[str, Any]:
        """``GET /pelias/v1/search`` -- forward geocoding.

        Boundary parameters (comma-separated; combinable when overlapping):
        ``boundary_countries`` (ISO 3166-1 alpha-2), ``boundary_geometries``
        (geometry codes), ``boundary_rect`` (``lon1,lat1,lon2,lat2``).
        """
        params = self._pelias_params(
            {
                "text": text,
                "size": size,
                "boundary.countries": boundary_countries,
                "boundary.geometries": boundary_geometries,
                "boundary.rect": boundary_rect,
            }
        )
        return await self._request_json("GET", "/pelias/v1/search", params=params)

    async def geocode_autocomplete(
        self,
        text: str,
        *,
        size: int = 5,
        boundary_countries: str | None = None,
        boundary_geometries: str | None = None,
        boundary_rect: str | None = None,
    ) -> dict[str, Any]:
        """``GET /pelias/v1/autocomplete`` -- geocoding suggestions.

        Responses are asynchronous (the service may still be loading), so
        callers should treat results as best-effort and throttle usage.
        """
        params = self._pelias_params(
            {
                "text": text,
                "size": size,
                "boundary.countries": boundary_countries,
                "boundary.geometries": boundary_geometries,
                "boundary.rect": boundary_rect,
            }
        )
        return await self._request_json("GET", "/pelias/v1/autocomplete", params=params)

    async def geocode_search_structured(
        self,
        *,
        size: int = 5,
        street: str | None = None,
        locality: str | None = None,
        region: str | None = None,
        county: str | None = None,
        country: str | None = None,
        postalcode: str | None = None,
        boundary_countries: str | None = None,
        boundary_geometries: str | None = None,
        boundary_rect: str | None = None,
    ) -> dict[str, Any]:
        """``GET /pelias/v1/search/structured`` -- structured forward geocoding (beta).

        Address components are matched component-wise; at least one of
        ``street``/``locality``/``region``/``county``/``country``/
        ``postalcode`` should be given.
        """
        params = self._pelias_params(
            {
                "size": size,
                "street": street,
                "locality": locality,
                "region": region,
                "county": county,
                "country": country,
                "postalcode": postalcode,
                "boundary.countries": boundary_countries,
                "boundary.geometries": boundary_geometries,
                "boundary.rect": boundary_rect,
            }
        )
        return await self._request_json("GET", "/pelias/v1/search/structured", params=params)

    async def geocode_reverse(
        self,
        point: Sequence[float] | str,
        *,
        size: int = 5,
        boundary_countries: str | None = None,
        boundary_geometries: str | None = None,
        boundary_rect: str | None = None,
    ) -> dict[str, Any]:
        """``GET /pelias/v1/reverse`` -- reverse geocoding.

        ``point`` is a ``[lon, lat]`` pair (or an already-formatted
        ``"lon,lat"`` string).
        """
        if isinstance(point, str):
            point_param = point
        else:
            point_param = f"{point[0]},{point[1]}"
        params = self._pelias_params(
            {
                "point": point_param,
                "size": size,
                "boundary.countries": boundary_countries,
                "boundary.geometries": boundary_geometries,
                "boundary.rect": boundary_rect,
            }
        )
        return await self._request_json("GET", "/pelias/v1/reverse", params=params)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """``GET /v2/health``.

        Note: /v2/health is exposed by self-hosted ORS backends but not by
        the public multi-tenant api.openrouteservice.org, which 404s here.
        That 404 is reported as "unknown", not "degraded" -- a permanently
        absent endpoint is not evidence the service itself is unhealthy.
        """
        try:
            response = await self._send("GET", "/v2/health", retries=0, raise_on_5xx=False)
        except (ProviderTimeoutError, ProviderUnavailableError) as exc:
            return {"status": "unavailable", "error": str(exc)}

        if response.status_code == 200:
            return {"status": "ok"}
        if response.status_code == 404:
            return {
                "status": "unknown",
                "detail": f"{self._base_url}/v2/health not found on this ORS deployment",
            }
        return {"status": "degraded", "status_code": response.status_code}
