"""Short, factual pros and cons of each route against the other distinct ones.

Deterministic and relative: a route is "shortest" only if the others are
measurably longer, and every line states a number, never a verdict about what is
good for the rider and never a safety claim. Metrics a route does not have are
skipped, so unknown never looks favourable (no main-road share for an engine that
gives no tags, no weather for an unforecast route). With a single distinct route
there is nothing to compare, so the lists stay empty.

Dimensions, in priority order (at most ``MAX_LINES`` each): the rider's own limits
and target, distance, time, climbing, main roads without a bike lane, headwind,
precipitation. Thresholds keep noise out: a difference must be both relative and
absolute before it is worth a line.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from bike_routing_agent.models import RouteCandidate, RouteConstraints

MAX_LINES = 3
# A difference is stated only when it clears both bars.
DISTANCE_REL, DISTANCE_ABS_KM = 0.05, 0.3
TIME_REL, TIME_ABS_MIN = 0.05, 2.0
ASCENT_REL, ASCENT_ABS_M = 0.20, 15.0
MAIN_ROAD_PP = 0.10  # percentage points, as a fraction
HEADWIND_KMH = 5.0
TARGET_REL = 0.10


def _label(candidate: RouteCandidate) -> str:
    return f"{candidate.provider}/{candidate.provider_profile}"


def _headwind(candidate: RouteCandidate) -> float | None:
    weather = candidate.weather
    return weather.summary.headwind_mean_kmh if weather is not None else None


def _wet(candidate: RouteCandidate) -> bool | None:
    weather = candidate.weather
    return None if weather is None else weather.summary.wet_stretch is not None


def _limits(c: RouteCandidate, constraints: RouteConstraints) -> list[str]:
    cons: list[str] = []
    ascent = c.metrics.ascent_m
    if constraints.max_ascent_m is not None and ascent is not None:
        if ascent > constraints.max_ascent_m:
            cons.append(
                f"Over your climbing limit ({ascent:.0f} m > {constraints.max_ascent_m:.0f} m)"
            )
    if constraints.max_distance_km is not None:
        km = c.metrics.distance_m / 1000
        if km > constraints.max_distance_km:
            cons.append(
                f"Over your distance limit ({km:.1f} km > {constraints.max_distance_km:g} km)"
            )
    return cons


def annotate_pros_cons(
    candidates: Sequence[RouteCandidate], constraints: RouteConstraints
) -> list[RouteCandidate]:
    """``candidates`` with ``pros``/``cons`` filled in for the distinct ones."""
    distinct = [i for i, c in enumerate(candidates) if c.duplicate_of is None]
    pros: dict[int, list[str]] = {i: [] for i in distinct}
    cons: dict[int, list[str]] = {i: [] for i in distinct}
    if len(distinct) >= 2:
        _compare(candidates, distinct, constraints, pros, cons)
    return [
        c.model_copy(
            update={
                "pros": pros.get(i, [])[:MAX_LINES],
                "cons": cons.get(i, [])[:MAX_LINES],
            }
        )
        for i, c in enumerate(candidates)
    ]


def _compare(
    candidates: Sequence[RouteCandidate],
    distinct: list[int],
    constraints: RouteConstraints,
    pros: dict[int, list[str]],
    cons: dict[int, list[str]],
) -> None:
    for i in distinct:
        cons[i].extend(_limits(candidates[i], constraints))

    target = constraints.target_distance_km
    if target is not None:
        gaps = {i: abs(candidates[i].metrics.distance_m / 1000 - target) for i in distinct}
        closest = min(gaps, key=gaps.__getitem__)
        rest = [g for i, g in gaps.items() if i != closest]
        if all(g - gaps[closest] >= target * TARGET_REL for g in rest):
            km = candidates[closest].metrics.distance_m / 1000
            pros[closest].append(f"Closest to your {target:g} km target ({km:.1f} km)")

    def numeric(
        get: Callable[[RouteCandidate], float | None],
        *,
        rel: float,
        absolute: float,
        pro: Callable[[float], str],
        con: Callable[[float, float], str],
        scale: float = 1.0,
    ) -> None:
        """Lower is better. The leader gets a pro only if every other route trails it
        clearly; each route that trails clearly gets a con with its own gap."""
        values = {i: v / scale for i in distinct if (v := get(candidates[i])) is not None}
        if len(values) < 2:
            return
        low = min(values.values())
        leaders = [i for i, v in values.items() if v == low]
        clear = {
            i: (v - low) >= absolute and (low == 0 or (v - low) / abs(low) >= rel)
            for i, v in values.items()
        }
        if len(leaders) == 1 and all(clear[i] for i in values if i != leaders[0]):
            pros[leaders[0]].append(pro(low))
        for i, v in values.items():
            if i not in leaders and clear[i]:
                cons[i].append(con(v - low, v))

    numeric(
        lambda c: c.metrics.distance_m,
        rel=DISTANCE_REL, absolute=DISTANCE_ABS_KM, scale=1000.0,
        pro=lambda v: f"Shortest ({v:.1f} km)",
        con=lambda gap, v: f"{gap:.1f} km longer than the shortest",
    )  # fmt: skip
    numeric(
        lambda c: c.metrics.duration_s,
        rel=TIME_REL, absolute=TIME_ABS_MIN, scale=60.0,
        pro=lambda v: f"Fastest ({v:.0f} min)",
        con=lambda gap, v: f"{gap:.0f} min slower than the fastest",
    )  # fmt: skip
    numeric(
        lambda c: c.metrics.ascent_m,
        rel=ASCENT_REL, absolute=ASCENT_ABS_M,
        pro=lambda v: f"Least climbing ({v:.0f} m)",
        con=lambda gap, v: f"{gap:.0f} m more climbing than the flattest",
    )  # fmt: skip
    numeric(
        lambda c: c.metrics.main_road_share,
        rel=0.0, absolute=MAIN_ROAD_PP,
        pro=lambda v: f"Least on main roads without a bike lane ({v * 100:.0f}%)",
        con=lambda gap, v: f"{v * 100:.0f}% on main roads without a bike lane",
    )  # fmt: skip
    numeric(
        _headwind,
        rel=0.0, absolute=HEADWIND_KMH,
        pro=lambda v: "Least headwind" if v > 0 else "Most tailwind",
        con=lambda gap, v: f"{gap:.0f} km/h more headwind on average",
    )  # fmt: skip

    wet = {i: w for i in distinct if (w := _wet(candidates[i])) is not None}
    if len(set(wet.values())) == 2:
        for i, is_wet in wet.items():
            (cons if is_wet else pros)[i].append(
                "Precipitation forecast along it" if is_wet else "Dry along the route"
            )
