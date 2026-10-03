import pytest

from bike_routing_agent.models import RouteCandidate, RouteConstraints, RouteMetrics
from bike_routing_agent.scoring import basic
from bike_routing_agent.scoring.basic import (
    score_candidate,
    score_candidates_together,
    surface_fit,
    uncertainty_notes,
)


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


def test_warning_penalty_scales_linearly_below_the_cap():
    candidate = _candidate()
    candidate = candidate.model_copy(update={"warnings": ["a", "b", "c"]})
    _, breakdown = score_candidate(candidate, RouteConstraints())
    assert breakdown["warning_penalty"] == pytest.approx(0.15)


def test_distance_fit_never_goes_negative_for_huge_detours():
    candidate = _candidate(distance_m=100_000)
    _, breakdown = score_candidate(candidate, RouteConstraints(target_distance_km=20))
    assert breakdown["distance_fit"] == 0.0


def test_elevation_fit_never_goes_negative():
    candidate = _candidate(ascent_m=10_000)
    _, breakdown = score_candidate(candidate, RouteConstraints(max_ascent_m=100))
    assert breakdown["elevation_fit"] == 0.0


def test_missing_ascent_keeps_elevation_fit_at_one_and_is_flagged():
    candidate = _candidate(ascent_m=None)
    constraints = RouteConstraints(max_ascent_m=50)
    _, breakdown = score_candidate(candidate, constraints)
    assert breakdown["elevation_fit"] == 1.0
    notes = uncertainty_notes(candidate)
    assert any("elevation" in note for note in notes)


def test_clean_candidate_has_no_uncertainty_notes():
    notes = uncertainty_notes(_candidate())
    assert notes == []


# -- surface fit (issue #23) ---------------------------------------------------

def _surface_candidate(provider: str, coverage: dict[str, float], **kw) -> RouteCandidate:
    unknown = round(1.0 - sum(coverage.values()), 6) if coverage else None
    candidate = _candidate(surface_coverage=coverage, unknown_surface_fraction=unknown, **kw)
    return candidate.model_copy(update={"provider": provider})


def test_surface_fit_is_none_without_preferences_or_evidence():
    covered = _surface_candidate("a", {"paved": 0.8})
    assert surface_fit(covered, RouteConstraints()) is None
    assert surface_fit(_candidate(), RouteConstraints(prefer_surfaces=["paved"])) is None


def test_surface_fit_prefer_only_is_share_of_known_length():
    candidate = _surface_candidate("a", {"paved": 0.3, "loose": 0.1})  # 60 % unknown
    fit, known = surface_fit(candidate, RouteConstraints(prefer_surfaces=["paved"]))
    # re-based onto the known 40 %: 0.3 / 0.4 -- unknown is not held against the route
    assert fit == pytest.approx(0.75)
    assert known == pytest.approx(0.4)


def test_surface_fit_avoid_only_and_combined_terms():
    candidate = _surface_candidate("a", {"paved": 0.5, "loose": 0.5})
    avoid_loose = RouteConstraints(avoid_surfaces=["loose"])
    assert surface_fit(candidate, avoid_loose)[0] == pytest.approx(0.5)
    both = RouteConstraints(prefer_surfaces=["paved"], avoid_surfaces=["loose"])
    assert surface_fit(candidate, both)[0] == pytest.approx(0.5)  # mean(0.5, 0.5)
    all_good = _surface_candidate("b", {"paved": 1.0})
    assert surface_fit(all_good, both)[0] == pytest.approx(1.0)  # mean(1.0, 1.0)


def test_surface_fit_resolves_raw_osm_values_and_ignores_unrecognised_tokens():
    candidate = _surface_candidate("a", {"masonry": 1.0})
    assert surface_fit(candidate, RouteConstraints(prefer_surfaces=["cobblestone"]))[0] == 1.0
    # "unpaved" is not an OSM surface value we can resolve: unknown, not guessed
    assert surface_fit(candidate, RouteConstraints(prefer_surfaces=["unpaved"])) is None


def test_breakdown_reports_surface_only_when_computable():
    constraints = RouteConstraints(prefer_surfaces=["paved"])
    _, plain = score_candidate(_candidate(), constraints)
    assert set(plain) == {"distance_fit", "elevation_fit", "warning_penalty"}
    _, with_data = score_candidate(_surface_candidate("a", {"paved": 0.9}), constraints)
    assert with_data["surface_fit"] == pytest.approx(1.0)
    assert with_data["surface_known_share"] == pytest.approx(0.9)


def test_surface_is_inactive_by_default_so_scores_are_unchanged():
    constraints = RouteConstraints(prefer_surfaces=["paved"])
    cands = [_surface_candidate("a", {"paved": 1.0}), _surface_candidate("b", {"loose": 1.0})]
    together = score_candidates_together(cands, constraints)
    assert [round(s, 9) for s, _ in together] == [
        round(score_candidate(c, constraints)[0], 9) for c in cands
    ]
    assert all("surface_weight_applied" not in b for _, b in together)


def test_active_surface_term_reorders_and_stays_in_unit_range():
    constraints = RouteConstraints(prefer_surfaces=["paved"], target_distance_km=20)
    near_but_loose = _surface_candidate("loose", {"loose": 1.0}, distance_m=20_000)
    farther_but_paved = _surface_candidate("paved", {"paved": 1.0}, distance_m=21_000)
    base = score_candidates_together([near_but_loose, farther_but_paved], constraints)
    assert base[0][0] > base[1][0]  # distance alone prefers the loose route
    scored = score_candidates_together(
        [near_but_loose, farther_but_paved], constraints, surface_weight=0.5
    )
    assert scored[1][0] > scored[0][0]  # a surface weight flips the ranking
    assert all(0.0 <= s <= 1.0 for s, _ in scored)
    assert all(b["surface_weight_applied"] == 0.5 for _, b in scored)


def test_surface_term_is_all_or_nothing_across_the_candidate_set():
    constraints = RouteConstraints(prefer_surfaces=["paved"])
    enriched = _surface_candidate("enriched", {"loose": 1.0})
    unenriched = _candidate()
    scored = score_candidates_together([enriched, unenriched], constraints, surface_weight=0.5)
    # one candidate has no evidence -> nobody is scored on surface, so the two
    # are still comparable and the unenriched one is not favoured by default
    assert all("surface_weight_applied" not in b for _, b in scored)
    assert scored[0][0] == pytest.approx(scored[1][0])


def test_sparse_surface_evidence_does_not_activate_the_term():
    constraints = RouteConstraints(prefer_surfaces=["paved"])
    sparse = _surface_candidate("sparse", {"paved": 0.2})  # 80 % unknown
    scored = score_candidates_together([sparse], constraints, surface_weight=0.5)
    assert "surface_weight_applied" not in scored[0][1]


def test_module_surface_weight_is_the_default(monkeypatch):
    constraints = RouteConstraints(prefer_surfaces=["paved"])
    cands = [_surface_candidate("a", {"paved": 1.0})]
    monkeypatch.setattr(basic, "SURFACE_WEIGHT", 0.25)
    scored = score_candidates_together(cands, constraints)
    assert scored[0][1]["surface_weight_applied"] == 0.25
