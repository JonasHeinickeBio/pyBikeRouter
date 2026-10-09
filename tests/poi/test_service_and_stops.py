from bike_routing_agent.errors import ProviderUnavailableError
from bike_routing_agent.poi.geo import cumulative_lengths_m
from bike_routing_agent.poi.models import Poi
from bike_routing_agent.poi.service import PoiService, dedupe, rank
from bike_routing_agent.poi.stops import select_stops

# A 20 km west-east line at 47.5 N.
LINE = [(10.0, 47.5), (10.3, 47.5)]


def poi(ident, *, kind="sight", category="attraction", lon=10.1, lat=47.5, **kw) -> Poi:
    return Poi(id=ident, category=category, kind=kind, lon=lon, lat=lat, **kw)


class FakeFetcher:
    def __init__(self, pois, error=None):
        self.pois = pois
        self.error = error
        self.calls = []

    async def along(self, line, categories, *, buffer_m, linked_only=False):
        self.calls.append(("along", [c.key for c in categories], buffer_m, linked_only))
        if self.error:
            raise self.error
        return list(self.pois)

    async def in_bbox(self, bbox, categories):
        self.calls.append(("bbox", [c.key for c in categories]))
        return list(self.pois)


class FakeResolver:
    def __init__(self, fame=None, by_title=None, fail=False):
        self._fame = fame or {}
        self._by_title = by_title or {}
        self.fail = fail
        self.asked = []

    async def fame(self, qids):
        self.asked.append(sorted(qids))
        return {} if self.fail else {q: self._fame[q] for q in qids if q in self._fame}

    async def wikidata_for_titles(self, tags):
        return {t: self._by_title[t] for t in tags if t in self._by_title}


def service(pois, **kw):
    resolver = FakeResolver(**{k: kw.pop(k) for k in ("fame", "by_title", "fail") if k in kw})
    return PoiService(FakeFetcher(pois, kw.pop("error", None)), resolver, **kw), resolver


# ---------------------------------------------------------------- ranking


def test_ranking_puts_the_best_known_first_and_never_treats_unknown_as_obscure():
    famous = poi("node/1", fame=120, distance_from_route_m=900.0)
    modest = poi("node/2", fame=4, distance_from_route_m=10.0)
    unknown_near = poi("node/3", distance_from_route_m=5.0)
    unknown_far = poi("node/4", distance_from_route_m=400.0)
    ranked = rank([unknown_far, modest, unknown_near, famous])
    assert [p.id for p in ranked] == ["node/1", "node/2", "node/3", "node/4"]


def test_dedupe_merges_one_item_mapped_twice():
    node = poi("node/1", name="Schloss", wikidata="Q1")
    outline = poi("way/9", name="Schloss", wikidata="Q1", lon=10.1002)
    other = poi("node/2", name="Aussicht", category="viewpoint", lon=10.2)
    kept = dedupe([outline, node, other])
    assert sorted(p.id for p in kept) == ["node/1", "node/2"]  # the node is preferred


def test_dedupe_merges_same_name_and_category_close_by_but_not_far_apart():
    a = poi("node/1", name="Pavillon")
    near = poi("node/2", name="pavillon", lon=10.1005)
    far = poi("node/3", name="Pavillon", lon=10.2)
    assert sorted(p.id for p in dedupe([a, near, far])) == ["node/1", "node/3"]


# ---------------------------------------------------------------- service


async def test_along_route_measures_distance_and_position_and_ranks_by_fame():
    near = poi("node/1", lat=47.5005, lon=10.05, wikidata="Q1")
    famous = poi("node/2", lat=47.505, lon=10.2, wikidata="Q2")
    unlinked = poi("node/3", lat=47.5, lon=10.15, category="viewpoint")
    svc, resolver = service([near, famous, unlinked], fame={"Q1": 5, "Q2": 90})
    result = await svc.along_route(LINE, None, buffer_m=1500)
    assert [p.id for p in result.pois] == ["node/2", "node/1", "node/3"]
    top = result.pois[0]
    assert top.fame == 90
    assert 540 < top.distance_from_route_m < 570  # 0.005 degrees of latitude
    assert 14.9 < top.along_route_km < 15.1  # 0.2 degrees of longitude
    assert result.fame_status == "ok" and result.truncated is False
    assert resolver.asked == [["Q1", "Q2"]]


async def test_pois_beyond_the_buffer_are_dropped_and_services_are_capped_at_500_m():
    too_far = poi("node/1", lat=47.52, wikidata="Q1")  # 2.2 km
    tap_far = poi("node/2", kind="service", category="water", lat=47.503, lon=10.1)  # 330 m
    tap_too_far = poi("node/3", kind="service", category="water", lat=47.507, lon=10.2)  # 780 m
    svc, _ = service([too_far, tap_far, tap_too_far], fame={"Q1": 9})
    result = await svc.along_route(LINE, None, buffer_m=1500)
    assert [p.id for p in result.pois] == ["node/2"]


async def test_a_wikipedia_only_poi_gets_its_wikidata_item_and_fame():
    castle = poi("way/1", category="historic", wikipedia="de:Schloss X")
    svc, _ = service([castle], fame={"Q7": 33}, by_title={"de:Schloss X": "Q7"})
    [found] = (await svc.along_route(LINE, None, buffer_m=1500)).pois
    assert found.wikidata == "Q7" and found.fame == 33


async def test_when_wikimedia_is_down_pois_are_still_found_and_it_says_so():
    svc, _ = service([poi("node/1", wikidata="Q1")], fail=True)
    result = await svc.along_route(LINE, None, buffer_m=1500)
    assert [p.id for p in result.pois] == ["node/1"]
    assert result.pois[0].fame is None and result.fame_status == "unavailable"


async def test_partial_fame_is_reported_as_partial():
    svc, _ = service(
        [poi("node/1", wikidata="Q1"), poi("node/2", wikidata="Q2", lon=10.2)], fame={"Q1": 3}
    )
    assert (await svc.along_route(LINE, None, buffer_m=1500)).fame_status == "partial"


async def test_no_wikimedia_links_means_nothing_to_look_up():
    svc, resolver = service([poi("node/1")])
    result = await svc.along_route(LINE, None, buffer_m=1500)
    assert result.fame_status == "skipped" and resolver.asked == []


async def test_services_are_never_ranked_by_fame():
    tap = poi("node/1", kind="service", category="water", wikidata="Q5", lat=47.5001)
    svc, resolver = service([tap], fame={"Q5": 50})
    [found] = (await svc.along_route(LINE, None, buffer_m=1500)).pois
    assert found.fame is None and resolver.asked == []


async def test_the_per_category_limit_truncates_and_says_so():
    pois = [poi(f"node/{i}", lon=10.0 + i * 0.01, wikidata=f"Q{i}") for i in range(1, 6)]
    svc, _ = service(pois, fame={f"Q{i}": i for i in range(1, 6)}, per_category_limit=3)
    result = await svc.along_route(LINE, None, buffer_m=1500)
    assert [p.id for p in result.pois] == ["node/5", "node/4", "node/3"] and result.truncated
    wider = await svc.along_route(LINE, None, buffer_m=1500, per_category_limit=5)
    assert len(wider.pois) == 5 and not wider.truncated


async def test_linked_only_keeps_pois_that_can_have_a_fame():
    linked = poi("node/1", wikidata="Q1")
    plain = poi("node/2", lon=10.2)
    svc, _ = service([linked, plain], fame={"Q1": 2})
    result = await svc.along_route(LINE, None, buffer_m=1500, linked_only=True)
    assert [p.id for p in result.pois] == ["node/1"]
    # The search itself is narrowed too, not only filtered afterwards.
    assert svc._fetcher.calls[-1][-1] is True


async def test_bbox_search_has_no_route_relation():
    svc, _ = service([poi("node/1", wikidata="Q1")], fame={"Q1": 8})
    result = await svc.in_bbox((10.0, 47.4, 10.2, 47.6), ["attraction"])
    assert result.pois[0].distance_from_route_m is None and result.pois[0].fame == 8
    assert svc._fetcher.calls[-1] == ("bbox", ["attraction"])


async def test_a_failing_search_propagates_so_the_caller_can_decide():
    svc, _ = service([], error=ProviderUnavailableError("down", provider="x"))
    try:
        await svc.along_route(LINE, None, buffer_m=1500)
    except ProviderUnavailableError:
        return
    raise AssertionError("expected the provider error")


# ---------------------------------------------------------------- stops


def along(ident, km, fame, *, off=100.0, lat=47.5, lon=10.1, kind="sight"):
    return poi(
        ident, kind=kind, fame=fame, along_route_km=km, distance_from_route_m=off, lat=lat, lon=lon
    )


def test_stops_are_the_most_famous_in_travel_order():
    pois = [
        along("a", 4.0, 20, lon=10.06),
        along("b", 9.0, 100, lon=10.13),
        along("c", 15.0, 60, lon=10.22),
        along("d", 12.0, 5, lon=10.18),
    ]
    stops = select_stops(pois, count=2, line_length_km=22.0)
    assert [p.id for p in stops] == ["b", "c"]  # fame 100 and 60, visited west to east


def test_unknown_fame_is_never_picked_even_when_nothing_else_exists():
    assert select_stops([along("a", 5.0, None)], count=1, line_length_km=22.0) == []


def test_services_and_the_ends_of_the_line_are_not_stops():
    pois = [
        along("tap", 8.0, 99, kind="service"),
        along("start", 0.2, 99, lon=10.003),
        along("end", 21.9, 99, lon=10.299),
    ]
    assert select_stops(pois, count=3, line_length_km=22.0) == []


def test_stops_keep_their_distance_from_each_other_and_from_given_places():
    close = [along("a", 8.0, 90, lon=10.12), along("b", 8.3, 80, lon=10.1235)]
    assert [p.id for p in select_stops(close, count=2, line_length_km=22.0)] == ["a"]
    assert select_stops(close, count=2, line_length_km=22.0, avoid=[(10.12, 47.5)]) == []


def test_a_sight_must_be_described_in_enough_languages_to_count_as_famous():
    pois = [along("a", 6.0, 3, lon=10.08), along("b", 12.0, 9, lon=10.16)]
    assert select_stops(pois, count=2, line_length_km=22.0) == pois  # default threshold: any fame
    only_b = select_stops(pois, count=2, line_length_km=22.0, min_fame=5)
    assert [p.id for p in only_b] == ["b"]
    assert select_stops(pois, count=2, line_length_km=22.0, min_fame=10) == []


def test_zero_count_or_empty_line_gives_no_stops():
    assert select_stops([along("a", 8.0, 9)], count=0, line_length_km=22.0) == []
    assert select_stops([along("a", 8.0, 9)], count=1, line_length_km=0) == []


def test_cumulative_lengths_start_at_zero():
    assert cumulative_lengths_m(LINE)[0] == 0.0
