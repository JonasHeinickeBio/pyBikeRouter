"""Ranking and de-duplication of route candidates (issue #24).

With several engines configured the same corridor often comes back more
than once: ORS, BRouter and Valhalla snap to the same streets and differ by a
few metres. Presenting those as "alternatives" is noise, so before ranking
this module recognises near-identical geometries and keeps the best-scored
member of each cluster.

Everything here is deterministic and explainable: ranking is the scorer's
order (ties: shorter, then name), "near-identical" is one metric with one
configurable threshold, and rationales are assembled from metric deltas
against the rank-1 route -- never from a model and never with safety claims.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from bike_routing_agent.models import RouteCandidate

DEFAULT_DEDUP_THRESHOLD_M = 50.0

# Both lines are resampled to this many points before comparison, which makes
# the metric independent of how densely each engine emits vertices.
_RESAMPLE_POINTS = 64
_EARTH_RADIUS_M = 6_371_008.8

_XY = tuple[float, float]


def candidate_label(candidate: RouteCandidate) -> str:
    return f"{candidate.provider}/{candidate.provider_profile}"


def _line_points(geometry: dict[str, Any]) -> list[tuple[float, float]]:
    """``(lon, lat)`` vertices of a (Multi)LineString, else empty."""
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    parts = coordinates if kind == "MultiLineString" else [coordinates]
    if kind not in ("LineString", "MultiLineString"):
        return []
    return [(float(p[0]), float(p[1])) for part in parts for p in part if len(p) >= 2]


def _project(points: Sequence[tuple[float, float]], lat0: float) -> list[_XY]:
    """Equirectangular metres around ``lat0`` -- ample at route scale."""
    cos_lat = math.cos(math.radians(lat0))
    return [
        (
            math.radians(lon) * _EARTH_RADIUS_M * cos_lat,
            math.radians(lat) * _EARTH_RADIUS_M,
        )
        for lon, lat in points
    ]


def _resample(points: Sequence[_XY], n: int) -> list[_XY]:
    """``n`` points evenly spaced by arc length along the polyline."""
    lengths = [0.0]
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        lengths.append(lengths[-1] + math.hypot(x1 - x0, y1 - y0))
    total = lengths[-1]
    if total == 0:
        return [points[0]] * n
    out: list[_XY] = []
    j = 0
    for i in range(n):
        target = total * i / (n - 1)
        while j < len(lengths) - 2 and lengths[j + 1] < target:
            j += 1
        span = lengths[j + 1] - lengths[j]
        t = 0.0 if span == 0 else (target - lengths[j]) / span
        (x0, y0), (x1, y1) = points[j], points[j + 1]
        out.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
    return out


def _discrete_frechet(a: Sequence[_XY], b: Sequence[_XY]) -> float:
    """Discrete Frechet distance (rolling-row dynamic programme)."""
    previous: list[float] = []
    for i, p in enumerate(a):
        current: list[float] = []
        for j, q in enumerate(b):
            d = math.hypot(p[0] - q[0], p[1] - q[1])
            if i == 0 and j == 0:
                best = d
            elif i == 0:
                best = max(current[j - 1], d)
            elif j == 0:
                best = max(previous[0], d)
            else:
                best = max(min(previous[j], previous[j - 1], current[j - 1]), d)
            current.append(best)
        previous = current
    return previous[-1]


def frechet_distance_m(a: RouteCandidate, b: RouteCandidate) -> float | None:
    """How far apart two candidates' routes are, in metres, or ``None``.

    ``None`` when either geometry is unusable (not a line, fewer than two
    vertices): then nothing can be said and the pair is never merged -- an
    unreadable route is not "the same as" anything.
    """
    pa = _line_points(a.geometry_geojson)
    pb = _line_points(b.geometry_geojson)
    if len(pa) < 2 or len(pb) < 2:
        return None
    lat0 = sum(lat for _, lat in (*pa, *pb)) / (len(pa) + len(pb))
    ra = _resample(_project(pa, lat0), _RESAMPLE_POINTS)
    rb = _resample(_project(pb, lat0), _RESAMPLE_POINTS)
    return _discrete_frechet(ra, rb)


def _order_key(c: RouteCandidate) -> tuple[float, float, str, str]:
    score = c.score if c.score is not None else float("-inf")
    return (-score, c.metrics.distance_m, c.provider, c.provider_profile)


def _signed(value: float, unit: str, more: str, less: str, digits: int = 0) -> str | None:
    if round(abs(value), digits) == 0:
        return None
    return f"{abs(value):.{digits}f} {unit} {more if value > 0 else less}"


def _rationale(
    rank: int,
    candidate: RouteCandidate,
    best: RouteCandidate,
    total: int,
    duplicate_of: str | None,
    threshold_m: float,
) -> str:
    if duplicate_of is not None:
        return (
            f"rank {rank}: near-identical to {duplicate_of} (within {threshold_m:.0f} m); "
            "not a distinct alternative"
        )
    if rank == 1:
        if candidate.score is None:
            return "rank 1: only candidate" if total == 1 else "rank 1: first candidate"
        return (
            "rank 1: only candidate"
            if total == 1
            else f"rank 1: highest score ({candidate.score:.2f})"
        )
    parts: list[str] = []
    if candidate.score is not None and best.score is not None:
        gap = best.score - candidate.score
        parts.append(f"score {candidate.score:.2f} ({gap:.2f} below rank 1)")
    distance_km = (candidate.metrics.distance_m - best.metrics.distance_m) / 1000
    distance = _signed(distance_km, "km", "longer", "shorter", 1)
    if distance:
        parts.append(distance)
    if candidate.metrics.ascent_m is not None and best.metrics.ascent_m is not None:
        ascent = _signed(
            candidate.metrics.ascent_m - best.metrics.ascent_m, "m", "more ascent", "less ascent"
        )
        if ascent:
            parts.append(ascent)
    detail = "; ".join(parts) if parts else "comparable to rank 1"
    return f"rank {rank}: {detail}"


def rank_candidates(
    candidates: Sequence[RouteCandidate],
    *,
    max_alternatives: int | None = None,
    dedup_threshold_m: float = DEFAULT_DEDUP_THRESHOLD_M,
) -> list[RouteCandidate]:
    """Rank scored candidates and mark near-identical ones.

    Order is best score first (ties: shorter, then provider/profile);
    unscored candidates last. Walking that order, a candidate within
    ``dedup_threshold_m`` (discrete Frechet distance) of an already kept one
    is its duplicate: the kept route lists it under ``duplicates``.

    ``max_alternatives=None`` keeps today's behaviour -- every candidate is
    returned, duplicates included but annotated (``duplicate_of``) so nothing
    changes for existing clients. With a number, duplicates are dropped and
    the list is capped, which is what "give me N distinct alternatives"
    means. Ranks are 1..n over what is returned, so ``result[0]`` is always
    rank 1 and is never a duplicate: the best-scored member of a cluster is
    the one kept.
    """
    ordered = sorted(candidates, key=_order_key)
    kept: list[RouteCandidate] = []
    duplicate_of: dict[int, str] = {}
    duplicates_of_kept: dict[int, list[str]] = {}
    for candidate in ordered:
        match = next(
            (
                k
                for k in kept
                if (d := frechet_distance_m(candidate, k)) is not None and d <= dedup_threshold_m
            ),
            None,
        )
        if match is None:
            kept.append(candidate)
        else:
            duplicate_of[id(candidate)] = candidate_label(match)
            duplicates_of_kept.setdefault(id(match), []).append(candidate_label(candidate))

    if max_alternatives is None:
        result = list(ordered)
    else:
        result = kept[: max(1, max_alternatives)]

    if not result:
        return []
    best = result[0]
    total = len(result)
    ranked: list[RouteCandidate] = []
    for rank, candidate in enumerate(result, start=1):
        dup = duplicate_of.get(id(candidate))
        ranked.append(
            candidate.model_copy(
                update={
                    "rank": rank,
                    "rank_rationale": _rationale(
                        rank, candidate, best, total, dup, dedup_threshold_m
                    ),
                    "duplicate_of": dup,
                    "duplicates": duplicates_of_kept.get(id(candidate), []),
                }
            )
        )
    return ranked
