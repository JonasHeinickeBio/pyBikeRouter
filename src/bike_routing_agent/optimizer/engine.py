"""Choose, from several candidate routes, the one that serves the goals best.

Deterministic and explainable -- no model, no randomness:

1. Every candidate's value for every goal metric is read from its ``RouteMetrics``.
2. A value becomes a *goodness* between 0 and 1 relative to the best value among the candidates:
   1 for the best, falling linearly to 0 at a per-metric distance from it ("zero at 0.30 more
   traffic index", "at 40 points less paved", "at 25 % more time"). Scaling against the best
   rather than the worst keeps one absurd candidate from flattening everyone else's
   differences.
3. The utility is the weighted mean of the goodness values. A candidate missing a value gets
   0 for that goal (never the benefit of the doubt); a goal nobody has a value for is dropped
   and reported.
4. Candidates more than ``max_detour_pct`` longer than the shortest one -- or beyond the
   request's own hard limits -- cannot win while any candidate is within them.
5. The highest utility wins; ties go to the shorter route. The Pareto-optimal candidates (no
   other one is at least as good everywhere and better somewhere) are marked.

The returned report carries the numbers behind the choice so it can be checked.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from bike_routing_agent.models import OptimizeSpec, RouteCandidate, RouteConstraints

Direction = Literal["min", "max"]


@dataclass(frozen=True)
class Metric:
    name: str
    direction: Direction
    label: str  # "less traffic": what the goal is called in a sentence
    value: Callable[[RouteCandidate], float | None]
    # How far from the best value a candidate may be before its goodness reaches 0.
    zero_at: Callable[[float], float]
    show: Callable[[float], str]


def _share(candidate: RouteCandidate, key: str) -> float | None:
    shares = candidate.metrics.engine_surface_shares
    return shares.get(key) if shares else None


def _band(candidate: RouteCandidate, key: str) -> float | None:
    shares = candidate.metrics.traffic_shares
    return shares.get(key, 0.0) if shares else None


def _pct(x: float) -> str:
    return f"{round(x * 100)} %"


METRICS: dict[str, Metric] = {
    m.name: m
    for m in (
        Metric(
            "traffic", "min", "less traffic", lambda c: c.metrics.traffic_index,
            lambda best: 0.30, lambda v: f"traffic index {v:.2f}",
        ),
        Metric(
            "busy_roads", "min", "fewer busy roads", lambda c: _band(c, "busy"),
            lambda best: 0.30, lambda v: f"{_pct(v)} busy roads",
        ),
        Metric(
            "paved", "max", "more asphalt", lambda c: _share(c, "paved"),
            lambda best: 0.40, lambda v: f"{_pct(v)} paved",
        ),
        Metric(
            "unpaved", "max", "more off-road", lambda c: _share(c, "unpaved"),
            lambda best: 0.40, lambda v: f"{_pct(v)} unpaved",
        ),
        Metric(
            "cobbles", "min", "fewer cobbles", lambda c: _share(c, "cobbles"),
            lambda best: 0.15, lambda v: f"{_pct(v)} cobbles",
        ),
        Metric(
            "distance", "min", "shorter", lambda c: c.metrics.distance_m,
            lambda best: max(2000.0, 0.25 * best), lambda v: f"{v / 1000:.1f} km",
        ),
        Metric(
            "duration", "min", "faster", lambda c: c.metrics.duration_s,
            lambda best: max(900.0, 0.25 * best), lambda v: f"{v / 3600:.1f} h",
        ),
        Metric(
            "pois", "max", "more sights along the route", lambda c: c.metrics.poi_count,
            lambda best: max(3.0, 0.5 * best),
            lambda v: f"{round(v)} well-known place{'s' if round(v) != 1 else ''} nearby",
        ),
        Metric(
            "ascent", "min", "less climbing", lambda c: c.metrics.ascent_m,
            lambda best: max(100.0, 0.5 * best), lambda v: f"{v:.0f} m of climbing",
        ),
    )
}  # fmt: skip
# "unpaved" is the one goal that is maximised although the word reads like a cost: it is for
# people who want off-road riding (an OptimizeSpec forbids it together with "paved").


def label(candidate: RouteCandidate) -> str:
    """ "brouter/trekking", or "brouter/trekking (2nd route)" for one of BRouter's own others."""
    source = candidate.provenance.get("optimizer_source")
    base = f"{candidate.provider}/{candidate.provider_profile}"
    return f"{base} ({source})" if source and source != "profile" else base


def goal_phrase(spec: OptimizeSpec) -> str:
    """ "less traffic and more asphalt" -- the goals in the order given."""
    names = [METRICS[g.metric].label for g in spec.goals]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


@dataclass
class Outcome:
    ordered: list[RouteCandidate]  # rank 1 first; duplicates and infeasible ones last
    report: dict[str, Any]


def _goodness(values: dict[int, float | None], metric: Metric) -> dict[int, float]:
    present = [v for v in values.values() if v is not None]
    if not present:
        return {i: 0.0 for i in values}
    best = min(present) if metric.direction == "min" else max(present)
    zero = metric.zero_at(abs(best))
    out: dict[int, float] = {}
    for i, v in values.items():
        if v is None:
            out[i] = 0.0  # no data is never the benefit of the doubt
        else:
            behind = (v - best) if metric.direction == "min" else (best - v)
            out[i] = max(0.0, min(1.0, 1.0 - behind / zero))
    return out


def optimize(
    candidates: list[RouteCandidate],
    spec: OptimizeSpec,
    constraints: RouteConstraints | None = None,
) -> Outcome:
    """Order ``candidates`` for ``spec`` and say why (see the module docstring)."""
    pool_idx = [i for i, c in enumerate(candidates) if not c.duplicate_of]
    if not pool_idx:
        pool_idx = list(range(len(candidates)))
    dup_idx = [i for i in range(len(candidates)) if i not in pool_idx]
    pool = {i: candidates[i] for i in pool_idx}

    # --- values and goodness ---------------------------------------------------------------
    active: list[tuple[Any, Metric, dict[int, float | None], dict[int, float]]] = []
    dropped: list[dict[str, str]] = []
    for goal in spec.goals:
        metric = METRICS[goal.metric]
        values = {i: metric.value(c) for i, c in pool.items()}
        if all(v is None for v in values.values()):
            reason = (
                "the POI lookup was not available, so nothing could be counted"
                if goal.metric == "pois"
                else "no candidate has data for it (engine gave none)"
            )
            dropped.append({"metric": goal.metric, "reason": reason})
            continue
        active.append((goal, metric, values, _goodness(values, metric)))

    # --- hard limits -----------------------------------------------------------------------
    distances = {i: c.metrics.distance_m for i, c in pool.items()}
    shortest = min(distances.values())
    detour_cap = shortest * (1 + spec.max_detour_pct / 100)
    ok = {i for i in pool if distances[i] <= detour_cap + 1e-6}
    limits: list[str] = []
    if constraints is not None:
        within = set(ok)
        if constraints.max_distance_km is not None:
            within = {i for i in within if distances[i] <= constraints.max_distance_km * 1000}
            limits.append(f"at most {constraints.max_distance_km:g} km")
        if constraints.max_ascent_m is not None:
            within = {
                i
                for i in within
                if pool[i].metrics.ascent_m is None
                or pool[i].metrics.ascent_m <= constraints.max_ascent_m  # type: ignore[operator]
            }
            limits.append(f"at most {constraints.max_ascent_m:g} m of climbing")
        if within:
            ok = within
        else:
            limits.append("(no route met them, so they were not applied)")
    feasible = ok

    # --- utility, Pareto, order --------------------------------------------------------------
    total_weight = sum(goal.weight for goal, *_ in active)
    utility: dict[int, float] = {}
    for i in pool:
        if not active:
            utility[i] = 0.0
            continue
        utility[i] = sum(goal.weight * good[i] for goal, _, _, good in active) / total_weight
    vectors = {i: [good[i] for *_, good in active] for i in pool}

    def dominated(i: int) -> bool:
        return any(
            j != i
            and all(a >= b for a, b in zip(vectors[j], vectors[i], strict=True))
            and any(a > b for a, b in zip(vectors[j], vectors[i], strict=True))
            for j in feasible
        )

    pareto = {i for i in feasible if active and not dominated(i)}

    def key(i: int) -> tuple[float, float, int]:
        return (-utility[i], distances[i], i)

    if active:
        order = sorted(feasible, key=key) + sorted(set(pool) - feasible, key=key)
    else:  # nothing to optimise on: keep the scorer's order
        order = [i for i in pool_idx if i in feasible] + [i for i in pool_idx if i not in feasible]
    winner = order[0]

    # --- the report ------------------------------------------------------------------------------
    def row(rank: int, i: int) -> dict[str, Any]:
        c = pool[i]
        return {
            "rank": rank,
            "label": label(c),
            "provider": c.provider,
            "profile": c.provider_profile,
            "source": c.provenance.get("optimizer_source") or "main",
            "distance_km": round(distances[i] / 1000, 2),
            "detour_pct": round((distances[i] / shortest - 1) * 100, 1),
            "values": {
                goal.metric: (None if (v := values[i]) is None else round(v, 4))
                for goal, _, values, _ in active
            },
            "shown": {
                goal.metric: ("no data" if (v := values[i]) is None else metric.show(v))
                for goal, metric, values, _ in active
            },
            "goodness": {goal.metric: round(good[i], 3) for goal, _, _, good in active},
            "utility": round(utility[i], 3),
            "pareto": i in pareto,
            "feasible": i in feasible,
            "selected": i == winner,
        }

    table = [row(rank, i) for rank, i in enumerate(order, start=1)]
    best_of = {
        goal.metric: (min if metric.direction == "min" else max)(
            [v for v in values.values() if v is not None]
        )
        for goal, metric, values, _ in active
    }
    won = pool[winner]
    facts: list[str] = []
    for goal, metric, values, _ in active:
        v = values[winner]
        if v is None:
            facts.append(f"no {goal.metric} data")
            continue
        ref = values[min(pool, key=lambda i: distances[i])]
        note = (
            "the best of the routes compared"
            if v == best_of[goal.metric]
            else (f"the best was {metric.show(best_of[goal.metric])}")
        )
        short = (
            f"; the shortest route had {metric.show(ref)}"
            if ref is not None and ref != v and goal.metric != "duration"
            else ""
        )
        facts.append(f"{metric.show(v)} ({note}{short})")
    detour_m = distances[winner] - shortest
    length = (
        "the shortest route found"
        if detour_m < 50
        else f"{detour_m / 1000:.1f} km ({(distances[winner] / shortest - 1) * 100:.0f} %) "
        "longer than the shortest route found"
    )
    summary = (
        f"Compared {len(candidates)} routes ({len(pool)} distinct) for {goal_phrase(spec)}"
        + (
            f"; no data for {', '.join(d['metric'] for d in dropped)}, so that goal was skipped"
            if dropped
            else ""
        )
        + f". Best fit: {label(won)} -- "
        + ("; ".join(facts) if facts else "no goal had data, so the usual ranking stands")
        + f"; {length}."
    )
    tradeoffs: list[str] = []
    for _goal, metric, values, _ in active:
        have = {i: v for i, v in values.items() if v is not None}
        leader = min(have, key=lambda i: (have[i] if metric.direction == "min" else -have[i], i))
        if leader == winner or have.get(winner) == have[leader]:
            continue
        cost_km = (distances[leader] - distances[winner]) / 1000
        way = f"{abs(cost_km):.1f} km {'longer' if cost_km > 0 else 'shorter'}"
        line = f"{label(pool[leader])} has {metric.show(have[leader])} ({metric.label})"
        tradeoffs.append(
            f"{line} but is {way} and lower on the other goals" if len(active) > 1 else line
        )
    report = {
        "goals": [
            {
                "metric": goal.metric,
                "weight": goal.weight,
                "direction": metric.direction,
                "label": metric.label,
            }
            for goal, metric, *_ in active
        ],
        "dropped_goals": dropped,
        "considered": len(candidates),
        "distinct": len(pool),
        "feasible": len(feasible),
        "max_detour_pct": spec.max_detour_pct,
        "hard_limits": limits,
        "shortest_km": round(shortest / 1000, 2),
        "selected": label(won),
        "summary": summary,
        "tradeoffs": tradeoffs,
        "table": table,
    }

    # --- candidates back, in the new order, with ranks --------------------------------------------
    ranked_idx = [*order, *dup_idx]
    ordered: list[RouteCandidate] = []
    for rank, i in enumerate(ranked_idx, start=1):
        c = candidates[i]
        if i in pool:
            r = f"optimizer: utility {utility[i]:.2f} for {goal_phrase(spec)}"
            if i not in feasible:
                r += " (over the detour or hard limits)"
            elif i == winner:
                r += " -- the best fit"
            elif i in pareto:
                r += " -- a different trade-off, not worse on every goal"
        else:
            r = "near-identical to a better route"
        ordered.append(c.model_copy(update={"rank": rank, "rank_rationale": r}))
    return Outcome(ordered=ordered, report=report)
