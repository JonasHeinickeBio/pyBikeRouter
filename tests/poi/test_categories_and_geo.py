import pytest

from bike_routing_agent.poi.categories import (
    ALL_KEYS,
    CATEGORIES,
    SIGHT_KEYS,
    category_for_tags,
    resolve_categories,
)
from bike_routing_agent.poi.geo import (
    corridor_boxes,
    cumulative_lengths_m,
    decimate,
    haversine_m,
    project_onto_line,
)


def test_every_selector_is_a_well_formed_overpass_filter():
    for category in CATEGORIES:
        assert category.selectors
        for selector in category.selectors:
            assert selector.startswith("[") and selector.endswith("]")
            assert selector.count("[") == selector.count("]")


def test_category_keys_are_unique_and_split_into_sights_and_services():
    assert len(set(ALL_KEYS)) == len(ALL_KEYS)
    assert {"viewpoint", "attraction", "historic"} <= set(SIGHT_KEYS)
    assert "food" not in SIGHT_KEYS and "water" not in SIGHT_KEYS


def test_resolve_categories_defaults_to_all_and_rejects_unknown_keys():
    assert [c.key for c in resolve_categories(None)] == list(ALL_KEYS)
    assert [c.key for c in resolve_categories(["water", "viewpoint", "water"])] == [
        "viewpoint",
        "water",
    ]
    with pytest.raises(ValueError, match="unknown POI categories"):
        resolve_categories(["viewpoint", "casino"])


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"tourism": "viewpoint"}, "viewpoint"),
        ({"historic": "castle", "name": "x"}, "historic"),
        ({"historic": "memorial", "wikidata": "Q1"}, "historic"),
        ({"historic": "memorial"}, None),  # memorials count only when in Wikidata
        ({"natural": "peak", "wikidata": "Q9"}, "nature"),
        ({"natural": "peak"}, None),
        ({"natural": "waterfall"}, "nature"),
        ({"amenity": "place_of_worship"}, None),
        ({"amenity": "place_of_worship", "wikidata": "Q2"}, "religious"),
        ({"amenity": "place_of_worship", "wikipedia": "de:Dom"}, "religious"),
        ({"historic": "memorial", "wikipedia": "de:X"}, "historic"),
        ({"amenity": "drinking_water"}, "water"),
        ({"amenity": "cafe"}, "food"),
        ({"shop": "bicycle"}, "bike_service"),
        ({"tourism": "hotel"}, None),
    ],
)
def test_category_for_tags_mirrors_the_overpass_selectors(tags, expected):
    found = category_for_tags(tags, list(CATEGORIES))
    assert (found.key if found else None) == expected


def test_category_for_tags_only_considers_the_allowed_categories():
    only_water = resolve_categories(["water"])
    assert category_for_tags({"tourism": "viewpoint"}, only_water) is None


def test_decimate_keeps_the_ends_and_the_bound():
    line = [(float(i), 0.0) for i in range(1000)]
    thinned = decimate(line, 50)
    assert len(thinned) == 50 and thinned[0] == line[0] and thinned[-1] == line[-1]
    assert decimate(line[:10], 50) == line[:10]


def test_haversine_matches_a_known_distance():
    # One degree of latitude is about 111.2 km.
    assert haversine_m((10.0, 50.0), (10.0, 51.0)) == pytest.approx(111_195, rel=1e-3)


def test_project_onto_line_reports_offset_and_position_along():
    line = [(10.0, 50.0), (10.1, 50.0), (10.2, 50.0)]
    cumulative = cumulative_lengths_m(line)
    # A point 0.01 degrees (about 1.1 km) north of the middle vertex.
    offset, along = project_onto_line((10.1, 50.01), line, cumulative)
    assert offset == pytest.approx(1112, rel=0.02)
    assert along == pytest.approx(cumulative[1], rel=0.01)
    # Beyond the end it is measured to the end point.
    offset_end, along_end = project_onto_line((10.3, 50.0), line, cumulative)
    assert along_end == pytest.approx(cumulative[-1], rel=1e-6)
    assert offset_end == pytest.approx(haversine_m((10.2, 50.0), (10.3, 50.0)), rel=0.02)


def test_project_onto_line_needs_two_points():
    with pytest.raises(ValueError):
        project_onto_line((0.0, 0.0), [(0.0, 0.0)], [0.0])


def test_corridor_boxes_cover_the_line_with_padding():
    line = [(10.0, 47.5), (10.1, 47.5)]
    [box] = corridor_boxes(line, 1000)
    assert box[0] < 10.0 and box[2] > 10.1 and box[1] < 47.5 < box[3]
    assert box[3] - 47.5 == pytest.approx(1000 / 111320, rel=0.01)


def test_one_long_diagonal_leg_gets_several_boxes_not_one_huge_one():
    # About 55 km across and 55 km up in two points: a single box would be 3000 km2.
    boxes = corridor_boxes([(10.0, 47.5), (10.75, 48.0)], 1500)
    assert len(boxes) >= 5
    assert all((b[3] - b[1]) * 111.32 < 20 for b in boxes)
    assert min(b[0] for b in boxes) < 10.0 and max(b[2] for b in boxes) > 10.75


def test_a_very_long_line_is_covered_by_at_most_max_boxes():
    line = [(8.0 + i * 0.02, 47.0 + i * 0.01) for i in range(500)]
    boxes = corridor_boxes(line, 1500, max_boxes=40)
    assert 1 < len(boxes) <= 40


def test_corridor_boxes_need_two_points():
    with pytest.raises(ValueError):
        corridor_boxes([(10.0, 47.5)], 100)
