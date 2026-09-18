"""Unit tests for the score_candidates node."""

from bike_routing_agent.nodes.score import score_candidates


def candidate_dict(provider: str, *, distance_m: float, warnings: list[str] | None = None):
    return {
        "provider": provider,
        "provider_profile": "cycling-regular",
        "geometry_geojson": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        "metrics": {
            "distance_m": distance_m,
            "duration_s": 1800,
            "ascent_m": 100,
            "descent_m": 95,
        },
        "warnings": warnings or [],
    }


def test_empty_candidate_list_is_structured_no_route():
    update = score_candidates({"candidates": [], "constraints": {}})

    assert update["status"] == "no_route"
    assert update["errors"][0]["code"] == "no_candidates"


def test_missing_candidates_key_is_also_structured_no_route():
    update = score_candidates({"constraints": {}})

    assert update["status"] == "no_route"


def test_best_candidate_is_selected_and_all_are_scored():
    # 20 km exactly matches the target; 40 km deviates fully.
    candidates = [
        candidate_dict("bad", distance_m=40_000),
        candidate_dict("good", distance_m=20_000),
    ]

    update = score_candidates({"candidates": candidates, "constraints": {"target_distance_km": 20}})

    assert update["status"] == "in_progress"
    assert update["selected_candidate"]["provider"] == "good"
    scores = {c["provider"]: c["score"] for c in update["candidates"]}
    assert scores["good"] > scores["bad"]
    assert update["selected_candidate"]["score_breakdown"]["distance_fit"] == 1.0


def test_warnings_can_break_a_tie_between_equal_geometry():
    candidates = [
        candidate_dict("clean", distance_m=20_000),
        candidate_dict("warned", distance_m=20_000, warnings=["steep grade"]),
    ]

    update = score_candidates({"candidates": candidates, "constraints": {"target_distance_km": 20}})

    assert update["selected_candidate"]["provider"] == "clean"
