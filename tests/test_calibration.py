"""Offline tests for the calibration harness (issue #4).

No network: the curated benchmark file is validated, and synthetic
candidates are built to exercise every judgement the file encodes.
"""

import json
from pathlib import Path

import pytest

from bike_routing_agent.calibration import (
    DEFAULT_BENCHMARK_PATH,
    SURFACE_CATEGORIES,
    BenchmarkSet,
    build_request,
    evaluate_candidate,
    load_benchmark,
    ranked_candidates,
    weight_sensitivity,
)
from bike_routing_agent.models import (
    Coordinate,
    RouteCandidate,
    RouteConstraints,
    RouteMetrics,
)

PADDING_ORDER = ["paved", "compacted", "loose", "natural_soft", "masonry"]


@pytest.fixture(scope="module")
def benchmark() -> BenchmarkSet:
    return load_benchmark()


def _candidate(provider: str, metrics: RouteMetrics, warnings=()) -> RouteCandidate:
    return RouteCandidate(
        provider=provider,
        provider_profile="cycling-regular",
        geometry_geojson={"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        metrics=metrics,
        warnings=list(warnings),
    )


def _good_metrics(case) -> RouteMetrics:
    """Metrics sitting at the centre of the case's judged envelopes."""
    exp = case.expectations
    distance = (
        (exp.distance_m.minimum + exp.distance_m.maximum) / 2
        if exp.distance_m
        else 5_000.0
    )
    ascent = (exp.ascent_m.minimum + exp.ascent_m.maximum) / 2 if exp.ascent_m else 10.0

    coverage: dict[str, float] = {}
    max_unknown = exp.max_unknown_surface_fraction
    surf = exp.surface
    if surf:
        for category, share in surf.min_by_category.items():
            coverage[category] = max(coverage.get(category, 0.0), share)
        if surf.min_combined:
            group = surf.min_combined.categories
            deficit = surf.min_combined.share - sum(
                coverage.get(c, 0.0) for c in group
            )
            if deficit > 0:
                coverage[group[0]] = coverage.get(group[0], 0.0) + deficit
        known = sum(coverage.values())
        floors = [known]
        if surf.min_known_share is not None:
            floors.append(surf.min_known_share)
        if max_unknown is not None:
            floors.append(1.0 - max_unknown)
        target = min(max(floors), 1.0)
        for category in PADDING_ORDER:
            if known >= target - 1e-9:
                break
            cap = 1.0
            if surf.max_by_category and category in surf.max_by_category:
                cap = min(cap, surf.max_by_category[category])
            if surf.max_combined and category in surf.max_combined.categories:
                cap = min(cap, surf.max_combined.share)
            room = cap - coverage.get(category, 0.0)
            add = min(room, target - known)
            if add > 0:
                coverage[category] = coverage.get(category, 0.0) + add
                known += add
    unknown = max_unknown if max_unknown is not None and not surf else (
        1.0 - sum(coverage.values()) if surf else None
    )
    return RouteMetrics(distance_m=distance, duration_s=3600, ascent_m=ascent,
                        surface_coverage=coverage, unknown_surface_fraction=unknown)


def _silly_metrics(case) -> RouteMetrics:
    """A detour that contradicts the judgement wherever data is claimed."""
    exp = case.expectations
    distance = exp.distance_m.maximum + 3_000.0 if exp.distance_m else 40_000.0
    return RouteMetrics(
        distance_m=distance,
        duration_s=7200,
        ascent_m=None,  # missing data must skip, never invent a failure
        surface_coverage={},
        unknown_surface_fraction=0.99,
    )


# -- curated file -----------------------------------------------------------


def test_default_benchmark_loads_and_validates(benchmark: BenchmarkSet):
    assert benchmark.schema_version == 1
    assert len(benchmark.cases) >= 5
    ids = [c.case_id for c in benchmark.cases]
    assert len(ids) == len(set(ids))


def test_benchmark_path_is_relative_to_package():
    assert DEFAULT_BENCHMARK_PATH.name == "core-v1.json"
    assert DEFAULT_BENCHMARK_PATH.is_file()


def test_load_benchmark_accepts_custom_path(tmp_path: Path, benchmark: BenchmarkSet):
    payload = json.loads(DEFAULT_BENCHMARK_PATH.read_text(encoding="utf-8"))
    payload["cases"] = payload["cases"][:1]
    path = tmp_path / "mini.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    mini = load_benchmark(path)
    assert [c.case_id for c in mini.cases] == [benchmark.cases[0].case_id]


def test_load_benchmark_is_consistent_with_construction(benchmark: BenchmarkSet):
    """The file round-trips through the schema unchanged."""
    again = BenchmarkSet.model_validate(
        json.loads(DEFAULT_BENCHMARK_PATH.read_text(encoding="utf-8"))
    )
    assert again == benchmark


# -- schema strictness --------------------------------------------------------


def _case_payload(**expectation_overrides):
    payload = json.loads(DEFAULT_BENCHMARK_PATH.read_text(encoding="utf-8"))
    expectations = dict(payload["cases"][0]["expectations"])
    expectations.update(expectation_overrides)
    payload["cases"][0]["expectations"] = expectations
    return payload


@pytest.mark.parametrize(
    "mutate, message",
    [
        (
            lambda p: p["cases"][0]["expectations"].update(
                {"surface": {"min_by_category": {"tarmac": 0.5}}}
            ),
            "unknown surface categories",
        ),
        (
            lambda p: p["cases"][0]["expectations"].update(
                {"distance_m": {"min": 5000, "max": 1000}}
            ),
            "empty range",
        ),
        (
            lambda p: p["cases"][0]["expectations"].update({"surface": {}}),
            "judges nothing",
        ),
        (
            lambda p: p["cases"][0]["request"].update({"detour": True}),
            "Extra",
        ),
        (
            lambda p: p["cases"].append(dict(p["cases"][0])),
            "duplicate case ids",
        ),
        (
            lambda p: p["cases"][0]["expectations"]["surface"].update(
                {"min_combined": {"categories": ["paved"], "share": 1.5}}
            ),
            "Input should be less than or equal to 1",
        ),
    ],
)
def test_schema_rejects_bad_files(mutate, message):
    payload = json.loads(DEFAULT_BENCHMARK_PATH.read_text(encoding="utf-8"))
    mutate(payload)
    with pytest.raises(Exception, match=message):
        BenchmarkSet.model_validate(payload)


def test_surface_categories_follow_taxonomy():
    assert {"paved", "masonry", "compacted", "loose", "natural_soft"} <= SURFACE_CATEGORIES


# -- evaluation semantics -----------------------------------------------------


@pytest.mark.parametrize("case", load_benchmark().cases, ids=lambda c: c.case_id)
def test_good_candidate_passes_every_judgement(case):
    evaluation = evaluate_candidate(case, _candidate("ideal", _good_metrics(case)))
    assert [c.name for c in evaluation.failed] == []
    assert [c.name for c in evaluation.skipped] == []
    assert evaluation.passed


@pytest.mark.parametrize("case", load_benchmark().cases, ids=lambda c: c.case_id)
def test_silly_candidate_fails_on_contradicted_expectations_only(case):
    evaluation = evaluate_candidate(case, _candidate("silly", _silly_metrics(case)))
    failed = {c.name for c in evaluation.failed}
    assert "distance_m" in failed
    if case.expectations.max_unknown_surface_fraction is not None:
        assert "unknown_surface_fraction" in failed
    # missing ascent and missing coverage skip -- unknown is never a failure
    skipped = {c.name for c in evaluation.skipped}
    assert "ascent_m" in skipped
    if case.expectations.surface:
        assert "surface" in skipped
    assert evaluation.passed is False


def test_candidate_without_any_data_skips_everything():
    case = load_benchmark().cases[0]
    metrics = RouteMetrics(distance_m=2_000)  # only distance is known
    evaluation = evaluate_candidate(case, _candidate("sparse", metrics))
    assert {c.name for c in evaluation.skipped} == {
        "ascent_m",
        "unknown_surface_fraction",
        "surface",
    }
    # distance is inside the envelope, nothing contradicted -> passes
    assert evaluation.passed


def test_ascent_expectation_catches_flat_harz_route(benchmark: BenchmarkSet):
    case = next(c for c in benchmark.cases if c.case_id == "harz-climb")
    metrics = _good_metrics(case).model_copy(update={"ascent_m": 5.0})
    evaluation = evaluate_candidate(case, _candidate("flat-lie", metrics))
    assert {c.name for c in evaluation.failed} == {"ascent_m"}


# -- ranking and weight sensitivity -------------------------------------------


def test_good_candidate_outranks_silly_detour(benchmark: BenchmarkSet):
    case = benchmark.cases[2]  # bs-wf-tour, has a distance envelope
    midpoint_km = (
        case.expectations.distance_m.minimum + case.expectations.distance_m.maximum
    ) / 2_000
    constraint_payload = case.request.constraints.model_dump()
    constraint_payload["target_distance_km"] = midpoint_km
    constraints = RouteConstraints(**constraint_payload)
    ranked = ranked_candidates(
        [
            _candidate("silly", _silly_metrics(case)),
            _candidate("ideal", _good_metrics(case)),
        ],
        constraints,
    )
    assert [c.provider for c in ranked] == ["ideal", "silly"]
    assert ranked[0].score > ranked[1].score
    assert set(ranked[0].score_breakdown) == {
        "distance_fit",
        "elevation_fit",
        "warning_penalty",
    }


def test_weight_grid_flips_a_borderline_ranking():
    """The trade-off the weights encode is case- and weight-dependent.

    A: exact distance target but over the ascent cap.
    B: 15 % longer but comfortably under the cap.
    """
    constraints = RouteConstraints(target_distance_km=10, max_ascent_m=200)
    a = _candidate(
        "a-exact-flat-violation", RouteMetrics(distance_m=10_000, ascent_m=300)
    )
    b = _candidate("b-longer-gentle", RouteMetrics(distance_m=11_500, ascent_m=50))

    default_order = [c.provider for c in ranked_candidates([a, b], constraints)]
    assert default_order == ["b-longer-gentle", "a-exact-flat-violation"]

    distance_heavy = [
        c.provider
        for c in ranked_candidates([a, b], constraints, 0.80, 0.20)
    ]
    assert distance_heavy == ["a-exact-flat-violation", "b-longer-gentle"]


def test_weight_sensitivity_report_lists_orders_per_grid_point(benchmark: BenchmarkSet):
    case = benchmark.cases[2]
    report = weight_sensitivity(
        [_candidate("ideal", _good_metrics(case)), _candidate("silly", _silly_metrics(case))],
        case.request.constraints,
    )
    assert len(report) == 4
    assert all(len(order) == 2 for order in report.values())


def test_ranked_candidates_matches_production_scoring_at_default_weights():
    from bike_routing_agent.scoring.basic import score_candidate

    constraints = RouteConstraints(target_distance_km=8)
    candidate = _candidate("p", RouteMetrics(distance_m=9_000, ascent_m=40))
    (score, _), = [
        (c.score, c) for c in ranked_candidates([candidate], constraints)
    ]
    assert score == pytest.approx(score_candidate(candidate, constraints)[0])


def test_build_request_mirrors_case():
    case = load_benchmark().cases[0]
    request = build_request(case)
    assert request.origin == Coordinate(lon=case.request.origin.lon,
                                        lat=case.request.origin.lat)
    assert request.destination == case.request.destination
    assert request.constraints == case.request.constraints
