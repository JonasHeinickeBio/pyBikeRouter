"""Route history contract (issue #7).

The same behavioral tests run against the in-memory implementation (always)
and the PostGIS one (``live``: needs ``TEST_DATABASE_URL``, see
tests/storage/conftest.py), so the two cannot drift apart.
"""

from datetime import UTC, datetime, timedelta

import pytest

from bike_routing_agent.storage.history import (
    InMemoryRouteHistory,
    PlanFilter,
    StatsFilter,
    record_from_state,
)

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def candidate(provider="ors", profile="cycling-regular", score=0.9, coords=None, **metrics):
    return {
        "provider": provider,
        "provider_profile": profile,
        "geometry_geojson": {
            "type": "LineString",
            "coordinates": coords or [[10.5, 52.3, 70.0], [10.6, 52.4, 85.0]],
        },
        "metrics": {"distance_m": 12_000.0, "duration_s": 2_400.0, "ascent_m": 80.0, **metrics},
        "score": score,
        "score_breakdown": {"distance_fit": 1.0},
        "warnings": ["steep"],
        "provenance": {"provider": provider, "profile": profile},
        "raw_provider_response": {"big": "payload"},
    }


def ready_state(candidates, *, selected=None, bike_type="gravel"):
    return {
        "status": "ready",
        "raw_input": {"origin": "A", "destination": "B"},
        "constraints": {"bike_type": bike_type},
        "resolved_origin": {"lon": 10.5, "lat": 52.3},
        "resolved_destination": {"lon": 10.6, "lat": 52.4},
        "candidates": candidates,
        "selected_candidate": selected if selected is not None else candidates[0],
        "explanation": "an explanation",
        "artifacts": {"geojson_file": "x.geojson", "gpx_file": "x.gpx"},
        "route_id": "x",
    }


def pid(n: int) -> str:
    return f"{n:032x}"


@pytest.fixture(
    params=["memory", pytest.param("postgres", marks=pytest.mark.live)],
)
def history(request):
    if request.param == "memory":
        return InMemoryRouteHistory()
    from bike_routing_agent.storage.postgres import PostgresRouteHistory

    return PostgresRouteHistory(request.getfixturevalue("postgres_database"))


def seed(history):
    """Three plans: a two-engine ready plan, a one-engine one, and a failure."""
    ors = candidate("ors", "cycling-regular", 0.9)
    bro = candidate(
        "brouter",
        "custom_gravel-v1",
        0.8,
        coords=[[13.3, 52.5], [13.4, 52.6]],
    )
    history.save(record_from_state(pid(1), ready_state([ors, bro]), created_at=T0))
    history.save(
        record_from_state(
            pid(2),
            ready_state([candidate("valhalla", "bicycle", 0.7)], bike_type="road"),
            created_at=T0 + timedelta(hours=1),
        )
    )
    history.save(
        record_from_state(
            pid(3),
            {
                "status": "provider_failure",
                "raw_input": {"origin": "A", "destination": "B"},
                "constraints": {"bike_type": "gravel"},
                "errors": [{"code": "provider_error", "message": "boom"}],
            },
            created_at=T0 + timedelta(hours=2),
        )
    )


def ids(plans):
    return [p.plan_id for p in plans]


def test_record_from_state_ranks_candidates_and_strips_raw_payloads():
    low = candidate("brouter", "trekking", 0.5)
    high = candidate("ors", "cycling-regular", 0.9)

    record = record_from_state(pid(1), ready_state([low, high], selected=high), created_at=T0)

    assert [(s.rank, s.candidate.provider, s.selected) for s in record.candidates] == [
        (1, "ors", True),
        (2, "brouter", False),
    ]
    assert all(s.candidate.raw_provider_response is None for s in record.candidates)
    assert record.bike_type == "gravel"
    assert record.artifacts == {"geojson_file": "x.geojson", "gpx_file": "x.gpx"}


def test_record_from_state_null_scores_rank_last():
    scored = candidate("ors", score=0.4)
    unscored = candidate("brouter", "trekking", score=None)

    record = record_from_state(pid(1), ready_state([unscored, scored], selected=scored))

    assert [s.candidate.provider for s in record.candidates] == ["ors", "brouter"]


def test_record_from_state_keeps_failures_without_candidates():
    record = record_from_state(
        pid(3),
        {"status": "no_route", "raw_input": {}, "errors": [{"code": "no_candidates"}]},
    )

    assert record.status == "no_route"
    assert record.candidates == []
    assert record.errors == [{"code": "no_candidates"}]


def test_save_then_get_round_trips_full_record(history):
    seed(history)

    record = history.get(pid(1))

    assert record is not None
    assert record.status == "ready"
    assert record.request == {"origin": "A", "destination": "B"}
    assert record.origin is not None and record.origin.lon == pytest.approx(10.5)
    assert record.explanation == "an explanation"
    assert record.created_at == T0
    top = record.candidates[0]
    assert (top.rank, top.selected, top.candidate.provider) == (1, True, "ors")
    # Lossless: elevations survive even though the spatial column is 2D.
    assert top.candidate.geometry_geojson["coordinates"][0] == [10.5, 52.3, 70.0]
    assert top.candidate.provenance == {"provider": "ors", "profile": "cycling-regular"}
    assert top.candidate.raw_provider_response is None


def test_get_unknown_plan_is_none(history):
    assert history.get(pid(99)) is None


def test_saving_the_same_plan_id_replaces_it(history):
    seed(history)
    history.save(
        record_from_state(
            pid(1), ready_state([candidate("valhalla", "bicycle", 0.1)]), created_at=T0
        )
    )

    record = history.get(pid(1))

    assert record is not None
    assert [s.candidate.provider for s in record.candidates] == ["valhalla"]


def test_query_defaults_to_newest_first(history):
    seed(history)

    assert ids(history.query(PlanFilter())) == [pid(3), pid(2), pid(1)]


def test_query_summaries_carry_candidates_without_geometry(history):
    seed(history)

    summary = history.query(PlanFilter(provider="ors"))[0]

    assert summary.plan_id == pid(1)
    assert summary.bike_type == "gravel"
    assert [(c.provider, c.provider_profile, c.selected) for c in summary.candidates] == [
        ("ors", "cycling-regular", True),
        ("brouter", "custom_gravel-v1", False),
    ]
    assert summary.candidates[0].distance_m == 12_000.0


def test_query_by_provider_matches_any_candidate(history):
    seed(history)

    assert ids(history.query(PlanFilter(provider="brouter"))) == [pid(1)]


def test_query_selected_only_matches_the_returned_candidate(history):
    seed(history)

    assert history.query(PlanFilter(provider="brouter", selected_only=True)) == []
    assert ids(history.query(PlanFilter(provider="ors", selected_only=True))) == [pid(1)]


def test_query_by_provider_and_profile(history):
    seed(history)

    assert ids(history.query(PlanFilter(provider="ors", profile="cycling-regular"))) == [pid(1)]
    assert history.query(PlanFilter(provider="ors", profile="cycling-road")) == []


def test_query_by_status_and_bike_type(history):
    seed(history)

    assert ids(history.query(PlanFilter(status="provider_failure"))) == [pid(3)]
    assert ids(history.query(PlanFilter(bike_type="road"))) == [pid(2)]


def test_query_by_time_window_is_half_open(history):
    seed(history)

    window = PlanFilter(since=T0 + timedelta(hours=1), until=T0 + timedelta(hours=2))

    assert ids(history.query(window)) == [pid(2)]


def test_query_by_bbox_matches_candidate_geometry(history):
    seed(history)

    berlin = PlanFilter(bbox=(13.0, 52.0, 14.0, 53.0))
    braunschweig = PlanFilter(bbox=(10.0, 52.0, 11.0, 53.0))

    assert ids(history.query(berlin)) == [pid(1)]
    assert ids(history.query(braunschweig)) == [pid(2), pid(1)]
    assert history.query(PlanFilter(bbox=(0.0, 0.0, 1.0, 1.0))) == []


def test_query_pagination(history):
    seed(history)

    assert ids(history.query(PlanFilter(limit=2))) == [pid(3), pid(2)]
    assert ids(history.query(PlanFilter(limit=2, offset=2))) == [pid(1)]


def test_plan_filter_rejects_unbounded_pages():
    with pytest.raises(ValueError):
        PlanFilter(limit=10_000)


# ---------------------------------------------------------------------------
# Evaluation aggregates (issue #7 dashboards)
# ---------------------------------------------------------------------------


def engine(stats, provider):
    [match] = [p for p in stats.providers if p.provider == provider]
    return match


def test_stats_on_an_empty_history(history):
    stats = history.stats(StatsFilter())

    assert stats.total_plans == 0
    assert stats.ready_rate is None
    assert (stats.by_status, stats.providers, stats.daily) == ({}, [], [])


def test_stats_count_outcomes_including_failures(history):
    seed(history)

    stats = history.stats(StatsFilter())

    assert stats.total_plans == 3
    assert stats.by_status == {"ready": 2, "provider_failure": 1}
    assert stats.ready_rate == pytest.approx(2 / 3)


def test_stats_per_engine_win_rate_and_means(history):
    seed(history)

    stats = history.stats(StatsFilter())

    assert [(p.provider, p.provider_profile) for p in stats.providers] == [
        ("brouter", "custom_gravel-v1"),
        ("ors", "cycling-regular"),
        ("valhalla", "bicycle"),
    ]
    brouter, ors = engine(stats, "brouter"), engine(stats, "ors")
    assert (ors.candidates, ors.selected, ors.win_rate) == (1, 1, 1.0)
    assert (brouter.candidates, brouter.selected, brouter.win_rate) == (1, 0, 0.0)
    assert ors.mean_score == pytest.approx(0.9)
    assert brouter.mean_score == pytest.approx(0.8)
    assert ors.mean_distance_m == pytest.approx(12_000.0)
    assert ors.mean_duration_s == pytest.approx(2_400.0)
    assert ors.mean_ascent_m == pytest.approx(80.0)
    assert ors.mean_score_breakdown == {"distance_fit": pytest.approx(1.0)}


def test_stats_win_rate_aggregates_across_plans_and_means_skip_nulls(history):
    # ors wins plan 1 (0.9) but loses plan 2 to brouter; one ascent is unknown.
    low = candidate("ors", "cycling-regular", 0.5, ascent_m=None)
    high = candidate("brouter", "trekking", 0.7)
    win = ready_state([candidate("ors", score=0.9)])
    history.save(record_from_state(pid(1), win, created_at=T0))
    history.save(
        record_from_state(pid(2), ready_state([low, high], selected=high), created_at=T0)
    )

    ors = engine(history.stats(StatsFilter()), "ors")

    assert (ors.candidates, ors.selected, ors.win_rate) == (2, 1, 0.5)
    assert ors.mean_score == pytest.approx(0.7)
    # Absence is not zero: only the plan that reported an ascent counts.
    assert ors.mean_ascent_m == pytest.approx(80.0)


def test_stats_mean_is_null_when_no_candidate_has_the_value(history):
    unscored = candidate("ors", score=None, ascent_m=None)
    history.save(record_from_state(pid(1), ready_state([unscored]), created_at=T0))

    ors = engine(history.stats(StatsFilter()), "ors")

    assert (ors.mean_score, ors.mean_ascent_m) == (None, None)


def test_stats_daily_volume_is_grouped_by_utc_day_and_status(history):
    seed(history)
    history.save(
        record_from_state(
            pid(4), ready_state([candidate()]), created_at=T0 + timedelta(days=2, hours=3)
        )
    )

    daily = history.stats(StatsFilter()).daily

    assert [(d.date.isoformat(), d.total, d.by_status) for d in daily] == [
        ("2026-09-01", 3, {"ready": 2, "provider_failure": 1}),
        ("2026-09-03", 1, {"ready": 1}),
    ]


def test_stats_filters_narrow_every_aggregate(history):
    seed(history)

    road = history.stats(StatsFilter(bike_type="road"))
    window = history.stats(
        StatsFilter(since=T0 + timedelta(hours=1), until=T0 + timedelta(hours=3))
    )

    assert (road.total_plans, [p.provider for p in road.providers]) == (1, ["valhalla"])
    assert road.by_status == {"ready": 1}
    assert window.total_plans == 2
    assert [p.provider for p in window.providers] == ["valhalla"]
    assert window.by_status == {"ready": 1, "provider_failure": 1}
