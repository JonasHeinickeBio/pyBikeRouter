"""Multi-engine score calibration against the curated benchmark set (issue #4).

Runs every benchmark case through all configured routing engines (and the
OSM surface enricher, when enabled), then reports for each case:

* how each engine's candidate fares against the manually judged
  expectations (passed / failed / skipped -- skipped means "no data to
  judge", never a failure);
* the ranking under the current scorer weights;
* the ranking under the sensitivity weight grid -- a ranking that flips
  inside plausible weights is the signal to gather more evidence, not to
  retune a constant.

The script only *measures*: changing ``scoring/basic.py`` weights should be
a deliberate, evidenced decision informed by this report.

Usage (from the repo root; needs a working .env, and BRouter/Valhalla
containers for those engines to appear):

    poetry run python scripts/calibrate.py [--out calibration-report.json]
        [--benchmark benchmarks/core-v1.json] [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from bike_routing_agent.api import build_routing_providers, build_surface_enricher
from bike_routing_agent.calibration import (
    DEFAULT_BENCHMARK_PATH,
    BenchmarkCase,
    build_request,
    evaluate_candidate,
    evaluation_rows,
    load_benchmark,
    ranked_candidates,
    weight_sensitivity,
)
from bike_routing_agent.config import Settings
from bike_routing_agent.enrichment.base import SurfaceEnricher
from bike_routing_agent.errors import ProviderError
from bike_routing_agent.models import Coordinate, RouteCandidate
from bike_routing_agent.scoring.basic import score_candidate


def line_coordinates(candidate: RouteCandidate) -> list[Coordinate]:
    geom = candidate.geometry_geojson
    if geom.get("type") != "LineString":
        return []
    return [Coordinate(lon=c[0], lat=c[1]) for c in geom["coordinates"]]


async def _enrich(
    candidate: RouteCandidate, enricher: SurfaceEnricher | None
) -> RouteCandidate:
    """Attach the surface profile, or return the candidate untouched.

    Enrichment failure keeps the surface checks *skipped*, mirroring the
    production enrich node's policy.
    """
    if enricher is None:
        return candidate
    coordinates = line_coordinates(candidate)
    if len(coordinates) < 2:
        return candidate
    try:
        summary = await enricher.surface_profile(coordinates)
    except ProviderError:
        return candidate
    return candidate.model_copy(
        update={
            "metrics": candidate.metrics.model_copy(
                update={
                    "surface_coverage": dict(summary.coverage),
                    "unknown_surface_fraction": summary.unknown_fraction,
                }
            )
        }
    )


async def run_case(
    case: BenchmarkCase,
    providers: list[Any],
    enricher: SurfaceEnricher | None,
) -> dict[str, Any]:
    request = build_request(case)
    constraints = case.request.constraints
    candidates: list[RouteCandidate] = []
    provider_errors: dict[str, str] = {}
    for provider in providers:
        try:
            candidate = await provider.route(request)
        except ProviderError as exc:
            provider_errors[provider.name] = f"{type(exc).__name__}: {exc}"
            continue
        candidates.append(await _enrich(candidate, enricher))

    scored = [
        c.model_copy(update={"score": score_candidate(c, constraints)[0]})
        for c in candidates
    ]
    rows = evaluation_rows(case, scored)
    ranked = ranked_candidates(scored, constraints)
    top = ranked[0] if ranked else None
    top_evaluation = evaluate_candidate(case, top) if top else None
    return {
        "case": case.case_id,
        "title": case.title,
        "providers_queried": [p.name for p in providers],
        "provider_errors": provider_errors,
        "candidates": rows,
        "ranking": [c.provider for c in ranked],
        "top_provider": top.provider if top else None,
        "top_passes_judgement": bool(top_evaluation and top_evaluation.passed),
        "top_skipped_checks": (
            [c.name for c in top_evaluation.skipped] if top_evaluation else []
        ),
        "weight_sensitivity": weight_sensitivity(scored, constraints),
    }


def print_markdown(report: dict[str, Any]) -> None:
    print(f"\nBenchmark: {report['benchmark']} ({report['case_count']} cases)")
    print(
        "\n| case | engine | distance | ascent | unknown | verdict | failed | skipped |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for case in report["cases"]:
        for row in case["candidates"]:
            details = row["details"]
            verdict = "PASS" if row["passed"] else "FAIL"
            print(
                f"| {row['case']} | {row['provider']} | "
                f"{details.get('distance_m', '-')} | "
                f"{details.get('ascent_m', '-')} | "
                f"{details.get('unknown_surface_fraction', '-')} | {verdict} | "
                f"{', '.join(row['failed']) or '-'} | "
                f"{', '.join(row['skipped']) or '-'} |"
            )
        for engine, error in case["provider_errors"].items():
            print(f"| {case['case']} | {engine} | - | - | - | NO ROUTE | {error} | - |")
    for case in report["cases"]:
        if case["ranking"]:
            flip = len({tuple(order) for order in case["weight_sensitivity"].values()}) > 1
            flag = "  (weight-sensitive!)" if flip else ""
            print(
                f"{case['case']}: best={case['top_provider']} "
                f"passes_judgement={case['top_passes_judgement']} "
                f"ranking={case['ranking']}{flag}"
            )
    judged = sum(1 for c in report["cases"] if c["candidates"])
    good = sum(1 for c in report["cases"] if c["top_passes_judgement"])
    print(f"\n{good}/{judged} judged cases: top-ranked engine matches local judgement.")
    print(
        "Reminder: this report informs weights; it does not change "
        "scoring/basic.py by itself."
    )


async def main(benchmark_path: Path, out_path: str, limit: int | None) -> int:
    benchmark = load_benchmark(benchmark_path)
    settings = Settings()
    providers = build_routing_providers(
        settings.model_copy(update={"routing_provider": "all"})
    )
    enricher = build_surface_enricher(settings)
    print(
        f"engines: {[p.name for p in providers]} | enricher: "
        f"{enricher.name if enricher else 'disabled'}"
    )

    cases = benchmark.cases[:limit] if limit else benchmark.cases
    case_reports = [await run_case(c, providers, enricher) for c in cases]
    report: dict[str, Any] = {
        "benchmark": str(benchmark_path),
        "region": benchmark.region,
        "judged_by": benchmark.judged_by,
        "case_count": len(case_reports),
        "cases": case_reports,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)

    if not any(c["candidates"] for c in case_reports):
        print("no engine produced any candidate -- nothing to calibrate.", file=sys.stderr)
        return 1
    print_markdown(report)
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark",
        default=str(DEFAULT_BENCHMARK_PATH),
        help="benchmark JSON path (default: benchmarks/core-v1.json)",
    )
    parser.add_argument(
        "--out", default="calibration-report.json", help="JSON report path"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="run only the first N cases"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(Path(args.benchmark), args.out, args.limit)))
