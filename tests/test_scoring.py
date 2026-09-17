from bike_routing_agent.models import RouteCandidate, RouteConstraints, RouteMetrics
from bike_routing_agent.scoring.basic import score_candidate, uncertainty_notes


def _candidate(**metrics_overrides) -> RouteCandidate:
    defaults = dict(distance_m=20_000, duration_s=3600, ascent_m=100, descent_m=95)
    defaults.update(metrics_overrides)
    metrics = RouteMetrics(**defaults)
    return RouteCandidate(
        provider="ors",
        provider_profile="cycling-regular",
        geometry_geojson={"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        metrics=metrics,
    )


def test_perfect_distance_and_elevation_fit_scores_near_max():
    candidate = _candidate()
    constraints = RouteConstraints(target_distance_km=20, max_ascent_m=200)
    score, breakdown = score_candidate(candidate, constraints)
    assert breakdown["distance_fit"] == 1.0
    assert breakdown["elevation_fit"] == 1.0
    assert score == 1.0


def test_distance_deviation_lowers_distance_fit():
    candidate = _candidate()
    constraints = RouteConstraints(target_distance_km=10)
    score, breakdown = score_candidate(candidate, constraints)
    assert breakdown["distance_fit"] == 0.0
    assert score < 1.0


def test_ascent_exceeding_max_lowers_elevation_fit():
    candidate = _candidate(ascent_m=300)
    constraints = RouteConstraints(max_ascent_m=200)
    score, breakdown = score_candidate(candidate, constraints)
    assert breakdown["elevation_fit"] == 0.5


def test_warnings_apply_capped_penalty():
    candidate = _candidate()
    candidate = candidate.model_copy(update={"warnings": ["a", "b", "c", "d", "e", "f", "g", "h"]})
    constraints = RouteConstraints()
    _, breakdown = score_candidate(candidate, constraints)
    assert breakdown["warning_penalty"] == 0.3


def test_missing_target_distance_defaults_distance_fit_to_one():
    candidate = _candidate()
    constraints = RouteConstraints()
    _, breakdown = score_candidate(candidate, constraints)
    assert breakdown["distance_fit"] == 1.0


def test_unknown_surface_fraction_is_reported_as_uncertainty_not_bonus():
    candidate = _candidate(unknown_surface_fraction=0.6)
    constraints = RouteConstraints(target_distance_km=20, max_ascent_m=200)
    score, _ = score_candidate(candidate, constraints)
    baseline_candidate = _candidate()
    baseline_score, _ = score_candidate(baseline_candidate, constraints)
    assert score == baseline_score
    notes = uncertainty_notes(candidate)
    assert any("unknown" in note for note in notes)
