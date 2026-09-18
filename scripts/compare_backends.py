"""Real-world comparison of the ORS public API vs a local BRouter server.

Uses the repo's own adapters, so profile maps, request normalisation and
error semantics are identical to production traffic. Requires a working
`.env` (ORS key) and a BRouter container with segments (docker/brouter).

Usage (from the repo root):

    poetry run python scripts/compare_backends.py [--out backend-compare.json]

Findings write-up: docs/providers-comparison.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from statistics import median
from typing import Any

from bike_routing_agent.api import build_routing_provider
from bike_routing_agent.config import BROUTER_PROFILE_MAP, ORS_PROFILE_MAP, Settings
from bike_routing_agent.models import (
    Coordinate,
    RouteCandidate,
    RouteConstraints,
    RoutingRequest,
)

CASES = {
    "bs-city": (
        "Braunschweig Hbf -> Bürgerpark (city hop)",
        (10.5267132, 52.2689081),
        (10.5410, 52.2735),
    ),
    "bs-wf": (
        "Braunschweig Hbf -> Schloss Wolfenbüttel (tour)",
        (10.5267132, 52.2689081),
        (10.5361, 52.1688),
    ),
    "harz": (
        "Bad Harzburg Bhf -> Braunlage Marktplatz (Harz climb)",
        (10.5555, 51.8883),
        (10.5816, 51.7269),
    ),
}
BIKE_TYPES = ["road", "gravel", "touring", "mountain", "city", "ebike", "commuter", "recumbent"]


def line_coords(candidate: RouteCandidate) -> list[tuple[float, float]]:
    geom = candidate.geometry_geojson
    if geom["type"] == "LineString":
        return [(c[0], c[1]) for c in geom["coordinates"]]
    return []


def densify(
    line: list[tuple[float, float]], max_gap_deg: float = 0.0005
) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in zip(line, line[1:], strict=False):
        out.append(a)
        steps = int(max(abs(a[0] - b[0]), abs(a[1] - b[1])) / max_gap_deg)
        out.extend(
            (a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps)
            for k in range(1, steps)
        )
    if line:
        out.append(line[-1])
    return out


def point_to_polyline_m(lon: float, lat: float, line: list[tuple[float, float]]) -> float:
    best = math.inf
    for (lon1, lat1), (lon2, lat2) in zip(line, line[1:], strict=False):
        # planar approximation is fine over <100 m distances in mid-Europe
        ax, ay = (lon1 - lon) * 60_000, (lat1 - lat) * 111_000
        bx, by = (lon2 - lon) * 60_000, (lat2 - lat) * 111_000
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length2))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def agreement(ors: RouteCandidate, br: RouteCandidate) -> dict[str, float]:
    a = densify(line_coords(ors))
    b = densify(line_coords(br))
    if not a or not b:
        return {"median_dev_m": -1.0, "p95_dev_m": -1.0, "shared_50m_pct": -1.0}
    devs = sorted(
        [point_to_polyline_m(x, y, b) for x, y in a] + [point_to_polyline_m(x, y, a) for x, y in b]
    )
    return {
        "median_dev_m": round(median(devs), 1),
        "p95_dev_m": round(devs[int(len(devs) * 0.95) - 1], 1),
        "shared_50m_pct": round(100 * sum(d <= 50 for d in devs) / len(devs), 1),
    }


async def run_case(
    ors_router: Any,
    br_router: Any,
    case_id: str,
    origin: tuple[float, float],
    dest: tuple[float, float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bike_type in BIKE_TYPES:
        request = RoutingRequest(
            origin=Coordinate(lon=origin[0], lat=origin[1]),
            destination=Coordinate(lon=dest[0], lat=dest[1]),
            constraints=RouteConstraints(bike_type=bike_type),
        )
        ors_cand = await ors_router.route(request)
        br_cand = await br_router.route(request)
        om, bm = ors_cand.metrics, br_cand.metrics
        rows.append(
            {
                "case": case_id,
                "bike_type": bike_type,
                "ors_profile": ors_cand.provenance["profile"],
                "br_profile": br_cand.provenance["profile"],
                "ors_dist_km": round(om.distance_m / 1000, 2),
                "br_dist_km": round(bm.distance_m / 1000, 2),
                "dist_diff_pct": round(100 * (bm.distance_m - om.distance_m) / om.distance_m, 1),
                "ors_dur_min": round(om.duration_s / 60, 1),
                "br_dur_min": round(bm.duration_s / 60, 1),
                "ors_ascent": om.ascent_m,
                "br_ascent": bm.ascent_m,
                **agreement(ors_cand, br_cand),
            }
        )
        r = rows[-1]
        print(
            f"{case_id}/{bike_type}: ORS {r['ors_dist_km']}km BRouter {r['br_dist_km']}km "
            f"diff {r['dist_diff_pct']}% shared {r['shared_50m_pct']}%",
            flush=True,
        )
    return rows


def print_markdown(results: list[dict[str, Any]]) -> None:
    print("\n| case | bike | ORS km/min/asc | BRouter km/min/asc | Δdist | Δdur | dev m | shared |")
    print("|---|---|---|---|---|---|---|---|")
    for r in results:
        print(
            f"| {r['case']} | {r['bike_type']} | {r['ors_profile']} {r['ors_dist_km']}/"
            f"{r['ors_dur_min']}/{r['ors_ascent']} | {r['br_profile']} {r['br_dist_km']}/"
            f"{r['br_dur_min']}/{r['br_ascent']} | {r['dist_diff_pct']}% | "
            f"{round(r['br_dur_min'] - r['ors_dur_min'], 1)} min | "
            f"{r['median_dev_m']}/{r['p95_dev_m']} | {r['shared_50m_pct']}% |"
        )
    ors_profiles = {ORS_PROFILE_MAP[t] for t in BIKE_TYPES}
    br_profiles = {BROUTER_PROFILE_MAP[t] for t in BIKE_TYPES}
    print(f"\ndistinct ORS profiles: {len(ors_profiles)} {sorted(ors_profiles)}")
    print(f"distinct BRouter profiles: {len(br_profiles)} {sorted(br_profiles)}")


async def main(out_path: str) -> None:
    settings = Settings()
    ors_router = build_routing_provider(settings.model_copy(update={"routing_provider": "ors"}))
    br_router = build_routing_provider(settings.model_copy(update={"routing_provider": "brouter"}))

    results: list[dict[str, Any]] = []
    for case_id, (_title, origin, dest) in CASES.items():
        results.extend(await run_case(ors_router, br_router, case_id, origin, dest))

    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=1)
    print(f"\nwrote {out_path}")
    print_markdown(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="backend-compare.json", help="JSON output path")
    args = parser.parse_args()
    asyncio.run(main(args.out))
