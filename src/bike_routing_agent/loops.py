"""Loop-route via synthesis (issue #5).

`RouteConstraints.return_to_origin` turns a request into a loop: the
destination is snapped back to the origin (geocode node) and, unless the
caller drew the loop themselves with `via` points, the route node
synthesizes waypoints here so the engines have to produce an actual
circuit instead of a degenerate zero-length route.

Why synthesized vias and not a provider-native round trip: a native
`roundtrip` endpoint exists only on standalone ORS installs -- BRouter and
Valhalla have nothing equivalent -- so per-engine loop support would make
the multi-candidate comparison engine-asymmetric. Via points are the one
mechanism every adapter already forwards (origin -> vias -> destination),
which keeps this module pure geometry with no provider knowledge.

The shape is a regular polygon *inscribed through the origin*: with the
origin on the circumcircle and the other vertices evenly spaced on it,
every leg is one chord of the target circuit, so each leg is
`target / sides` metres of pure "make progress" and the route cannot
collapse into retracing a single road out and back. Two synthesized vias
(a triangle through the origin) are the minimum that guarantees this --
one via would just be an out-and-back with extra steps, and more vias add
provider segments without making the loop meaningfully rounder. The
deterministic bearing start makes results reproducible; the caller's
`loop_direction` biases whether the circuit sweeps clockwise or
counterclockwise from the origin.

Like the enrichment layer, this module refuses to pretend: if the target
distance is missing or nonsensical, that is an error for the caller to
surface, not something geometry can invent an answer for.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from bike_routing_agent.models import Coordinate, LoopDirection

# One degree of latitude on a sphere of Earth's mean radius.
METERS_PER_DEGREE_LAT = 111_320.0

# Number of synthesized vias: origin + these form a triangle through the
# origin (see module docstring for why two is the exact right number).
SYNTHESIZED_VIA_COUNT = 2

# Above this |latitude| the equirectangular projection below degenerates
# (cos(lat) -> 0); polar loop planning is deliberately unsupported.
_MAX_SUPPORTABLE_LAT = 89.5


@dataclass(frozen=True)
class LoopPlan:
    """The synthesized circuit: waypoints to route through, plus provenance.

    ``radius_m`` is the circumradius of the polygon; ``reach_m`` is the
    straight-line distance from the start to the farthest waypoint -- the
    "how far out does this loop go" answer a rider would understand.
    ``sides`` is the polygon's vertex count including the origin.
    """

    vias: tuple[Coordinate, ...]
    radius_m: float
    direction: LoopDirection
    sides: int

    @property
    def reach_m(self) -> float:
        """Straight-line start-to-farthest-waypoint distance."""
        return 2.0 * self.radius_m * math.sin(math.pi / self.sides)

    def to_state(self) -> dict[str, object]:
        """JSON-safe record for graph state / candidate provenance."""
        return {
            "vias": [v.model_dump(mode="json") for v in self.vias],
            "radius_m": self.radius_m,
            "reach_m": round(self.reach_m, 1),
            "direction": self.direction,
            "sides": self.sides,
        }


def _meters_per_degree_lon(lat_deg: float) -> float:
    return METERS_PER_DEGREE_LAT * math.cos(math.radians(lat_deg))


def _plane_to_coordinate(origin: Coordinate, x_m: float, y_m: float) -> Coordinate:
    """Local equirectangular plane (x=east, y=north, metres) back to WGS84.

    Good enough for loop sizing at the ~100 km scale the constraint model
    allows (errors are well under the width of a routing detour); latitudes
    are clamped and longitudes wrapped so a loop starting very close to a
    pole or the antimeridian still yields valid coordinates.
    """
    lat = origin.lat + y_m / METERS_PER_DEGREE_LAT
    lon = origin.lon + x_m / _meters_per_degree_lon(origin.lat)
    lat = max(-90.0, min(90.0, lat))
    lon = (lon + 180.0) % 360.0 - 180.0
    return Coordinate(lon=round(lon, 7), lat=round(lat, 7))


def synthesize_loop_vias(
    origin: Coordinate,
    target_distance_m: float,
    *,
    direction: LoopDirection = "clockwise",
) -> LoopPlan:
    """Plan a circuit of ``target_distance_m`` starting and ending at ``origin``.

    The origin and ``SYNTHESIZED_VIA_COUNT`` synthesized vias sit on a
    common circumcircle; consecutive legs are its chords, so the straight-line
    circuit length is exactly the target (road routing adds the usual detour
    overhead on top, the same way it does for any waypoint route).

    Raises ``ValueError`` for a non-positive target or a polar origin.
    """
    if target_distance_m <= 0:
        raise ValueError(f"target_distance_m must be > 0, got {target_distance_m}")
    if abs(origin.lat) > _MAX_SUPPORTABLE_LAT:
        raise ValueError(
            f"loop synthesis is unsupported above |lat| {_MAX_SUPPORTABLE_LAT} (got {origin.lat})"
        )
    if direction not in ("clockwise", "counterclockwise"):  # defensive: model layer checks too
        raise ValueError(f"unknown loop direction: {direction!r}")

    sides = SYNTHESIZED_VIA_COUNT + 1  # polygon vertices including the origin
    # Perimeter of a regular `sides`-gon through the circumcircle:
    # sides * 2r sin(pi/sides) == target  ->  r = target / (2 sides sin(pi/sides))
    radius_m = target_distance_m / (2.0 * sides * math.sin(math.pi / sides))

    # Put the circle's centre due west of the origin in the local plane, so
    # the origin is the circle's east-most point (central angle 0). Vias sit
    # at even central-angle steps; the sign of the step is the direction bias.
    sign = 1.0 if direction == "counterclockwise" else -1.0
    center_x, center_y = -radius_m, 0.0

    vias: list[Coordinate] = []
    for step in range(1, sides):
        angle = sign * 2.0 * math.pi * step / sides
        x = center_x + radius_m * math.cos(angle)
        y = center_y + radius_m * math.sin(angle)
        vias.append(_plane_to_coordinate(origin, x, y))

    return LoopPlan(
        vias=tuple(vias), radius_m=round(radius_m, 1), direction=direction, sides=sides
    )
