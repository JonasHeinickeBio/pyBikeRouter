import httpx
import pytest
import respx

from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.poi.categories import resolve_categories
from bike_routing_agent.poi.fetcher import (
    MAX_SERVICE_ELEMENTS,
    MAX_SIGHT_ELEMENTS,
    OverpassPoiFetcher,
    build_along_query,
    build_bbox_query,
    parse_pois,
)
from bike_routing_agent.providers.base import InMemoryTTLCache

URL = "https://overpass.example/api/interpreter"
LINE = [(10.70, 47.56), (10.73, 47.56), (10.75, 47.55)]

# Shapes as the real Overpass API returns them with `out tags center`.
PAYLOAD = {
    "elements": [
        {
            "type": "node",
            "id": 1,
            "lat": 47.5667,
            "lon": 10.6997,
            "tags": {
                "name": "Ehemaliges Kloster St. Mang",
                "name:en": "Former St Mang's Abbey",
                "tourism": "attraction",
                "wikidata": "Q1527304",
                "website": "https://example.org/mang",
                "opening_hours": "Mo-Su 09:00-17:00",
            },
        },
        {
            "type": "way",
            "id": 2,
            "center": {"lat": 47.5575, "lon": 10.7498},
            "tags": {
                "historic": "castle",
                "name": "Schloss",
                "wikipedia": "de:Schloss Hohenschwangau",
            },
        },
        {"type": "node", "id": 3, "lat": 47.56, "lon": 10.72, "tags": {"tourism": "viewpoint"}},
        {
            "type": "node",
            "id": 4,
            "lat": 47.561,
            "lon": 10.721,
            "tags": {"amenity": "drinking_water"},
        },
        # Things that must be skipped, not fail the search:
        {"type": "node", "id": 5, "lat": 47.56, "lon": 10.72, "tags": {"shop": "kiosk"}},
        {"type": "node", "id": 6, "lat": 999, "lon": 10.72, "tags": {"tourism": "viewpoint"}},
        {"type": "way", "id": 7, "tags": {"tourism": "viewpoint"}},  # no centre
        {"type": "node", "id": "x", "lat": 1, "lon": 1, "tags": {"tourism": "viewpoint"}},
        "not an element",
        {
            "type": "node",
            "id": 8,
            "lat": 47.5,
            "lon": 10.7,
            "tags": {"tourism": "viewpoint", "wikidata": "not-a-qid", "website": "javascript:x"},
        },
    ]
}


def fetcher(**kw) -> OverpassPoiFetcher:
    return OverpassPoiFetcher(base_urls=[URL], **kw)


def test_parse_keeps_what_it_can_use_and_skips_the_rest():
    pois = parse_pois(PAYLOAD, resolve_categories(None))
    assert [p.id for p in pois] == ["node/1", "way/2", "node/3", "node/4", "node/8"]
    abbey, castle, viewpoint, water, odd = pois
    assert abbey.category == "attraction" and abbey.kind == "sight"
    assert abbey.name == "Ehemaliges Kloster St. Mang" and abbey.wikidata == "Q1527304"
    assert abbey.website == "https://example.org/mang" and abbey.opening_hours.startswith("Mo-Su")
    assert abbey.fame is None  # nothing is known about fame until it is looked up
    assert castle.category == "historic" and (castle.lat, castle.lon) == (47.5575, 10.7498)
    assert castle.wikipedia == "de:Schloss Hohenschwangau"
    assert viewpoint.name is None
    assert water.kind == "service"
    # Malformed links never reach URLs or API calls.
    assert odd.wikidata is None and odd.website is None


def test_parse_rejects_a_payload_that_is_not_overpass_json():
    with pytest.raises(ProviderBadResponseError):
        parse_pois({"remark": "runtime error"}, resolve_categories(None))


def test_the_along_query_uses_bounding_boxes_not_around_and_separates_sights_from_services():
    query = build_along_query(
        LINE, resolve_categories(["viewpoint", "water"]), buffer_m=3000, timeout_s=25
    )
    assert query.startswith("[out:json][timeout:25];(")
    assert "around" not in query  # `around` over a polyline times out on public Overpass
    assert 'nwr["tourism"="viewpoint"](' in query and 'nwr["amenity"="drinking_water"](' in query
    # Sights first with their own cap, then services with theirs.
    assert query.index("viewpoint") < query.index("drinking_water")
    assert f"out tags center {MAX_SIGHT_ELEMENTS};" in query
    assert f"out tags center {MAX_SERVICE_ELEMENTS};" in query
    assert query.index("viewpoint") < query.index(f"out tags center {MAX_SIGHT_ELEMENTS};")
    assert query.index(f"out tags center {MAX_SIGHT_ELEMENTS};") < query.index("drinking_water")


def test_linked_only_takes_the_linked_elements_once_and_narrows_them_in_memory():
    query = build_along_query(
        LINE,
        resolve_categories(["attraction", "religious", "water"]),
        buffer_m=1500,
        timeout_s=25,
        linked_only=True,
    )
    # One lookup per tag and box, then the categories are filtered from that set.
    assert 'nwr["wikidata"](' in query and 'nwr["wikipedia"](' in query
    assert ")->.linked;" in query
    assert 'nwr.linked["tourism"="attraction"];' in query
    assert 'nwr.linked["amenity"="place_of_worship"];' in query
    # A selector that already demanded a link is not repeated or doubled.
    assert query.count('nwr.linked["amenity"="place_of_worship"];') == 1
    assert '"wikidata"]["wikidata"' not in query
    # Services are never famous: left out of a linked search.
    assert "drinking_water" not in query
    assert query.count("out tags center") == 1


def test_linked_only_with_no_sight_category_asks_for_nothing():
    query = build_along_query(
        LINE, resolve_categories(["water"]), buffer_m=1500, timeout_s=25, linked_only=True
    )
    assert "nwr" not in query


@respx.mock
async def test_a_query_overpass_gave_up_on_is_an_error_not_an_empty_answer():
    # HTTP 200, but the server timed out: empty elements and a remark (seen on the public instance).
    timed_out = {
        "elements": [],
        "remark": 'runtime error: Query timed out in "query" at line 1 after 26 seconds.',
    }
    respx.post(URL).mock(return_value=httpx.Response(200, json=timed_out))
    cache = InMemoryTTLCache()
    f = fetcher(cache=cache)
    with pytest.raises(ProviderTimeoutError):
        await f.in_bbox((10.0, 47.0, 10.2, 47.1), resolve_categories(None))
    # ... and it was not remembered as "nothing here".
    respx.post(URL).mock(return_value=httpx.Response(200, json=PAYLOAD))
    assert len(await f.in_bbox((10.0, 47.0, 10.2, 47.1), resolve_categories(None))) == 5

    out_of_memory = {"elements": [], "remark": "runtime error: Query run out of memory"}
    respx.post(URL).mock(return_value=httpx.Response(200, json=out_of_memory))
    with pytest.raises(ProviderUnavailableError):
        await fetcher().in_bbox((10.1, 47.0, 10.2, 47.1), resolve_categories(None))


@respx.mock
async def test_linked_only_and_plain_searches_are_cached_separately():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=PAYLOAD))
    f = fetcher(cache=InMemoryTTLCache())
    await f.along(LINE, resolve_categories(None), buffer_m=1500)
    await f.along(LINE, resolve_categories(None), buffer_m=1500, linked_only=True)
    await f.along(LINE, resolve_categories(None), buffer_m=1500, linked_only=True)
    assert route.call_count == 2


def test_service_boxes_are_padded_less_than_sight_boxes():
    import re

    query = build_along_query(
        [(10.0, 47.5), (10.01, 47.5)],
        resolve_categories(["viewpoint", "water"]),
        buffer_m=3000,
        timeout_s=25,
    )

    def lat_span(tag: str) -> float:
        s, _w, n, _e = map(
            float,
            re.search(rf'{tag}"\]\(([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+)\)', query).groups(),
        )
        return n - s

    assert lat_span("viewpoint") == pytest.approx(2 * 3000 / 111320, rel=0.01)
    assert lat_span("drinking_water") == pytest.approx(2 * 500 / 111320, rel=0.01)


def test_a_long_route_is_covered_by_a_bounded_number_of_boxes():
    long_line = [(10.0 + i * 0.01, 47.5 + i * 0.004) for i in range(300)]  # roughly 250 km
    query = build_along_query(
        long_line, resolve_categories(["viewpoint"]), buffer_m=1500, timeout_s=25
    )
    assert query.count('nwr["tourism"="viewpoint"]') <= 40


def test_the_bbox_query_is_south_west_north_east():
    query = build_bbox_query(
        (10.0, 47.0, 10.2, 47.1), resolve_categories(["viewpoint"]), timeout_s=9
    )
    assert 'nwr["tourism"="viewpoint"](47.00000,10.00000,47.10000,10.20000);' in query


@respx.mock
async def test_along_posts_the_query_with_an_identifying_user_agent():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=PAYLOAD))
    pois = await fetcher().along(LINE, resolve_categories(None), buffer_m=1500)
    assert len(pois) == 5
    request = route.calls.last.request
    assert b"nwr" in request.content and b"around" not in request.content
    assert "bike-routing-agent" in request.headers["user-agent"]


@respx.mock
async def test_a_second_identical_search_is_served_from_the_cache():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=PAYLOAD))
    f = fetcher(cache=InMemoryTTLCache())
    first = await f.along(LINE, resolve_categories(None), buffer_m=1500)
    second = await f.along(LINE, resolve_categories(None), buffer_m=1500)
    assert route.call_count == 1 and first == second
    await f.along(LINE, resolve_categories(None), buffer_m=2500)  # other radius: a new search
    assert route.call_count == 2
    await f.in_bbox((10.0, 47.0, 10.2, 47.1), resolve_categories(None))
    assert route.call_count == 3


@respx.mock
async def test_a_busy_instance_hands_over_to_the_next_one():
    other = "https://overpass2.example/api/interpreter"
    first = respx.post(URL).mock(return_value=httpx.Response(504, text="<html>timeout</html>"))
    second = respx.post(other).mock(return_value=httpx.Response(200, json=PAYLOAD))
    pois = await OverpassPoiFetcher(base_urls=[URL, other]).in_bbox(
        (10.0, 47.0, 10.2, 47.1), resolve_categories(None)
    )
    assert len(pois) == 5 and first.call_count == 1 and second.call_count == 1


@respx.mock
async def test_html_error_pages_with_status_200_count_as_a_failure():
    other = "https://overpass2.example/api/interpreter"
    respx.post(URL).mock(return_value=httpx.Response(200, text="<html>too busy</html>"))
    respx.post(other).mock(return_value=httpx.Response(200, json=PAYLOAD))
    pois = await OverpassPoiFetcher(base_urls=[URL, other]).in_bbox(
        (10.0, 47.0, 10.2, 47.1), resolve_categories(None)
    )
    assert len(pois) == 5


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(429), ProviderRateLimitError),
        (httpx.Response(504, text="x"), ProviderUnavailableError),
        (httpx.Response(200, text="<html>"), ProviderBadResponseError),
    ],
)
@respx.mock
async def test_when_every_instance_fails_the_last_error_is_raised(response, error):
    respx.post(URL).mock(return_value=response)
    with pytest.raises(error):
        await fetcher().in_bbox((10.0, 47.0, 10.2, 47.1), resolve_categories(None))


@respx.mock
async def test_timeouts_and_connection_errors_are_provider_errors():
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderTimeoutError):
        await fetcher().in_bbox((10.0, 47.0, 10.2, 47.1), resolve_categories(None))
    respx.post(URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(ProviderUnavailableError):
        await fetcher().in_bbox((10.0, 47.0, 10.2, 47.1), resolve_categories(None))


def test_a_route_needs_two_points_and_a_url():
    with pytest.raises(ValueError):
        OverpassPoiFetcher(base_urls=[])
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(fetcher().along([(10.0, 47.0)], resolve_categories(None), buffer_m=100))


def test_a_real_corridor_response_parses_into_ranked_ready_pois():
    """Elements captured from the public Overpass for Braunschweig -> Goslar (ways and
    relations carry `center`, most carry a wikidata id, some also a wikipedia tag)."""
    import json
    from pathlib import Path

    fixture = json.loads(
        (
            Path(__file__).resolve().parents[1] / "fixtures" / "overpass_poi_corridor.json"
        ).read_text()
    )
    pois = parse_pois(fixture, resolve_categories(None))
    assert len(pois) == len(fixture["elements"]) == 21
    by_id = {p.id: p for p in pois}
    dom = by_id["way/22978953"]
    assert (dom.name, dom.wikidata, dom.wikipedia) == (
        "Braunschweiger Dom",
        "Q279402",
        "de:Braunschweiger Dom",
    )
    assert 52.2 < dom.lat < 52.3 and 10.4 < dom.lon < 10.6  # taken from `center`
    assert by_id["relation/331104"].name == "Schloss Wolfenbüttel"
    assert by_id["node/91597511"].name is None and by_id["node/91597511"].wikidata == "Q130072672"
    # A mapper's `en:` tag is kept as written (the resolver handles any language).
    assert by_id["way/243689993"].wikipedia == "en:Dankwarderode Castle"
    assert {p.category for p in pois} >= {"historic", "museum", "religious", "nature", "viewpoint"}
    assert all(p.fame is None for p in pois)


@pytest.mark.parametrize(
    "bad_answer",
    [{"version": 0.6}, {"elements": "nope"}, ["a", "list"], {"elements": None}],
)
@respx.mock
async def test_a_200_that_is_not_an_element_list_hands_over_to_the_next_instance(bad_answer):
    other = "https://overpass2.example/api/interpreter"
    first = respx.post(URL).mock(return_value=httpx.Response(200, json=bad_answer))
    second = respx.post(other).mock(return_value=httpx.Response(200, json=PAYLOAD))
    cache = InMemoryTTLCache()
    f = OverpassPoiFetcher(base_urls=[URL, other], cache=cache)
    pois = await f.in_bbox((10.0, 47.0, 10.2, 47.1), resolve_categories(None))
    assert len(pois) == 5 and first.call_count == 1 and second.call_count == 1


@respx.mock
async def test_when_every_instance_answers_garbage_the_error_is_a_bad_response():
    respx.post(URL).mock(return_value=httpx.Response(200, json={"version": 0.6}))
    with pytest.raises(ProviderBadResponseError):
        await fetcher().in_bbox((10.0, 47.0, 10.2, 47.1), resolve_categories(None))
