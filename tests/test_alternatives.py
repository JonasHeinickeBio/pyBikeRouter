"""Ranking, de-duplication and rationale of route candidates (issue #24)."""

import math

import pytest

from bike_routing_agent.models import RouteCandidate, RouteMetrics
from bike_routing_agent.nodes.parse import build_parse_node
from bike_routing_agent.nodes.score import build_score_node, score_candidates
from bike_routing_agent.scoring.alternatives import (
    candidate_label,
    frechet_distance_m,
    rank_candidates,
)

# ~111.2 km per degree of latitude; 0.001 deg lon at lat 52 is ~68 m.
LAT = 52.0
M_PER_DEG_LON = 111_320 * math.cos(math.radians(LAT))


def _line(offset_m: float = 0.0, n: int = 10, start_lon: float = 10.0) -> dict:
    """West-to-east line at lat 52.0 + offset (metres north), n vertices."""
    lat = LAT + offset_m / 111_320
    return {
        "type": "LineString",
        "coordinates": [
            [start_lon + i * (0.02 / (n - 1)), lat, 100.0] for i in range(n)
        ],
    }


def _candidate(
    provider: str,
    geometry: dict,
    *,
    score: float | None = 0.8,
    distance_m: float = 1_400.0,
    ascent_m: float | None = 20.0,
    profile: str = "p",
) -> RouteCandidate:
    return RouteCandidate(
        provider=provider,
        provider_profile=profile,
        geometry_geojson=geometry,
        metrics=RouteMetrics(distance_m=distance_m, ascent_m=ascent_m),
        score=score,
    )


# -- geometry metric -----------------------------------------------------------


def test_identical_lines_have_zero_distance():
    a = _candidate("a", _line())
    assert frechet_distance_m(a, _candidate("b", _line())) == pytest.approx(0.0, abs=1e-6)


def test_vertex_density_does_not_matter():
    sparse = _candidate("a", _line(n=3))
    dense = _candidate("b", _line(n=200))
    assert frechet_distance_m(sparse, dense) < 1.0


def test_offset_lines_report_the_offset():
    a = _candidate("a", _line(0))
    b = _candidate("b", _line(120))
    assert frechet_distance_m(a, b) == pytest.approx(120, rel=0.05)


def test_unusable_geometry_has_no_distance():
    point = _candidate("a", {"type": "Point", "coordinates": [10.0, 52.0]})
    short = _candidate("b", {"type": "LineString", "coordinates": [[10.0, 52.0]]})
    assert frechet_distance_m(point, _candidate("c", _line())) is None
    assert frechet_distance_m(short, _candidate("c", _line())) is None


def test_multilinestring_parts_are_joined():
    line = _line()
    multi = {
        "type": "MultiLineString",
        "coordinates": [line["coordinates"][:5], line["coordinates"][4:]],
    }
    assert frechet_distance_m(_candidate("a", multi), _candidate("b", line)) < 1.0


def test_degenerate_zero_length_line_does_not_crash():
    same_point = {"type": "LineString", "coordinates": [[10.0, 52.0], [10.0, 52.0]]}
    result = frechet_distance_m(_candidate("a", same_point), _candidate("b", _line()))
    assert result is not None and result > 0


# -- ranking -------------------------------------------------------------------


def test_ranks_follow_score_with_deterministic_tiebreaks():
    far = _line(500)
    ranked = rank_candidates(
        [
            _candidate("c", far, score=0.5),
            _candidate("b", _line(1000), score=0.7, distance_m=2_000),
            _candidate("a", _line(2000), score=0.7, distance_m=1_000),  # shorter wins the tie
            _candidate("n", _line(3000), score=None),
        ]
    )
    assert [c.provider for c in ranked] == ["a", "b", "c", "n"]
    assert [c.rank for c in ranked] == [1, 2, 3, 4]


def test_default_returns_everything_and_annotates_duplicates():
    ranked = rank_candidates(
        [
            _candidate("ors", _line(0), score=0.9),
            _candidate("brouter", _line(5), score=0.85),  # same street, snapped 5 m off
            _candidate("valhalla", _line(800), score=0.7),
        ]
    )
    assert [c.provider for c in ranked] == ["ors", "brouter", "valhalla"]
    ors, brouter, valhalla = ranked
    assert ors.duplicates == ["brouter/p"] and ors.duplicate_of is None
    assert brouter.duplicate_of == "ors/p"
    assert "near-identical to ors/p" in brouter.rank_rationale
    assert valhalla.duplicate_of is None and valhalla.duplicates == []


def test_max_alternatives_drops_duplicates_and_caps():
    candidates = [
        _candidate("ors", _line(0), score=0.9),
        _candidate("brouter", _line(5), score=0.85),
        _candidate("valhalla", _line(800), score=0.7),
        _candidate("other", _line(1600), score=0.6),
    ]
    ranked = rank_candidates(candidates, max_alternatives=2)
    assert [c.provider for c in ranked] == ["ors", "valhalla"]
    assert ranked[0].duplicates == ["brouter/p"]
    assert [c.rank for c in ranked] == [1, 2]
    assert len(rank_candidates(candidates, max_alternatives=10)) == 3  # 4 minus the duplicate


def test_better_scored_member_of_a_cluster_is_kept_regardless_of_input_order():
    ranked = rank_candidates(
        [_candidate("low", _line(3), score=0.4), _candidate("high", _line(0), score=0.9)],
        max_alternatives=3,
    )
    assert [c.provider for c in ranked] == ["high"]
    assert ranked[0].duplicates == ["low/p"]


def test_threshold_controls_what_counts_as_the_same_route():
    pair = [_candidate("a", _line(0), score=0.9), _candidate("b", _line(80), score=0.8)]
    assert len(rank_candidates(pair, max_alternatives=3, dedup_threshold_m=50)) == 2
    assert len(rank_candidates(pair, max_alternatives=3, dedup_threshold_m=100)) == 1


def test_unreadable_geometry_is_never_merged():
    broken = {"type": "Point", "coordinates": [10.0, 52.0]}
    ranked = rank_candidates(
        [_candidate("a", broken, score=0.9), _candidate("b", broken, score=0.8)],
        max_alternatives=3,
    )
    assert len(ranked) == 2


def test_empty_input_ranks_to_nothing():
    assert rank_candidates([]) == []


def test_rank_one_is_never_a_duplicate_and_single_candidate_is_labelled():
    only = rank_candidates([_candidate("ors", _line())])[0]
    assert only.rank == 1 and only.duplicate_of is None
    assert only.rank_rationale == "rank 1: only candidate"


def test_rationale_states_metric_differences_as_facts():
    ranked = rank_candidates(
        [
            _candidate("ors", _line(0), score=0.9, distance_m=10_000, ascent_m=100),
            _candidate("brouter", _line(900), score=0.8, distance_m=11_200, ascent_m=60),
        ]
    )
    assert ranked[0].rank_rationale == "rank 1: highest score (0.90)"
    assert ranked[1].rank_rationale == (
        "rank 2: score 0.80 (0.10 below rank 1); 1.2 km longer; 40 m less ascent"
    )
    text = " ".join(c.rank_rationale for c in ranked).lower()
    assert "safe" not in text


def test_rationale_without_metric_differences_says_comparable():
    ranked = rank_candidates(
        [
            _candidate("a", _line(0), score=None, ascent_m=None),
            _candidate("b", _line(900), score=None, ascent_m=None),
        ]
    )
    assert ranked[1].rank_rationale == "rank 2: comparable to rank 1"


def test_candidate_label():
    assert candidate_label(_candidate("ors", _line(), profile="cycling-regular")) == (
        "ors/cycling-regular"
    )


# -- score node and parse node ---------------------------------------------------


def _node_candidate(provider: str, distance_m: float, geometry: dict) -> dict:
    return {
        "provider": provider,
        "provider_profile": "p",
        "geometry_geojson": geometry,
        "metrics": {"distance_m": distance_m, "duration_s": 600, "ascent_m": 10},
        "warnings": [],
    }


def _state(**extra):
    return {
        "candidates": [
            _node_candidate("ors", 10_000, _line(0)),
            _node_candidate("brouter", 10_050, _line(4)),
            _node_candidate("valhalla", 12_000, _line(900)),
        ],
        "constraints": {"target_distance_km": 10},
        **extra,
    }


def test_score_node_ranks_all_candidates_by_default():
    update = score_candidates(_state())
    assert [c["rank"] for c in update["candidates"]] == [1, 2, 3]
    assert update["selected_candidate"] == update["candidates"][0]
    assert update["selected_candidate"]["provider"] == "ors"


def test_score_node_honors_max_alternatives():
    update = score_candidates(_state(max_alternatives=2))
    assert [c["provider"] for c in update["candidates"]] == ["ors", "valhalla"]


@pytest.mark.parametrize("bad", [0, -3, True, "2", 2.5])
def test_score_node_ignores_nonsensical_caps(bad):
    update = score_candidates(_state(max_alternatives=bad))
    assert len(update["candidates"]) == 3


def test_score_node_clamps_oversized_cap():
    update = score_candidates(_state(max_alternatives=999))
    assert len(update["candidates"]) == 2  # three candidates, one near-duplicate


def test_score_node_threshold_is_configurable():
    node = build_score_node(dedup_threshold_m=1.0)
    update = node(_state(max_alternatives=3))
    assert len(update["candidates"]) == 3  # 4 m apart is distinct at a 1 m threshold


def test_parse_node_passes_max_alternatives_through():
    node = build_parse_node()
    base = {"origin": "A", "destination": "B"}
    assert node({"raw_input": {**base, "max_alternatives": 3}})["max_alternatives"] == 3
    assert node({"raw_input": base})["max_alternatives"] is None
