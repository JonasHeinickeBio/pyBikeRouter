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
