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

A traffic fit (issue #23) follows the same discipline: it comes from the
engine-estimated ``traffic_index`` (BRouter's road tags), enters the score
only while the request asks to avoid high-traffic roads *and* every
candidate in the comparison carries an index, and is never applied to some
candidates and not others. A route much longer than the shortest one gets
no credit for being quiet, so quietness cannot buy an absurd detour (the
route optimiser, docs/optimizer.md, is where such trade-offs are chosen on
purpose).
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

# Weight of the traffic fit, in the same terms as SURFACE_WEIGHT. Chosen from
# the evidence in docs/scoring-and-exports.md ("Traffic weight: evidence"):
# on eight real trips (the stored candidate sets, BRouter profiles) every
# weight from 0.4 to 1.0 changes the same single verdict -- a road-bike trip
# where the commuter profile is shorter *and* far quieter than ``fastbike`` --
# and no weight changed another one. 0.5 sits inside that stable range. Set
# it to 0.0 to switch the component off (it is still reported in
# ``score_breakdown``).
TRAFFIC_WEIGHT = 0.5

# The traffic index (0 = no exposure to cars, see brouter_tags.traffic_summary)
# at which the fit reaches zero. The measured indices run 0.00-0.35: quiet
# profile routes 0.00-0.12, ``fastbike`` on main roads 0.23-0.35.
TRAFFIC_INDEX_AT_ZERO_FIT = 0.4

# A candidate this much longer than the shortest one in the comparison gets no
# credit for low traffic (its fit is 0). The longest alternative measured was
# 38 % longer.
MAX_TRAFFIC_DETOUR = 0.5


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


def traffic_fit(candidate: RouteCandidate, constraints: RouteConstraints) -> float | None:
    """How little traffic the route has, 0..1 (1 = none), or ``None`` if there is nothing to judge.

    Only when the request asks to avoid high-traffic roads, and only from the engine's own
    estimate (``traffic_index``): a candidate without one -- another engine, or tags that were
    not returned -- is unknown, never "quiet".
    """
    index = candidate.metrics.traffic_index
    if not constraints.avoid_high_traffic_roads or index is None:
        return None
    return min(1.0, max(0.0, 1.0 - index / TRAFFIC_INDEX_AT_ZERO_FIT))


def traffic_active(breakdowns: Sequence[dict[str, float]], traffic_weight: float) -> bool:
    """Whether the traffic fit enters the score of this candidate set (all or nothing, like
    the surface fit: one candidate without an estimate must not be ranked on another scale)."""
    return traffic_weight > 0 and bool(breakdowns) and all("traffic_fit" in b for b in breakdowns)


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
    traffic_weight: float = 0.0,
    use_traffic: bool = False,
) -> float:
    """Combine breakdown components; shared with the calibration re-weighting.

    Active components are weighted against the 1.0 carried by distance + elevation and the sum
    is divided by the total weight, so the score stays in [0, 1] before the warning penalty.
    """
    total = (
        distance_weight * breakdown["distance_fit"] + elevation_weight * breakdown["elevation_fit"]
    )
    weight = 1.0
    if use_surface:
        total += surface_weight * breakdown["surface_fit"]
        weight += surface_weight
    if use_traffic:
        total += traffic_weight * breakdown["traffic_fit"]
        weight += traffic_weight
    if weight != 1.0:
        total /= weight
    return total - breakdown["warning_penalty"]


def cap_traffic_detours(
    scored: list[tuple[float, dict[str, float]]], distances_m: Sequence[float]
) -> list[dict[str, float]]:
    """The breakdowns with the traffic fit zeroed for any route much longer than the shortest."""
    shortest = min(distances_m) if distances_m else 0.0
    out: list[dict[str, float]] = []
    for (_, breakdown), distance in zip(scored, distances_m, strict=True):
        if (
            "traffic_fit" in breakdown
            and shortest > 0
            and distance > shortest * (1 + MAX_TRAFFIC_DETOUR)
        ):
            breakdown = {**breakdown, "traffic_fit": 0.0, "traffic_detour_capped": 1.0}
        out.append(breakdown)
    return out


def score_candidates_together(
    candidates: Sequence[RouteCandidate],
    constraints: RouteConstraints,
    *,
    surface_weight: float | None = None,
    traffic_weight: float | None = None,
) -> list[tuple[float, dict[str, float]]]:
    """Score a comparison set; each optional term applies to all of it or none of it."""
    s_weight = SURFACE_WEIGHT if surface_weight is None else surface_weight
    t_weight = TRAFFIC_WEIGHT if traffic_weight is None else traffic_weight
    scored = [score_candidate(c, constraints) for c in candidates]
    breakdowns = cap_traffic_detours(scored, [c.metrics.distance_m for c in candidates])
    use_surface = surface_active(breakdowns, s_weight)
    use_traffic = traffic_active(breakdowns, t_weight)
    if not (use_surface or use_traffic):
        return [
            (score, breakdown) for (score, _), breakdown in zip(scored, breakdowns, strict=True)
        ]
    out: list[tuple[float, dict[str, float]]] = []
    for breakdown in breakdowns:
        applied = dict(breakdown)
        if use_surface:
            applied["surface_weight_applied"] = s_weight
        if use_traffic:
            applied["traffic_weight_applied"] = t_weight
        score = weighted_score(
            applied,
            surface_weight=s_weight,
            use_surface=use_surface,
            traffic_weight=t_weight,
            use_traffic=use_traffic,
        )
        out.append((score, applied))
    return out


def score_candidate(
    candidate: RouteCandidate,
    constraints: RouteConstraints,
) -> tuple[float, dict[str, float]]:
    """Score one candidate *without* the surface and traffic terms (see score_candidates_together).

    The surface and traffic fits (and the evidence behind them) are still reported in the
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
    traffic = traffic_fit(candidate, constraints)
    if traffic is not None:
        breakdown["traffic_fit"] = traffic
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
