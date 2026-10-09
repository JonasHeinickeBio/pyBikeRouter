"""Picking the famous POIs a ride should pass.

Pure functions over already-found POIs: which ones, and in which order they are
visited. The graph node (``nodes/poi_stops.py``) does the I/O around them.

The order is the order along the intended line origin -> (vias) -> destination,
so the stops never send the rider back and forth.
"""

from __future__ import annotations

from collections.abc import Sequence

from bike_routing_agent.poi.geo import LonLat, haversine_m
from bike_routing_agent.poi.models import Poi

# Stops at the very ends would just be the start or finish.
_END_MARGIN = 0.03
# Two stops closer than this are the same detour.
MIN_STOP_SPACING_M = 1_000.0


def select_stops(
    pois: Sequence[Poi],
    *,
    count: int,
    line_length_km: float,
    min_fame: int = 1,
    avoid: Sequence[LonLat] = (),
    min_spacing_m: float = MIN_STOP_SPACING_M,
) -> list[Poi]:
    """The ``count`` best-known sights along a line, in visiting order.

    Only POIs with a known fame of at least ``min_fame`` qualify: "most famous" is never
    guessed for a place nobody has measured, nor claimed for one hardly anybody describes.
    Greedy by fame (ties: closer to the line), skipping any POI that sits too close to one
    already chosen or to a place in ``avoid`` (origin, destination, the caller's own vias).
    """
    if count < 1 or line_length_km <= 0:
        return []
    low, high = _END_MARGIN * line_length_km, (1 - _END_MARGIN) * line_length_km
    candidates = [
        p
        for p in pois
        if p.kind == "sight"
        and p.fame is not None
        and p.fame >= min_fame
        and p.along_route_km is not None
        and low <= p.along_route_km <= high
    ]
    candidates.sort(key=lambda p: (-(p.fame or 0), p.distance_from_route_m or 0.0, p.id))

    chosen: list[Poi] = []
    for poi in candidates:
        here = (poi.lon, poi.lat)
        if any(haversine_m(here, other) < min_spacing_m for other in avoid):
            continue
        if any(haversine_m(here, (c.lon, c.lat)) < min_spacing_m for c in chosen):
            continue
        chosen.append(poi)
        if len(chosen) == count:
            break
    return sorted(chosen, key=lambda p: p.along_route_km or 0.0)
