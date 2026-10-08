"""weather_candidates node: the forecast along each ranked route at departure.

Runs after scoring, so only the candidates actually returned to the caller are
looked up, and *never changes the plan's outcome*: weather is best effort. A
provider outage, a departure beyond the forecast range or an unusable geometry
leaves ``weather`` empty and sets ``weather_status`` instead of failing the plan.
Weather is informational -- it does not enter the score.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from bike_routing_agent.models import MAX_FORECAST_DAYS, RouteCandidate
from bike_routing_agent.state import RouteAgentState
from bike_routing_agent.weather.analysis import (
    ESTIMATED_SPEED_KMH,
    RoutePoint,
    build_route_weather,
    sample_route,
)
from bike_routing_agent.weather.service import WeatherService

logger = logging.getLogger(__name__)

WeatherNodeFn = Callable[[RouteAgentState], Awaitable[dict[str, Any]]]

# Forecast grids are kilometres wide: points closer than this share one lookup.
POINT_GRID_DEG = 0.05
# A departure this far in the past is read as "now" (a stale form value).
MAX_PAST = timedelta(hours=1)


def _parse_departure(raw: Any, now: datetime) -> datetime:
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return now
        departure = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        departure = departure.astimezone(UTC)
        return now if departure < now - MAX_PAST else departure
    return now


def _grid(lon: float, lat: float) -> tuple[float, float]:
    return (
        round(round(lon / POINT_GRID_DEG) * POINT_GRID_DEG, 4),
        round(round(lat / POINT_GRID_DEG) * POINT_GRID_DEG, 4),
    )


def _floor_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def build_weather_node(
    *,
    service: WeatherService | None,
    max_samples: int = 5,
    spacing_km: float = 10.0,
    option_hours_before: int = 0,
    option_hours_after: int = 0,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> WeatherNodeFn:
    """``option_hours_before/after``: also compare departures this many hours around the
    requested one (0/0 = off); the forecast window is widened to cover them."""
    offsets = (
        list(range(-option_hours_before, option_hours_after + 1))
        if option_hours_before or option_hours_after
        else []
    )

    async def weather_candidates(state: RouteAgentState) -> dict[str, Any]:
        raw_candidates = state.get("candidates", [])
        if service is None or not raw_candidates:
            return {}

        current = now()
        departure = _parse_departure(state.get("departure_time"), current)
        if departure > current + timedelta(days=MAX_FORECAST_DAYS):
            return {"weather_status": "not_covered"}

        candidates = [RouteCandidate.model_validate(c) for c in raw_candidates]
        plans: list[tuple[list[RoutePoint], float, str]] = []
        for candidate in candidates:
            points = sample_route(
                candidate.geometry_geojson, max_samples=max_samples, spacing_km=spacing_km
            )
            duration = candidate.metrics.duration_s
            source = "provider"
            if duration is None or duration <= 0:
                duration = candidate.metrics.distance_m / 1000 / ESTIMATED_SPEED_KMH * 3600
                source = "estimated"
            plans.append((points, duration, source))

        grid_index: dict[tuple[float, float], int] = {}
        for points, _, _ in plans:
            for point in points:
                grid_index.setdefault(_grid(point.lon, point.lat), len(grid_index))
        if not grid_index:
            return {"weather_status": "skipped"}

        arrival = max(departure + timedelta(seconds=duration) for _, duration, _ in plans)
        # Alternatives widen the window, but never into the past: you cannot leave earlier than now.
        earliest_option = max(departure - timedelta(hours=option_hours_before), current)
        start = _floor_hour(min(departure, earliest_option))
        end = _floor_hour(arrival + timedelta(hours=option_hours_after)) + timedelta(hours=1)
        try:
            forecast = await service.forecast(list(grid_index), start, end)
        except Exception:
            logger.warning("weather lookup failed; plan continues without it", exc_info=True)
            return {"weather_status": "unavailable"}

        updated: list[dict[str, Any]] = []
        any_weather = False
        for raw, candidate, (points, duration, source) in zip(
            raw_candidates, candidates, plans, strict=True
        ):
            series = [forecast.series[grid_index[_grid(p.lon, p.lat)]] for p in points]
            weather = build_route_weather(
                points,
                series,
                departure=departure,
                duration_s=duration,
                duration_source=source,
                provider=forecast.provider,
                attribution=forecast.attribution,
                retrieved_at=forecast.retrieved_at,
                distance_m=candidate.metrics.distance_m,
                sources=forecast.sources,
                option_offsets_h=offsets,
                earliest=current,
            )
            any_weather = any_weather or weather is not None
            updated.append(
                candidate.model_copy(update={"weather": weather}).model_dump(mode="json")
                if weather is not None
                else raw
            )

        update: dict[str, Any] = {
            "candidates": updated,
            "weather_status": "ok" if any_weather else "not_covered",
        }
        selected = state.get("selected_candidate")
        if selected is not None:
            index = next((i for i, c in enumerate(raw_candidates) if c == selected), 0)
            update["selected_candidate"] = updated[index]
        return update

    return weather_candidates
