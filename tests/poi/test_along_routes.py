"""Counting well-known places along several candidate routes with ONE search."""

from __future__ import annotations

import httpx
import respx

from bike_routing_agent.poi.categories import resolve_categories
from bike_routing_agent.poi.fetcher import OverpassPoiFetcher
from bike_routing_agent.poi.geo import corridor_boxes, merge_boxes
from bike_routing_agent.poi.models import Poi
from bike_routing_agent.poi.service import PoiService

URL = "https://overpass.example/api/interpreter"
# Two routes that share the first half and part company: the second bends 3 km north.
ROUTE_A = [(10.0, 47.5), (10.15, 47.5), (10.3, 47.5)]
ROUTE_B = [(10.0, 47.5), (10.15, 47.527), (10.3, 47.5)]


def test_boxes_that_lie_inside_or_mostly_overlap_another_become_one():
    big = (10.0, 47.0, 10.3, 47.1)
    inside = (10.05, 47.02, 10.1, 47.05)
    mostly = (10.29, 47.0, 10.4, 47.1)  # overlaps the smaller box's area by 9 %
    far = (11.0, 48.0, 11.1, 48.1)
    assert merge_boxes([inside, big, far]) == [big, far]
    assert len(merge_boxes([big, mostly, far], overlap=0.05)) == 2
    assert len(merge_boxes([big, mostly, far], overlap=0.5)) == 3


def test_near_parallel_routes_need_far_fewer_boxes_than_the_sum_of_their_own():
    each = [corridor_boxes(line, 300.0) for line in (ROUTE_A, ROUTE_B)]
    merged = merge_boxes([b for boxes in each for b in boxes])
    assert 1 <= len(merged) < sum(len(b) for b in each)


@respx.mock
async def test_one_overpass_search_serves_all_routes_and_each_is_measured_against_its_own_line():
    on_a = {"type": "node", "id": 1, "lat": 47.5003, "lon": 10.05,
            "tags": {"historic": "castle", "name": "Burg A", "wikidata": "Q1"}}  # fmt: skip
    on_b = {"type": "node", "id": 2, "lat": 47.5265, "lon": 10.15,
            "tags": {"historic": "castle", "name": "Burg B", "wikidata": "Q2"}}  # fmt: skip
    shared = {"type": "node", "id": 3, "lat": 47.5001, "lon": 10.01,
              "tags": {"tourism": "viewpoint", "name": "Aussicht", "wikidata": "Q3"}}  # fmt: skip
    far = {"type": "node", "id": 4, "lat": 47.54, "lon": 10.1,  # 2 km off both routes
           "tags": {"historic": "castle", "name": "Weit", "wikidata": "Q4"}}  # fmt: skip
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json={"elements": [on_a, on_b, shared, far]})
    )

    class Resolver:
        async def fame(self, qids):
            return {q: 10 for q in qids}

        async def wikidata_for_titles(self, titles):
            return {}

    service = PoiService(OverpassPoiFetcher(base_urls=[URL]), Resolver())
    got = await service.along_routes([ROUTE_A, ROUTE_B], None, buffer_m=300.0, linked_only=True)
    assert route.call_count == 1  # not one per route
    names = [sorted(p.name for p in near) for near in got.per_route]
    assert names == [["Aussicht", "Burg A"], ["Aussicht", "Burg B"]]
    burg_a = next(p for p in got.per_route[0] if p.name == "Burg A")
    assert burg_a.distance_from_route_m < 100 and 3.0 < burg_a.along_route_km < 4.5
    assert got.fame_status == "ok"


@respx.mock
async def test_a_single_line_or_a_degenerate_one_is_handled():
    respx.post(URL).mock(return_value=httpx.Response(200, json={"elements": []}))
    service = PoiService(OverpassPoiFetcher(base_urls=[URL]), resolver=None)  # type: ignore[arg-type]
    got = await service.along_routes([ROUTE_A, [(10.0, 47.5)]], None, buffer_m=300.0)
    assert got.per_route == [[], []]


def test_the_corridor_query_for_many_lines_is_one_query_per_category_and_box():
    from bike_routing_agent.poi.fetcher import build_boxes_query

    boxes = [(10.0, 47.4, 10.2, 47.6)]
    linked = build_boxes_query(boxes, [], resolve_categories(["historic"]), 25.0, linked_only=True)
    assert "wikidata" in linked and linked.count("10.0") >= 1
    plain = build_boxes_query(boxes, boxes, resolve_categories(None), 25.0)
    assert plain.count("nwr") > linked.count("nwr")  # every category and selector
    assert isinstance(Poi, type)  # imported for the type used in the fakes above
