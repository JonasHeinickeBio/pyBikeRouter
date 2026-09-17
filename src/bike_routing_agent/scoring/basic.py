"""Deterministic initial scoring.

Distance and elevation fit are computed from provider metrics; surface and
traffic quality are not scored yet because that needs an enrichment source
and a data-quality policy (tracked as a follow-up issue). Unknown surface
coverage is therefore treated as uncertainty -- it contributes no bonus --
rather than being assumed rideable.
"""

from __future__ import annotations

from bike_routing_agent.models import RouteCandidate, RouteConstraints

DISTANCE_WEIGHT = 0.65
ELEVATION_WEIGHT = 0.35
MAX_WARNING_PENALTY = 0.3
WARNING_PENALTY_PER_ITEM = 0.05


def score_candidate(
    candidate: RouteCandidate,
    constraints: RouteConstraints,
) -> tuple[float, dict[str, float]]:
    distance_km = candidate.metrics.distance_m / 1_000
    distance_fit = 1.0

    if constraints.target_distance_km:
        deviation = abs(distance_km - constraints.target_distance_km)
        distance_fit = max(0.0, 1.0 - deviation / constraints.target_distance_km)

    elevation_fit = 1.0
    if constraints.max_ascent_m and candidate.metrics.ascent_m is not None:
        excess = max(0.0, candidate.metrics.ascent_m - constraints.max_ascent_m)
        elevation_fit = max(0.0, 1.0 - excess / constraints.max_ascent_m)

    warning_penalty = min(MAX_WARNING_PENALTY, WARNING_PENALTY_PER_ITEM * len(candidate.warnings))
    score = DISTANCE_WEIGHT * distance_fit + ELEVATION_WEIGHT * elevation_fit - warning_penalty

    return score, {
        "distance_fit": distance_fit,
        "elevation_fit": elevation_fit,
        "warning_penalty": warning_penalty,
    }


def uncertainty_notes(candidate: RouteCandidate) -> list[str]:
    """Facts about missing/uncertain map metadata, for use in explanations.

    Kept separate from the score itself: unknown data is a caveat to state,
    not a penalty or a bonus to apply.
    """
    notes: list[str] = []
    fraction = candidate.metrics.unknown_surface_fraction
    if fraction is not None and fraction > 0:
        notes.append(f"surface type is unknown for {fraction:.0%} of this route")
    if candidate.metrics.ascent_m is None:
        notes.append("elevation data was not available for this route")
    return notes
