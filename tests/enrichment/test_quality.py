"""Data-quality policy tests for OSM surface/access tag classification.

The policy (enrichment/quality.py) decides what raw tag soup means before
scoring ever sees it: explicit surface tags win, tracktype/paved/native
infer, contradictions cancel out to unknown, and anything outside the
taxonomy stays unknown.
"""

from __future__ import annotations

import pytest

from bike_routing_agent.enrichment.quality import (
    EXPLICIT,
    INFERRED,
    WayClass,
    build_summary,
    classify_way,
    normalize_tag_value,
)


def test_normalize_tag_value_strips_case_whitespace_and_colon_suffix() -> None:
    assert normalize_tag_value("  Paving_Stones:30 ") == "paving_stones"
    assert normalize_tag_value("Asphalt") == "asphalt"


@pytest.mark.parametrize(
    ("tags", "category", "source"),
    [
        ({"surface": "asphalt"}, "paved", EXPLICIT),
        ({"surface": "Paving_Stones:30"}, "masonry", EXPLICIT),
        ({"surface": "compacted"}, "compacted", EXPLICIT),
        ({"surface": "gravel"}, "loose", EXPLICIT),
        ({"surface": "dirt"}, "natural_soft", EXPLICIT),
        ({"tracktype": "grade1"}, "paved", INFERRED),
        ({"tracktype": "grade2"}, "compacted", INFERRED),
        ({"tracktype": "grade5"}, "natural_soft", INFERRED),
        ({"paved": "yes"}, "paved", INFERRED),
        ({"paved": "true"}, "paved", INFERRED),
        ({"native": "yes"}, "natural_soft", INFERRED),
    ],
)
def test_classify_way_resolves_category_and_evidence(
    tags: dict, category: str, source: str
) -> None:
    way_class = classify_way(tags)
    assert way_class.category == category
    assert way_class.source == source
    assert way_class.conflict is False


@pytest.mark.parametrize(
    "tags",
    [
        {},  # nothing mapped at all
        {"surface": "trail"},  # outside the taxonomy
        {"surface": "unpaved"},  # deliberately not mapped: too ambiguous
        {"tracktype": "grade9"},  # not an OSM grade
        {"paved": "no"},  # says what a way is NOT
        {"native": "no"},
        {"highway": "residential"},  # road class is not surface evidence
    ],
)
def test_classify_way_keeps_unknown_unknown(tags: dict) -> None:
    way_class = classify_way(tags)
    assert way_class.category is None
    assert way_class.source is None
    assert way_class.conflict is False


def test_explicit_surface_beats_tracktype_and_booleans() -> None:
    way_class = classify_way(
        {"surface": "asphalt", "tracktype": "grade5", "native": "yes"}
    )
    # native=yes + hard surface would conflict, but an explicit asphalt
    # surface outranks the fallback tags... except native is a *conflict*
    # rule here, so policy cancels it out instead of trusting either.
    assert way_class.conflict is True
    assert way_class.category is None

    way_class = classify_way({"surface": "ground", "tracktype": "grade1"})
    assert way_class.category == "natural_soft"
    assert way_class.source == EXPLICIT


@pytest.mark.parametrize(
    "tags",
    [
        {"paved": "yes", "native": "yes"},  # paved and unpaved at once
        {"paved": "yes", "surface": "sand"},  # paved but loose surface
        {"paved": "no", "surface": "asphalt"},  # not paved but asphalt
        {"native": "yes", "surface": "concrete"},  # natural but concrete
    ],
)
def test_contradictory_tags_cancel_out_to_conflict(tags: dict) -> None:
    way_class = classify_way(tags)
    assert way_class.conflict is True
    assert way_class.category is None
    assert way_class.source is None


@pytest.mark.parametrize(
    ("tags", "category"),
    [
        # compacted is legitimately ambiguous OSM tagging, never a conflict.
        ({"paved": "yes", "surface": "compacted"}, "compacted"),
        ({"paved": "no", "surface": "compacted"}, "compacted"),
    ],
)
def test_compacted_never_conflicts_with_paved(tags: dict, category: str) -> None:
    way_class = classify_way(tags)
    assert way_class.category == category
    assert way_class.conflict is False


def test_highway_tag_is_carried_through() -> None:
    way_class = classify_way({"highway": "cycleway", "surface": "asphalt"})
    assert way_class.highway == "cycleway"
    assert classify_way({"surface": "asphalt"}).highway is None
    assert classify_way({"highway": 123}).highway is None  # non-string ignored


def test_build_summary_weights_everything_by_length() -> None:
    paved = WayClass(category="paved", source=EXPLICIT, conflict=False, highway="residential")
    loose_inferred = WayClass(
        category="loose", source=INFERRED, conflict=False, highway="track"
    )
    conflict = WayClass(category=None, source=None, conflict=True, highway="path")
    summary = build_summary(
        [
            (100.0, paved),
            (100.0, loose_inferred),
            (100.0, conflict),
            (100.0, None),  # no way matched this segment
            (100.0, WayClass(category=None, source=None, conflict=False, highway=None)),
        ]
    )
    assert summary.total_m == pytest.approx(500.0)
    assert summary.coverage == {"paved": pytest.approx(0.2), "loose": pytest.approx(0.2)}
    # conflict + unmatched + unmapped all count as unknown, never as coverage
    assert summary.unknown_fraction == pytest.approx(0.6)
    assert summary.conflict_fraction == pytest.approx(0.2)
    assert summary.inferred_fraction == pytest.approx(0.2)
    # road-class visibility includes conflicting ways (the way is known even
    # when its surface is not) but not unmatched geometry.
    assert summary.highway_fractions == {
        "residential": pytest.approx(0.2),
        "track": pytest.approx(0.2),
        "path": pytest.approx(0.2),
    }


def test_build_summary_fractions_sum_to_one() -> None:
    paved = WayClass(category="paved", source=EXPLICIT, conflict=False, highway=None)
    summary = build_summary([(10.0, paved), (40.0, None)])
    assert sum(summary.coverage.values()) + summary.unknown_fraction == pytest.approx(1.0)


def test_build_summary_degenerate_route_is_all_unknown() -> None:
    summary = build_summary([(0.0, None)])
    assert summary.total_m == 0.0
    assert summary.unknown_fraction == pytest.approx(1.0)
    assert summary.coverage == {}
