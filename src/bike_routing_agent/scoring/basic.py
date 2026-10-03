"""Deterministic scoring.

Distance and elevation fit are computed from provider metrics. A surface
fit (issue #23) is computed from the OSM enrichment under the
unknown-is-unknown policy and enters the score only through
``surface_active``: when the caller stated surface preferences *and* every
candidate being compared carries enough surface evidence. Anything less
leaves the score exactly as it was -- unknown surface coverage is
uncertainty, never a bonus and never a penalty -- and, because the decision
is made for the whole candidate set, scores of unenriched and enriched
candidates are never compared across different formulas.

Traffic / road-class quality is still not scored (it needs the highway
shares on ``RouteMetrics`` and calibration cases of its own).
"""

from __future__ import annotations

from collections.abc import Sequence

from bike_routing_agent.config import resolve_surface_tokens
from bike_routing_agent.models import RouteCandidate, RouteConstraints

DISTANCE_WEIGHT = 0.65
ELEVATION_WEIGHT = 0.35
MAX_WARNING_PENALTY = 0.3
WARNING_PENALTY_PER_ITEM = 0.05

# Weight of the surface fit relative to the 1.0 carried by distance +
# elevation: when active, score = (D*d + E*e + SURFACE_WEIGHT*s) /
# (1 + SURFACE_WEIGHT) - penalty, so the score stays in [0, 1] and a request
# without surface preferences scores exactly as before.
#
# Deliberately 0.0 (inactive) until a calibration run justifies a value:
# docs/scoring-and-exports.md ("Enabling the surface component") describes
# how to produce that evidence with scripts/calibrate.py, and the weight is
# then a one-line change here. The component is still computed and reported
# in ``score_breakdown`` so the calibration harness can sweep it.
SURFACE_WEIGHT = 0.0

# A candidate's surface evidence counts only when at least this share of its
# length has a known surface category; below it the fit would describe a
# sliver of the route.
MIN_KNOWN_SURFACE_SHARE = 0.5


def surface_fit(
    candidate: RouteCandidate, constraints: RouteConstraints
) -> tuple[float, float] | None:
    """``(fit, known_share)`` for the stated surface preferences, or ``None``.

    ``None`` means there is nothing to judge: no (recognised) preference, or
    no surface evidence. Shares in ``surface_coverage`` are of the whole
    route and sum with ``unknown_surface_fraction`` to 1, so they are
    re-based onto the *known* length -- a poorly mapped route is not
    penalised for what is unknown, it just carries less evidence
    (``known_share``).

    ``fit`` is the mean of the stated terms: the share of known length on
    preferred categories, and one minus the share on avoided categories.
    """
    preferred, _ = resolve_surface_tokens(constraints.prefer_surfaces)
    avoided, _ = resolve_surface_tokens(constraints.avoid_surfaces)
    coverage = candidate.metrics.surface_coverage
    known = sum(coverage.values())
    if not (preferred or avoided) or known <= 0:
        return None
    terms: list[float] = []
    if preferred:
        terms.append(sum(coverage.get(c, 0.0) for c in preferred) / known)
    if avoided:
        terms.append(1.0 - sum(coverage.get(c, 0.0) for c in avoided) / known)
    fit = min(1.0, max(0.0, sum(terms) / len(terms)))
    return fit, min(1.0, known)


def surface_active(breakdowns: Sequence[dict[str, float]], surface_weight: float) -> bool:
    """Whether the surface fit enters the score of this candidate set.

    All-or-nothing over the set so every candidate is scored by the same
    formula: one enriched and one unenriched candidate must not be ranked
    by different scales.
    """
    return (
        surface_weight > 0
        and bool(breakdowns)
        and all(
            "surface_fit" in b and b.get("surface_known_share", 0.0) >= MIN_KNOWN_SURFACE_SHARE
            for b in breakdowns
        )
    )


def weighted_score(
    breakdown: dict[str, float],
    *,
    distance_weight: float = DISTANCE_WEIGHT,
    elevation_weight: float = ELEVATION_WEIGHT,
    surface_weight: float = 0.0,
    use_surface: bool = False,
) -> float:
    """Combine breakdown components; shared with the calibration re-weighting."""
    total = (
        distance_weight * breakdown["distance_fit"]
        + elevation_weight * breakdown["elevation_fit"]
    )
    if use_surface:
        total = (total + surface_weight * breakdown["surface_fit"]) / (1 + surface_weight)
    return total - breakdown["warning_penalty"]


def score_candidates_together(
    candidates: Sequence[RouteCandidate],
    constraints: RouteConstraints,
    *,
    surface_weight: float | None = None,
) -> list[tuple[float, dict[str, float]]]:
    """Score a comparison set; the surface term applies to all or none of it."""
    weight = SURFACE_WEIGHT if surface_weight is None else surface_weight
    scored = [score_candidate(c, constraints) for c in candidates]
    if not surface_active([breakdown for _, breakdown in scored], weight):
        return scored
    out: list[tuple[float, dict[str, float]]] = []
    for _, breakdown in scored:
        breakdown = {**breakdown, "surface_weight_applied": weight}
        out.append(
            (weighted_score(breakdown, surface_weight=weight, use_surface=True), breakdown)
        )
    return out


def score_candidate(
    candidate: RouteCandidate,
    constraints: RouteConstraints,
) -> tuple[float, dict[str, float]]:
    """Score one candidate *without* the surface term (see score_candidates_together).

    The surface fit and the evidence behind it are still reported in the
    breakdown whenever they can be computed.
    """
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

    breakdown = {
        "distance_fit": distance_fit,
        "elevation_fit": elevation_fit,
        "warning_penalty": warning_penalty,
    }
    surface = surface_fit(candidate, constraints)
    if surface is not None:
        breakdown["surface_fit"], breakdown["surface_known_share"] = surface
    return score, breakdown


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
