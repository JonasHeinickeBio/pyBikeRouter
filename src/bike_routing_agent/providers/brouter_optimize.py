"""Which BRouter requests to make for an optimisation, in order of usefulness.

BRouter prices the same trip differently per profile, offers its own alternative routes
(``alternativeidx`` 1 and 2) and takes per-request profile variables. For an optimisation we
want a broad, *relevant* set rather than every combination: the profiles that tend to serve
the goals come first, and the effort level cuts the list.

The orders below are judgement from docs/profile-evaluation.md and the measurements in
docs/optimizer.md, not guarantees: every candidate is measured afterwards and only the
measurement decides.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from bike_routing_agent.config import BROUTER_TRAFFIC_SWITCHES
from bike_routing_agent.models import MAX_VIA_POINTS, Coordinate, OptimizeSpec

# Profiles that tend to serve a goal, best guess first.
_FOR_GOAL: dict[str, tuple[str, ...]] = {
    "traffic": ("custom_commuter-v1", "custom_touring-v1", "custom_gravel-v2", "trekking"),
    "busy_roads": ("custom_commuter-v1", "custom_touring-v1", "custom_gravel-v2", "trekking"),
    "paved": ("trekking", "fastbike", "custom_commuter-v1", "custom_touring-v1"),
    "unpaved": ("mtb", "custom_gravel-v2", "custom_touring-v1"),
    "cobbles": ("custom_gravel-v2", "custom_touring-v1", "trekking"),
    "distance": ("shortest", "fastbike", "trekking"),
    "duration": ("fastbike", "trekking", "shortest"),
    "ascent": ("custom_touring-v1", "trekking", "custom_commuter-v1"),
    # The places passed depend on the corridor more than on the profile; the profiles differ
    # in how far they stray from the direct line, which is what varies the count.
    "pois": ("trekking", "custom_touring-v1", "custom_gravel-v2"),
}
EFFORT_REQUESTS = {"quick": 4, "normal": 8, "thorough": 12}
# Side detours (see detour_stops) fetched in addition to the requests above.
EFFORT_DETOURS = {"quick": 2, "normal": 4, "thorough": 6}
# Goals that no detour can serve (a longer way is never shorter or quicker).
_NO_DETOUR_GOALS = frozenset({"distance", "duration"})
# Share of the detour cap a side detour is sized for. A via point makes the roads wind far more
# than the straight-line sum suggests: at 0.7 the routes measured on Braunschweig -> Wernigerode
# came out 31-41 % longer than the shortest one (cap 30 %), at 0.3 they land at 11-25 %.
DETOUR_CAP_SHARE = 0.3
MIN_DETOUR_TRIP_KM = 3.0  # below this a "side" is a different street, not a different route
MIN_OFFSET_M = 300.0
MAX_OFFSET_SHARE = 0.35  # of the straight length: a via point further out is a different trip
# Where along the way the via point sits, and on which side, most promising first.
_DETOUR_PLACES = (
    (0.5, "left"),
    (0.5, "right"),
    (0.35, "left"),
    (0.65, "right"),
    (0.65, "left"),
    (0.35, "right"),
)


@dataclass(frozen=True)
class Variant:
    profile: str
    alternative_index: int = 0
    overrides: dict[str, int] = field(default_factory=dict, hash=False, compare=False)
    source: str = ""


def plan_variants(spec: OptimizeSpec, own_profile: str) -> list[Variant]:
    """Requests to make *besides* the main route (``own_profile``, index 0), most useful first."""
    goals = sorted(spec.goals, key=lambda g: -g.weight)  # stable: the order given breaks ties
    profiles: list[str] = []
    for goal in goals:
        for profile in _FOR_GOAL[goal.metric]:
            if profile not in profiles:
                profiles.append(profile)
    if "shortest" not in profiles:
        profiles.append("shortest")  # the baseline the detour is measured against
    profiles = [p for p in profiles if p != own_profile]

    variants = [Variant(p, 0, source="profile") for p in profiles]
    # BRouter's own second and third routes for the profiles that matter most.
    lead = ([own_profile] if own_profile != "shortest" else []) + profiles[:1]
    for profile in dict.fromkeys(lead):
        variants.append(Variant(profile, 1, source="2nd route"))
    if profiles:
        variants.append(Variant(profiles[0], 2, source="3rd route"))
    # The traffic switch the other way round, for the first profile that has one: with
    # goals that are not about traffic, ignoring it may be faster or more direct.
    if not any(g.metric in ("traffic", "busy_roads") for g in spec.goals):
        for profile in [own_profile, *profiles]:
            if profile in BROUTER_TRAFFIC_SWITCHES:
                variants.append(
                    Variant(
                        profile,
                        0,
                        {BROUTER_TRAFFIC_SWITCHES[profile]: 0},
                        source="traffic not avoided",
                    )
                )
                break
    limit = EFFORT_REQUESTS[spec.effort]
    return variants[:limit]


def lead_profile(spec: OptimizeSpec, own_profile: str) -> str:
    """The profile that tends to serve the most important goal best (``own_profile`` when none
    of the lists has one): the one a detour is routed with."""
    for goal in sorted(spec.goals, key=lambda g: -g.weight):
        for profile in _FOR_GOAL[goal.metric]:
            if profile != "shortest":
                return profile
    return own_profile


@dataclass(frozen=True)
class Detour:
    """A via point beside the direct line, to send a route a different way than any profile does."""

    side: str  # "left" or "right" of the direction of travel
    along: float  # where on the direct line (0..1) the via point lies abeam of
    via: Coordinate

    @property
    def label(self) -> str:
        return f"detour {self.side} {round(self.along * 100)} %"


_M_PER_DEG_LAT = 111_320.0


def _via_length(along: float, offset: float) -> float:
    """Length of the way origin -> via -> destination over a direct line of length 1."""
    return math.hypot(along, offset) + math.hypot(1.0 - along, offset)


def _offset_for(along: float, extra: float) -> float:
    """The distance of the via point from the line (as a share of the line's length) that makes
    the way through it ``extra`` (a share, 0.1 = 10 %) longer than the direct line."""
    low, high = 0.0, 1.0
    for _ in range(40):  # the length grows steadily with the offset: bisect
        mid = (low + high) / 2
        low, high = (mid, high) if _via_length(along, mid) < 1.0 + extra else (low, mid)
    return (low + high) / 2


def detour_stops(
    origin: Coordinate,
    destination: Coordinate,
    *,
    max_detour_pct: float,
    count: int,
) -> list[Detour]:
    """Via points to the left and right of the direct line, sized so the way through each is
    about ``DETOUR_CAP_SHARE`` of the allowed detour longer than the straight line.

    A profile only reprices the roads between the places; a via point makes the route bend away
    from a busy corridor (or toward other roads) that no profile would leave. Most will not
    win -- every route is measured afterwards and only the measurement decides -- and ones that
    come out near-identical are collapsed by the optimiser.
    """
    if count <= 0 or max_detour_pct <= 0:
        return []
    mid_lat = math.radians((origin.lat + destination.lat) / 2)
    dlon = (destination.lon - origin.lon + 180.0) % 360.0 - 180.0  # the short way round the globe
    x = dlon * math.cos(mid_lat) * _M_PER_DEG_LAT
    y = (destination.lat - origin.lat) * _M_PER_DEG_LAT
    length = math.hypot(x, y)
    if length < MIN_DETOUR_TRIP_KM * 1000.0:
        return []
    extra = min(max_detour_pct / 100.0, 1.0) * DETOUR_CAP_SHARE
    # Left of the direction of travel is the vector rotated a quarter turn anticlockwise.
    left_x, left_y = -y / length, x / length
    found: list[Detour] = []
    for along, side in _DETOUR_PLACES:
        offset = min(_offset_for(along, extra), MAX_OFFSET_SHARE) * length
        if offset < MIN_OFFSET_M:
            continue
        sign = 1.0 if side == "left" else -1.0
        px = along * x + sign * offset * left_x
        py = along * y + sign * offset * left_y
        lon = (origin.lon + px / (math.cos(mid_lat) * _M_PER_DEG_LAT) + 180.0) % 360.0 - 180.0
        lat = origin.lat + py / _M_PER_DEG_LAT
        if not -90 <= lat <= 90:
            continue
        found.append(Detour(side, along, Coordinate(lon=lon, lat=lat)))
        if len(found) == count:
            break
    return found


@dataclass(frozen=True)
class PoiVia:
    """A well-known place a candidate route may be sent through (the ``pois`` goal)."""

    name: str
    coordinate: Coordinate
    along_km: float  # position along the direct line origin -> destination


def poi_via_sets(vias: Sequence[PoiVia], *, room: int = MAX_VIA_POINTS) -> list[list[PoiVia]]:
    """Which places to try as via points, one set per extra route: each of the leading ones
    alone, and the two best together -- in travel order, at most ``room`` per set."""
    top = list(vias)[:3]
    sets: list[list[PoiVia]] = [[v] for v in top]
    if len(top) >= 2 and room >= 2:
        sets.append(top[:2])
    return [sorted(s, key=lambda v: v.along_km) for s in sets if len(s) <= room]
