import pytest

from bike_routing_agent.calibration import (
    TRAFFIC_WEIGHT_GRID,
    ranked_candidates,
    traffic_weight_sensitivity,
)
from bike_routing_agent.models import RouteCandidate, RouteConstraints, RouteMetrics
from bike_routing_agent.scoring import basic
from bike_routing_agent.scoring.alternatives import rank_candidates
from bike_routing_agent.scoring.basic import (
    MAX_TRAFFIC_DETOUR,
    TRAFFIC_INDEX_AT_ZERO_FIT,
    TRAFFIC_WEIGHT,
    score_candidate,
    score_candidates_together,
    surface_fit,
    traffic_active,
    traffic_fit,
    uncertainty_notes,
    weighted_score,
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
    # a word that is neither a category nor an OSM surface value: unknown, not guessed
    assert surface_fit(candidate, RouteConstraints(prefer_surfaces=["lava"])) is None


def test_unpaved_means_every_unpaved_category_for_a_request():
    """``unpaved`` is an OSM surface value and the planner's own word: compacted, loose and soft."""
    loose = _surface_candidate("a", {"loose": 0.6, "compacted": 0.2, "paved": 0.2})
    paved = _surface_candidate("b", {"paved": 1.0})
    wants = RouteConstraints(prefer_surfaces=["unpaved"])
    assert surface_fit(loose, wants)[0] == pytest.approx(0.8)  # 0.6 + 0.2 of the known length
    assert surface_fit(paved, wants)[0] == pytest.approx(0.0)
    avoids = RouteConstraints(avoid_surfaces=["Unpaved"])  # any case
    assert surface_fit(paved, avoids)[0] == pytest.approx(1.0)
    assert surface_fit(loose, avoids)[0] == pytest.approx(0.2)
    # it adds to what else is named; a colon suffix is read like an OSM value's
    mixed = RouteConstraints(prefer_surfaces=["paved", "unpaved:grade2"])
    assert surface_fit(loose, mixed)[0] == pytest.approx(1.0)


def test_unpaved_is_judged_from_the_engines_own_shares_too():
    gravel = _engine_candidate(
        "main", {"paved": 0.2, "unpaved": 0.7, "cobbles": 0.0, "unknown": 0.1}
    )
    got = surface_fit(gravel, RouteConstraints(prefer_surfaces=["unpaved"]))
    assert got is not None and got[0] == pytest.approx(0.7 / 0.9)  # re-based onto the known length


def test_words_that_resolve_to_nothing_are_listed_for_the_response():
    from bike_routing_agent.config import unrecognised_surface_words

    assert unrecognised_surface_words(
        ["gravle", "paved", "unpaved"], ["dirt", "Lava", "gravle"]
    ) == [
        "gravle",
        "Lava",
    ]
    assert unrecognised_surface_words([], []) == []
    assert unrecognised_surface_words(["  ", ""], ["asphalt"]) == []  # blanks mean nothing


def test_breakdown_reports_surface_only_when_computable():
    constraints = RouteConstraints(prefer_surfaces=["paved"])
    _, plain = score_candidate(_candidate(), constraints)
    assert set(plain) == {"distance_fit", "elevation_fit", "warning_penalty"}
    _, with_data = score_candidate(_surface_candidate("a", {"paved": 0.9}), constraints)
    assert with_data["surface_fit"] == pytest.approx(1.0)
    assert with_data["surface_known_share"] == pytest.approx(0.9)


def test_a_request_without_surface_preferences_is_scored_exactly_as_before():
    cands = [_surface_candidate("a", {"paved": 1.0}), _surface_candidate("b", {"loose": 1.0})]
    together = score_candidates_together(cands, RouteConstraints())
    assert [round(s, 9) for s, _ in together] == [
        round(score_candidate(c, RouteConstraints())[0], 9) for c in cands
    ]
    assert all("surface_weight_applied" not in b for _, b in together)


def test_a_surface_weight_of_zero_switches_the_term_off():
    constraints = RouteConstraints(prefer_surfaces=["paved"])
    cands = [_surface_candidate("a", {"paved": 1.0}), _surface_candidate("b", {"loose": 1.0})]
    together = score_candidates_together(cands, constraints, surface_weight=0.0)
    assert [round(s, 9) for s, _ in together] == [
        round(score_candidate(c, constraints)[0], 9) for c in cands
    ]
    assert all("surface_weight_applied" not in b for _, b in together)


def test_active_surface_term_reorders_and_stays_in_unit_range():
    constraints = RouteConstraints(prefer_surfaces=["paved"], target_distance_km=20)
    near_but_loose = _surface_candidate("loose", {"loose": 1.0}, distance_m=20_000)
    farther_but_paved = _surface_candidate("paved", {"paved": 1.0}, distance_m=21_000)
    base = score_candidates_together(
        [near_but_loose, farther_but_paved], constraints, surface_weight=0.0
    )
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


# ------------------------------------------------------------ traffic (issue #23)


def _traffic_candidate(profile: str, km: float, index: float | None, *, alt_of: str | None = None):
    return RouteCandidate(
        provider="brouter",
        provider_profile=profile,
        geometry_geojson={"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        metrics=RouteMetrics(distance_m=km * 1000, duration_s=3600, traffic_index=index),
        provenance={"alternative_of": alt_of} if alt_of else {},
    )


@pytest.mark.parametrize(
    "index, expected",
    [(0.0, 1.0), (0.1, 0.75), (0.2, 0.5), (0.4, 0.0), (0.7, 0.0)],
)
def test_the_traffic_fit_falls_linearly_to_zero_at_the_scale_end(index, expected):
    assert TRAFFIC_INDEX_AT_ZERO_FIT == 0.4
    got = traffic_fit(_traffic_candidate("p", 10, index), RouteConstraints())
    assert got == pytest.approx(expected)


def test_there_is_no_traffic_fit_without_an_estimate_or_when_traffic_is_not_to_be_avoided():
    assert traffic_fit(_traffic_candidate("p", 10, None), RouteConstraints()) is None
    quiet = _traffic_candidate("p", 10, 0.0)
    assert traffic_fit(quiet, RouteConstraints(avoid_high_traffic_roads=False)) is None
    _, breakdown = score_candidate(quiet, RouteConstraints(avoid_high_traffic_roads=False))
    assert "traffic_fit" not in breakdown


def test_the_traffic_fit_is_reported_but_does_not_change_the_single_score():
    candidate = _traffic_candidate("p", 10, 0.3)
    score, breakdown = score_candidate(candidate, RouteConstraints())
    assert breakdown["traffic_fit"] == pytest.approx(0.25) and score == 1.0


def test_a_quiet_route_outscores_a_busy_one_and_the_weight_is_recorded():
    quiet = _traffic_candidate("quiet", 66, 0.08)
    busy = _traffic_candidate("busy", 70, 0.23)
    (q_score, q), (b_score, b) = score_candidates_together([quiet, busy], RouteConstraints())
    assert q_score > b_score
    assert q["traffic_weight_applied"] == b["traffic_weight_applied"] == TRAFFIC_WEIGHT
    # (1.0 * 1 + 0.5 * fit) / 1.5: the scale stays 0..1
    assert q_score == pytest.approx((1.0 + 0.5 * 0.8) / 1.5)
    assert b_score == pytest.approx((1.0 + 0.5 * (1 - 0.23 / 0.4)) / 1.5)


def test_one_candidate_without_an_estimate_switches_the_term_off_for_the_whole_set():
    candidates = [_traffic_candidate("a", 66, 0.08), _traffic_candidate("b", 70, None)]
    results = score_candidates_together(candidates, RouteConstraints())
    assert [score for score, _ in results] == [1.0, 1.0]  # exactly the old formula
    assert all("traffic_weight_applied" not in breakdown for _, breakdown in results)
    assert traffic_active([breakdown for _, breakdown in results], 0.5) is False


def test_a_weight_of_zero_or_a_request_that_does_not_avoid_traffic_leaves_scores_alone():
    candidates = [_traffic_candidate("a", 66, 0.08), _traffic_candidate("b", 70, 0.3)]
    off = score_candidates_together(candidates, RouteConstraints(), traffic_weight=0.0)
    assert [s for s, _ in off] == [1.0, 1.0]
    free = score_candidates_together(candidates, RouteConstraints(avoid_high_traffic_roads=False))
    assert [s for s, _ in free] == [1.0, 1.0]


def test_a_much_longer_route_gets_no_credit_for_being_quiet():
    short_busy = _traffic_candidate("short", 60, 0.3)
    long_quiet = _traffic_candidate("long", 60 * (1 + MAX_TRAFFIC_DETOUR) + 1, 0.0)
    ok_quiet = _traffic_candidate("ok", 60 * (1 + MAX_TRAFFIC_DETOUR) - 1, 0.0)
    results = dict(
        zip(
            ["short", "long", "ok"],
            score_candidates_together([short_busy, long_quiet, ok_quiet], RouteConstraints()),
            strict=True,
        )
    )
    assert results["long"][1]["traffic_fit"] == 0.0
    assert results["long"][1]["traffic_detour_capped"] == 1.0
    assert (
        results["ok"][1]["traffic_fit"] == 1.0 and "traffic_detour_capped" not in results["ok"][1]
    )
    assert results["long"][0] < results["short"][0] < results["ok"][0]


def test_the_weighted_score_combines_surface_and_traffic_over_their_total_weight():
    breakdown = {
        "distance_fit": 1.0, "elevation_fit": 1.0, "warning_penalty": 0.1,
        "surface_fit": 0.5, "traffic_fit": 0.25,
    }  # fmt: skip
    both = weighted_score(
        breakdown, surface_weight=0.2, use_surface=True, traffic_weight=0.5, use_traffic=True
    )
    assert both == pytest.approx((1.0 + 0.2 * 0.5 + 0.5 * 0.25) / 1.7 - 0.1)
    assert weighted_score(breakdown, traffic_weight=0.5, use_traffic=True) == pytest.approx(
        (1.0 + 0.5 * 0.25) / 1.5 - 0.1
    )
    assert weighted_score(breakdown) == pytest.approx(0.9)  # nothing active: the old formula


def test_the_road_bikes_own_busy_profile_loses_to_a_clearly_quieter_equal_length_alternative():
    """The case the weight was chosen from (docs/scoring-and-exports.md, evidence table)."""
    main = _traffic_candidate("fastbike", 69.8, 0.2347)
    alt = _traffic_candidate("custom_commuter-v1", 66.0, 0.0789, alt_of="fastbike")
    constraints = RouteConstraints()
    old = rank_candidates(
        [
            c.model_copy(update={"score": s, "score_breakdown": b})
            for c, (s, b) in zip(
                [main, alt],
                score_candidates_together([main, alt], constraints, traffic_weight=0.0),
                strict=True,
            )
        ]
    )
    new = rank_candidates(
        [
            c.model_copy(update={"score": s, "score_breakdown": b})
            for c, (s, b) in zip(
                [main, alt], score_candidates_together([main, alt], constraints), strict=True
            )
        ]
    )
    assert old[0].provider_profile == "fastbike"  # without the term: its own profile wins
    assert new[0].provider_profile == "custom_commuter-v1"


def test_a_slightly_quieter_alternative_does_not_reshuffle_the_ranking():
    main = _traffic_candidate("custom_gravel-v2", 58, 0.039)
    alt = _traffic_candidate("custom_touring-v1", 47.5, 0.12, alt_of="custom_gravel-v2")
    scored = [
        c.model_copy(update={"score": s, "score_breakdown": b})
        for c, (s, b) in zip(
            [main, alt], score_candidates_together([main, alt], RouteConstraints()), strict=True
        )
    ]
    assert rank_candidates(scored)[0].provider_profile == "custom_gravel-v2"


def test_the_calibration_ranking_follows_the_production_rule_and_sweeps_the_weight():
    busy = _traffic_candidate("busy", 70, 0.3)
    quiet = _traffic_candidate("quiet", 70, 0.05)
    constraints = RouteConstraints()
    assert [c.provider_profile for c in ranked_candidates([busy, quiet], constraints)] == [
        "quiet", "busy",
    ]  # fmt: skip
    sweep = traffic_weight_sensitivity([busy, quiet], constraints)
    assert list(sweep) == [f"traffic={w:.2f}" for w in TRAFFIC_WEIGHT_GRID]
    assert sweep["traffic=0.00"] == ["brouter/busy", "brouter/quiet"]  # a tie: provider order
    assert sweep["traffic=0.50"] == ["brouter/quiet", "brouter/busy"]
    unasked = traffic_weight_sensitivity(
        [busy, quiet], RouteConstraints(avoid_high_traffic_roads=False)
    )
    assert len({tuple(v) for v in unasked.values()}) == 1  # inactive: the same everywhere


# ----------------------------------------------- surface from the engine's own shares (issue #23)


def _engine_candidate(profile: str, shares: dict[str, float], km: float = 66.0, **kw):
    return RouteCandidate(
        provider="brouter",
        provider_profile=profile,
        geometry_geojson={"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        metrics=RouteMetrics(distance_m=km * 1000, engine_surface_shares=shares, **kw),
        provenance={} if profile == "main" else {"alternative_of": "main"},
    )


def test_the_shipped_surface_weight_is_the_calibrated_one():
    assert basic.SURFACE_WEIGHT == 1.0  # docs/scoring-and-exports.md, "Surface weight: evidence"


def test_the_engines_shares_give_a_surface_fit_when_there_is_no_enrichment():
    candidate = _engine_candidate(
        "p", {"paved": 0.5, "unpaved": 0.2, "cobbles": 0.1, "unknown": 0.2}
    )
    fit, known = surface_fit(candidate, RouteConstraints(prefer_surfaces=["paved"]))
    assert fit == pytest.approx(0.5 / 0.8) and known == pytest.approx(0.8)  # of the known length
    fit, _ = surface_fit(candidate, RouteConstraints(avoid_surfaces=["cobblestone"]))
    assert fit == pytest.approx(1 - 0.1 / 0.8)  # a raw OSM value resolves to a category first
    both, _ = surface_fit(
        candidate, RouteConstraints(prefer_surfaces=["paved"], avoid_surfaces=["sett"])
    )
    assert both == pytest.approx((0.5 / 0.8 + (1 - 0.1 / 0.8)) / 2)


@pytest.mark.parametrize("token", ["gravel", "compacted", "dirt", "loose", "natural_soft"])
def test_every_unpaved_category_is_read_as_the_engines_unpaved_class(token):
    candidate = _engine_candidate(
        "p", {"paved": 0.4, "unpaved": 0.6, "cobbles": 0.0, "unknown": 0.0}
    )
    fit, _ = surface_fit(candidate, RouteConstraints(prefer_surfaces=[token]))
    assert fit == pytest.approx(0.6)


def test_a_preference_the_engine_cannot_tell_apart_is_not_judged():
    candidate = _engine_candidate(
        "p", {"paved": 0.4, "unpaved": 0.6, "cobbles": 0.0, "unknown": 0.0}
    )
    # compacted and loose are both "unpaved" to the engine: preferring one and avoiding the other
    # is a question it cannot answer, so there is no fit (never a guess)
    conflicting = RouteConstraints(prefer_surfaces=["compacted"], avoid_surfaces=["loose"])
    assert surface_fit(candidate, conflicting) is None


def test_no_engine_shares_or_all_unknown_means_no_fit():
    constraints = RouteConstraints(prefer_surfaces=["paved"])
    assert surface_fit(_engine_candidate("p", {}), constraints) is None
    unknown = _engine_candidate("p", {"paved": 0.0, "unpaved": 0.0, "cobbles": 0.0, "unknown": 1.0})
    assert surface_fit(unknown, constraints) is None


def test_the_osm_enrichment_wins_over_the_engines_shares_when_both_exist():
    candidate = _engine_candidate(
        "p",
        {"paved": 0.9, "unpaved": 0.0, "cobbles": 0.0, "unknown": 0.1},
        surface_coverage={"loose": 0.8},
        unknown_surface_fraction=0.2,
    )
    fit, known = surface_fit(candidate, RouteConstraints(prefer_surfaces=["paved"]))
    assert fit == 0.0 and known == pytest.approx(0.8)  # the finer source decides


def test_a_gravel_rider_who_asks_for_paved_gets_the_paved_alternative_of_equal_length():
    """The clearest case of the calibration (Braunschweig -> Hannover)."""
    main = _engine_candidate(
        "main", {"paved": 0.26, "unpaved": 0.58, "cobbles": 0.07, "unknown": 0.1}
    )
    alt = _engine_candidate(
        "touring", {"paved": 0.65, "unpaved": 0.15, "cobbles": 0.14, "unknown": 0.06}
    )
    constraints = RouteConstraints(bike_type="gravel", prefer_surfaces=["paved"])

    def best(**kw):
        results = score_candidates_together([main, alt], constraints, **kw)
        ranked = rank_candidates(
            [
                c.model_copy(update={"score": s, "score_breakdown": b})
                for c, (s, b) in zip([main, alt], results, strict=True)
            ]
        )
        return ranked[0].provider_profile

    assert best(surface_weight=0.0) == "main"  # the term off: the bike type's own profile
    assert best() == "touring"  # shipped weight: the stated preference decides
    plain = score_candidates_together([main, alt], RouteConstraints(bike_type="gravel"))
    assert plain[0][0] == plain[1][0]  # no preference stated: nothing changes


def test_one_candidate_from_another_engine_without_shares_switches_the_term_off_for_all():
    other = RouteCandidate(
        provider="ors",
        provider_profile="cycling-regular",
        geometry_geojson={"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        metrics=RouteMetrics(distance_m=66_000),
    )
    engine = _engine_candidate(
        "main", {"paved": 0.2, "unpaved": 0.8, "cobbles": 0.0, "unknown": 0.0}
    )
    results = score_candidates_together(
        [engine, other], RouteConstraints(prefer_surfaces=["paved"])
    )
    assert all("surface_weight_applied" not in breakdown for _, breakdown in results)
