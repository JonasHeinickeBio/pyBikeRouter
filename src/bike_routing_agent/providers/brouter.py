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
import logging
import math
from collections.abc import Sequence
from typing import Any

import httpx

from bike_routing_agent.config import (
    BROUTER_ALTERNATIVE_PROFILES,
    BROUTER_PROFILE_MAP,
    BROUTER_TRAFFIC_SWITCHES,
)
from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderCoverageError,
    ProviderNoRouteError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.models import (
    MAX_VIA_POINTS,
    OptimizeSpec,
    RouteCandidate,
    RouteMetrics,
    RoutingRequest,
)
from bike_routing_agent.providers.brouter_optimize import (
    EFFORT_REQUESTS,
    PoiVia,
    plan_variants,
    poi_via_sets,
)
from bike_routing_agent.providers.brouter_segments import missing_segment
from bike_routing_agent.providers.brouter_tags import (
    main_road_share,
    surface_shares,
    traffic_summary,
)

# Fragments BRouter puts in its plain-text 400 body when the request was
# well-formed but no path exists (as opposed to a bad profile/coordinates).
# Every version of the gravel profile: results recorded with an older one (the
# profile name is in each plan's provenance) keep their warnings.
GRAVEL_PROFILES = frozenset({"custom_gravel-v1", "custom_gravel-v2"})
logger = logging.getLogger(__name__)

_NO_ROUTE_MARKERS = ("not reachable", "no route", "no track")


def _was_killed(body: str) -> bool:
    """BRouter's answer when its watchdog cancelled a request that waited too long."""
    lowered = body.lower()
    return "operation killed" in lowered or "thread-priority-watchdog" in lowered


_MAX_ERROR_BODY_CHARS = 500


def _straight_km(a: Any, b: Any) -> float:
    """Straight-line distance between two coordinates (haversine), in km."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat, dlon = lat2 - lat1, math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


class BRouterAdapter:
    """RoutingProvider backed by a local/self-hosted BRouter RouteServer."""

    name = "brouter"

    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float = 10.0,
        max_retries: int = 0,
        alternatives: bool = False,
        alternatives_timeout_s: float = 8.0,
        alternatives_timeout_per_km_s: float = 0.0,
        alternatives_timeout_max_s: float = 120.0,
        optimize_timeout_per_km_s: float = 1.2,
        optimize_timeout_max_s: float = 300.0,
        max_concurrency: int = 1,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        # A BRouter server runs one routing thread unless it was started with more; a request
        # that has to wait more than ~2 s for the thread is killed ("contention", HTTP 400
        # "operation killed by thread-priority-watchdog"). The main route and its alternatives
        # are sent together, so they are queued here instead of racing each other there.
        self._slots = asyncio.Semaphore(max_concurrency)
        self._alternatives = alternatives
        self._alternatives_timeout_s = alternatives_timeout_s
        self._alternatives_per_km_s = alternatives_timeout_per_km_s
        self._alternatives_max_s = max(alternatives_timeout_max_s, alternatives_timeout_s)
        self._optimize_per_km_s = optimize_timeout_per_km_s
        self._optimize_max_s = optimize_timeout_max_s
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
        elif profile in GRAVEL_PROFILES:
            # Stock gravel only penalises ferry segments (initialcost 20000),
            # it never forbids them; custom_touring-v1 sets allow_ferries=false
            # and stock profile behaviour is not inferred.
            warnings.append(
                f"avoid_ferries=True is not enforced by {profile}: the profile "
                "penalises ferry segments but may still route over them"
            )
        if request.constraints.avoid_high_traffic_roads and profile not in BROUTER_TRAFFIC_SWITCHES:
            warnings.append(
                f"avoid_high_traffic_roads=True cannot be applied by {profile}: the profile "
                "has no traffic setting, so its own cost structure decides"
            )
        return warnings

    @staticmethod
    def _profile_overrides(request: RoutingRequest, profile: str) -> dict[str, int]:
        """Profile variables set for this request (``avoid_high_traffic_roads`` -> the
        profile's traffic switch); empty for a profile without one."""
        variable = BROUTER_TRAFFIC_SWITCHES.get(profile)
        if variable is None:
            return {}
        return {variable: 1 if request.constraints.avoid_high_traffic_roads else 0}

    async def _get(
        self, params: dict[str, str], *, timeout_s: float | None = None
    ) -> httpx.Response:
        # One HTTP call may take as long as the caller allows (never less than the setting).
        call_timeout = max(self._timeout_s, timeout_s or 0.0)
        attempt = 0
        while True:
            try:
                # The slot is held for the HTTP call only, not while backing off: BRouter is
                # idle then, and a waiting request should be able to use it.
                async with self._slots:
                    if self._client is not None:
                        response = await self._client.get(
                            f"{self._base_url}/brouter", params=params, timeout=call_timeout
                        )
                    else:
                        async with httpx.AsyncClient(timeout=call_timeout) as client:
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

            if response.status_code == 400 and _was_killed(response.text):
                # Busy, not wrong: BRouter cancelled this request itself. Try again.
                if attempt >= self._max_retries:
                    raise ProviderUnavailableError(
                        "BRouter was busy and cancelled the request",
                        provider=self.name,
                        detail={
                            "status_code": 400,
                            "body": response.text[:_MAX_ERROR_BODY_CHARS],
                            "attempts": attempt + 1,
                        },
                    )
                attempt += 1
                await asyncio.sleep(1.0 * attempt)
                continue
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
        return await self._route_with_profile(
            request, self._profile_for(request.constraints.bike_type.value)
        )

    def alternatives_deadline_s(self, request: RoutingRequest) -> float:
        """How long the alternatives may take for this trip.

        BRouter's time grows with the trip (the slowest profile took ~43 s for a 195 km route
        and the requests queue behind the main route), so the base allowance is raised by
        ``alternatives_timeout_per_km_s`` for every km between the places (straight line),
        up to ``alternatives_timeout_max_s``.
        """
        points = [request.origin, *request.via, request.destination]
        km = sum(_straight_km(a, b) for a, b in zip(points, points[1:], strict=False))
        wanted = self._alternatives_timeout_s + self._alternatives_per_km_s * km
        return min(wanted, self._alternatives_max_s)

    def optimization_deadline_s(self, request: RoutingRequest) -> float:
        """How long an optimisation may spend fetching routes: 20 s plus
        ``optimize_timeout_per_km_s`` per km between the places, up to ``optimize_timeout_max_s``
        (the requests queue behind each other)."""
        points = [request.origin, *request.via, request.destination]
        km = sum(_straight_km(a, b) for a, b in zip(points, points[1:], strict=False))
        return min(20.0 + self._optimize_per_km_s * km, max(self._optimize_max_s, 20.0))

    async def optimization_candidates(
        self,
        request: RoutingRequest,
        spec: OptimizeSpec,
        *,
        poi_vias: Sequence[PoiVia] = (),
    ) -> list[RouteCandidate]:
        """Many routes for the same trip, for the optimiser: other profiles, BRouter's own
        second and third routes, and a traffic variant (``brouter_optimize.plan_variants``).

        Best effort within :meth:`optimization_deadline_s`; whatever has finished is returned,
        in plan order, and a variant that fails or finds no route is simply absent.
        """
        own = self._profile_for(request.constraints.bike_type.value)
        # Routes sent through well-known places (the "pois" goal): the own profile with one or
        # two sights as via points; only when the person gave no via points of their own.
        through = poi_via_sets(poi_vias, room=MAX_VIA_POINTS) if not request.via else []
        variants = plan_variants(spec, own)
        budget = max(2, EFFORT_REQUESTS[spec.effort] - len(through))
        variants = variants[:budget]
        if not variants and not through:
            return []
        deadline = self.optimization_deadline_s(request)
        via_tasks = {
            asyncio.ensure_future(
                self._route_with_profile(
                    request.model_copy(update={"via": [v.coordinate for v in stops]}),
                    own,
                    timeout_s=deadline,
                )
            ): stops
            for stops in through
        }
        tasks = {
            asyncio.ensure_future(
                self._route_with_profile(
                    request,
                    v.profile,
                    timeout_s=deadline,
                    alternative_index=v.alternative_index,
                    extra_overrides=v.overrides,
                )
            ): v
            for v in variants
        }
        done, pending = await asyncio.wait([*tasks, *via_tasks], timeout=deadline)
        for task in pending:
            task.cancel()
            what = (
                tasks[task].source
                if task in tasks
                else "via " + ", ".join(v.name for v in via_tasks[task])
            )
            logger.info("brouter optimisation variant %s timed out", what)
        found: list[RouteCandidate] = []
        for task, stops in via_tasks.items():
            if task not in done:
                continue
            try:
                result = task.result()
            except Exception as exc:
                logger.info("brouter route via %s unavailable: %s", [v.name for v in stops], exc)
                continue
            names = [v.name for v in stops]
            provenance = {
                **result.provenance,
                "alternative_of": own,
                "optimizer_source": "via " + ", ".join(names),
                "optimizer_via": names,
            }
            found.append(result.model_copy(update={"provenance": provenance}))
        for task, variant in tasks.items():  # plan order
            if task not in done:
                continue
            try:
                result = task.result()
            except Exception as exc:
                logger.info("brouter optimisation variant %s unavailable: %s", variant.source, exc)
                continue
            provenance = {
                **result.provenance,
                "alternative_of": own,
                "optimizer_source": variant.source,
            }
            found.append(result.model_copy(update={"provenance": provenance}))
        return found

    async def alternatives(self, request: RoutingRequest) -> list[RouteCandidate]:
        """The same trip under the other profiles suggested for this bike type.

        Best effort: a profile that finds no route or fails is left out, because
        the main route is what the caller asked for. Results are marked in their
        provenance (``alternative_of``) so they can be told apart.
        """
        if not self._alternatives:
            return []
        main = self._profile_for(request.constraints.bike_type.value)
        profiles = [
            p for p in BROUTER_ALTERNATIVE_PROFILES.get(request.constraints.bike_type.value, ())
            if p != main
        ]  # fmt: skip
        if not profiles:
            return []
        # Best effort within a deadline: whatever has finished when it passes is kept,
        # the rest is cancelled (with its retries), so one slow profile cannot hold up
        # the plan.
        deadline = self.alternatives_deadline_s(request)
        tasks = {
            asyncio.ensure_future(self._route_with_profile(request, p, timeout_s=deadline)): p
            for p in profiles
        }
        done, pending = await asyncio.wait(tasks, timeout=deadline)
        for task in pending:
            task.cancel()
            logger.info("brouter alternative profile %s timed out", tasks[task])
        found: list[RouteCandidate] = []
        for task, profile in tasks.items():  # profile order, not completion order
            if task not in done:
                continue
            try:
                result = task.result()
            except Exception as exc:
                logger.info("brouter alternative profile %s unavailable: %s", profile, exc)
                continue
            provenance = {**result.provenance, "alternative_of": main}
            found.append(result.model_copy(update={"provenance": provenance}))
        return found

    async def _route_with_profile(
        self,
        request: RoutingRequest,
        profile: str,
        *,
        timeout_s: float | None = None,
        alternative_index: int = 0,
        extra_overrides: dict[str, int] | None = None,
    ) -> RouteCandidate:
        overrides = {**self._profile_overrides(request, profile), **(extra_overrides or {})}
        response = await self._get(
            {
                "lonlats": self._lonlats(request),
                "profile": profile,
                "alternativeidx": str(alternative_index),
                "format": "geojson",
                **{f"profile:{name}": str(value) for name, value in overrides.items()},
            },
            timeout_s=timeout_s,
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

        candidate = self._normalize(payload, profile=profile, overrides=overrides)
        build_warnings = self._build_warnings(request, profile)
        if build_warnings:
            candidate = candidate.model_copy(
                update={"warnings": [*build_warnings, *candidate.warnings]}
            )
        return candidate

    @staticmethod
    def _raise_for_routing_error(response: httpx.Response) -> None:
        body = response.text[:_MAX_ERROR_BODY_CHARS]
        missing = missing_segment(body)
        if missing is not None:
            raise ProviderCoverageError(
                f"BRouter has no map data for this area (segment {missing})",
                provider="brouter",
                detail={"status_code": response.status_code, "body": body, "segments": [missing]},
            )
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

    def _normalize(
        self, payload: object, *, profile: str, overrides: dict[str, int] | None = None
    ) -> RouteCandidate:
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
            main_road_share=main_road_share(properties.get("messages")),
            traffic_shares=traffic_summary(properties.get("messages"))[0],
            traffic_index=traffic_summary(properties.get("messages"))[1],
            engine_surface_shares=surface_shares(properties.get("messages")),
        )

        return RouteCandidate(
            provider=self.name,
            provider_profile=profile,
            geometry_geojson=track["geometry"],
            metrics=metrics,
            # What was set per request is part of the record: the same profile with a
            # different switch is a different route.
            provenance={
                "provider": self.name,
                "profile": profile,
                **({"profile_overrides": overrides} if overrides else {}),
            },
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
