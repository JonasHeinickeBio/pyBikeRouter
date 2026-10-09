"""The Wikimedia resolver against responses captured from the real services."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from bike_routing_agent.errors import ProviderRateLimitError
from bike_routing_agent.poi.wikimedia import WikimediaResolver
from bike_routing_agent.providers.base import InMemoryTTLCache

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
WIKIDATA = "https://wikidata.example/w/api.php"
WIKIPEDIA = "https://{lang}.wikipedia.example"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def resolver(**kw) -> WikimediaResolver:
    return WikimediaResolver(wikidata_url=WIKIDATA, wikipedia_url=WIKIPEDIA, **kw)


@respx.mock
async def test_fame_is_the_number_of_sitelinks():
    route = respx.get(WIKIDATA).mock(
        return_value=httpx.Response(200, json=fixture("wikidata_sitelinks_raw.json"))
    )
    fame = await resolver().fame(["Q4152", "Q1527304", "not-a-qid"])
    assert fame == {"Q4152": 93, "Q1527304": 10}
    request = route.calls.last.request
    assert request.url.params["ids"] == "Q1527304|Q4152"  # sorted, one batch, junk dropped
    assert "bike-routing-agent" in request.headers["user-agent"]


@respx.mock
async def test_fame_batches_in_fifties_and_caches_per_item():
    payload = {"entities": {}}
    route = respx.get(WIKIDATA).mock(return_value=httpx.Response(200, json=payload))
    ids = [f"Q{n}" for n in range(1, 121)]
    await resolver().fame(ids)
    assert route.call_count == 3

    # Cached after a successful lookup: the second call asks nothing.
    cache = InMemoryTTLCache()
    respx.get(WIKIDATA).mock(
        return_value=httpx.Response(200, json=fixture("wikidata_sitelinks_raw.json"))
    )
    cached = resolver(cache=cache)
    await cached.fame(["Q4152"])
    before = respx.calls.call_count
    assert await cached.fame(["Q4152"]) == {"Q4152": 93}
    assert respx.calls.call_count == before


@respx.mock
async def test_fame_leaves_out_what_could_not_be_looked_up():
    respx.get(WIKIDATA).mock(return_value=httpx.Response(503))
    assert await resolver().fame(["Q4152"]) == {}
    respx.get(WIKIDATA).mock(return_value=httpx.Response(429))
    assert await resolver().fame(["Q4152"]) == {}


@respx.mock
async def test_missing_entities_are_not_given_a_fame():
    respx.get(WIKIDATA).mock(
        return_value=httpx.Response(200, json={"entities": {"Q999999999": {"missing": ""}}})
    )
    assert await resolver().fame(["Q999999999"]) == {}


@respx.mock
async def test_titles_resolve_to_wikidata_items_and_unknown_pages_stay_unknown():
    route = respx.get("https://de.wikipedia.example/w/api.php").mock(
        return_value=httpx.Response(200, json=fixture("wikipedia_pageprops_raw.json"))
    )
    found = await resolver().wikidata_for_titles(
        ["de:Schloss Neuschwanstein", "de:Hohes Schloss (Füssen)", "not a tag"]
    )
    assert found == {"de:Schloss Neuschwanstein": "Q4152"}
    assert route.call_count == 1


@respx.mock
async def test_titles_follow_normalisation_and_redirects():
    payload = {
        "query": {
            "normalized": [{"from": "Schloss_Neuschwanstein", "to": "Schloss Neuschwanstein"}],
            "redirects": [{"from": "Neuschwanstein", "to": "Schloss Neuschwanstein"}],
            "pages": [
                {
                    "title": "Schloss Neuschwanstein",
                    "pageprops": {"wikibase_item": "Q4152"},
                }
            ],
        }
    }
    respx.get("https://de.wikipedia.example/w/api.php").mock(
        return_value=httpx.Response(200, json=payload)
    )
    found = await resolver().wikidata_for_titles(["de:Schloss_Neuschwanstein", "de:Neuschwanstein"])
    assert found == {
        "de:Schloss_Neuschwanstein": "Q4152",
        "de:Neuschwanstein": "Q4152",
    }


@respx.mock
async def test_info_combines_wikidata_wikipedia_and_the_sister_projects():
    respx.get(WIKIDATA).mock(
        return_value=httpx.Response(200, json=fixture("wikidata_entity_raw.json"))
    )
    summary = respx.get(
        "https://de.wikipedia.example/api/rest_v1/page/summary/Schloss_Neuschwanstein"
    ).mock(return_value=httpx.Response(200, json=fixture("wikipedia_summary_neuschwanstein.json")))

    info = await resolver().info(
        wikidata="Q4152",
        wikipedia=None,
        osm_id="way/123",
        website="https://www.neuschwanstein.de",
        lang="de",
    )
    assert summary.called
    assert info.title == "Schloss Neuschwanstein"
    assert info.language == "de" and info.extract and "Neuschwanstein" in info.extract
    assert info.description == "Schloss in Bayern"
    assert info.thumbnail_url and info.thumbnail_url.startswith("https://thumb.wikimedia.org/")
    assert info.sitelinks == 93
    kinds = {link.kind: link.url for link in info.links}
    assert kinds["wikipedia"].startswith("https://de.wikipedia.org/wiki/")
    assert kinds["wikivoyage"] == "https://de.wikivoyage.org/wiki/Neuschwanstein"
    assert kinds["commons"].startswith("https://commons.wikimedia.org/wiki/")
    assert kinds["wikidata"] == "https://www.wikidata.org/wiki/Q4152"
    assert kinds["osm"] == "https://www.openstreetmap.org/way/123"
    assert kinds["website"] == "https://www.neuschwanstein.de"
    assert any("OpenStreetMap" in line for line in info.attribution)


@respx.mock
async def test_info_falls_back_to_the_osm_wikipedia_tag_without_wikidata():
    respx.get("https://de.wikipedia.example/api/rest_v1/page/summary/Schloss_Neuschwanstein").mock(
        return_value=httpx.Response(200, json=fixture("wikipedia_summary_neuschwanstein.json"))
    )
    info = await resolver().info(
        wikidata=None,
        wikipedia="de:Schloss Neuschwanstein",
        osm_id=None,
        website=None,
        lang="en",
    )
    assert info.extract and info.language == "de"
    assert [link.kind for link in info.links] == ["wikipedia"]


@respx.mock
async def test_info_is_partial_when_a_service_is_down_and_does_not_cache_that():
    respx.get(WIKIDATA).mock(return_value=httpx.Response(503))
    cache = InMemoryTTLCache()
    r = resolver(cache=cache)
    info = await r.info(wikidata="Q4152", wikipedia=None, osm_id="node/1", website=None, lang="en")
    assert info.extract is None and info.title is None
    assert {link.kind for link in info.links} == {"wikidata", "osm"}  # what we know locally
    # The outage was not remembered: the next call asks again.
    respx.get(WIKIDATA).mock(
        return_value=httpx.Response(200, json=fixture("wikidata_entity_raw.json"))
    )
    respx.get(host="de.wikipedia.example").mock(return_value=httpx.Response(404))
    again = await r.info(wikidata="Q4152", wikipedia=None, osm_id=None, website=None, lang="de")
    assert again.title == "Schloss Neuschwanstein"


@respx.mock
async def test_a_disambiguation_page_is_not_used_as_the_description():
    respx.get(WIKIDATA).mock(
        return_value=httpx.Response(200, json=fixture("wikidata_entity_raw.json"))
    )
    respx.get(host="de.wikipedia.example").mock(
        return_value=httpx.Response(200, json={"type": "disambiguation", "extract": "x"})
    )
    info = await resolver().info(
        wikidata="Q4152", wikipedia=None, osm_id=None, website=None, lang="de"
    )
    assert info.extract is None
    # The article link is still offered.
    assert any(link.kind == "wikipedia" for link in info.links)


@respx.mock
async def test_thumbnails_outside_wikimedia_are_dropped():
    summary = {
        **fixture("wikipedia_summary_neuschwanstein.json"),
        "thumbnail": {"source": "https://evil.example/x.png", "width": 1, "height": 1},
    }
    respx.get(host="de.wikipedia.example").mock(return_value=httpx.Response(200, json=summary))
    info = await resolver().info(
        wikidata=None, wikipedia="de:Schloss Neuschwanstein", osm_id=None, website=None, lang="de"
    )
    assert info.thumbnail_url is None


async def test_info_rejects_a_language_that_could_become_a_host_name():
    with pytest.raises(ValueError):
        await resolver().info(
            wikidata=None, wikipedia=None, osm_id=None, website=None, lang="evil.com/x"
        )


@respx.mock
async def test_rate_limits_and_timeouts_are_reported_as_provider_errors():
    r = resolver()
    respx.get(WIKIDATA).mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderRateLimitError):
        await r._get_json(WIKIDATA, {})
    respx.get(WIKIDATA).mock(side_effect=httpx.ConnectTimeout("slow"))
    from bike_routing_agent.errors import ProviderTimeoutError

    with pytest.raises(ProviderTimeoutError):
        await r._get_json(WIKIDATA, {})


@respx.mock
async def test_malformed_wikidata_answers_leave_fame_unknown_instead_of_raising():
    r = resolver()
    for payload in (
        {"entities": []},
        {"entities": {"Q1": {"sitelinks": ["not", "a", "dict"]}}},
        {"entities": {"Q1": "oops"}},
        {},
    ):
        respx.get(WIKIDATA).mock(return_value=httpx.Response(200, json=payload))
        assert await r.fame(["Q1"]) == {}
    respx.get(WIKIDATA).mock(return_value=httpx.Response(200, json=["a", "list"]))
    assert await r.fame(["Q1"]) == {}  # not an object at all


@respx.mock
async def test_malformed_wikipedia_page_properties_are_skipped_not_fatal():
    payload = {
        "query": {
            "normalized": [{"to": "no from"}, "garbage", {"from": "a", "to": "b"}],
            "redirects": None,
            "pages": [
                "garbage",
                {"no": "title"},
                {"title": "Burg", "pageprops": "not a dict"},
                {"title": "Schloss", "pageprops": {"wikibase_item": "Q7"}},
            ],
        }
    }
    respx.get("https://de.wikipedia.example/w/api.php").mock(
        return_value=httpx.Response(200, json=payload)
    )
    found = await resolver().wikidata_for_titles(["de:Burg", "de:Schloss"])
    assert found == {"de:Schloss": "Q7"}
    respx.get("https://de.wikipedia.example/w/api.php").mock(
        return_value=httpx.Response(200, json={"query": []})
    )
    assert await resolver().wikidata_for_titles(["de:Burg"]) == {}


@respx.mock
async def test_an_odd_summary_shape_costs_the_description_not_the_poi():
    respx.get(WIKIDATA).mock(
        return_value=httpx.Response(200, json=fixture("wikidata_entity_raw.json"))
    )
    odd = {"extract": "Text", "content_urls": ["not", "a", "dict"], "thumbnail": "x"}
    respx.get(host="de.wikipedia.example").mock(return_value=httpx.Response(200, json=odd))
    info = await resolver().info(
        wikidata="Q4152", wikipedia=None, osm_id="way/5", website=None, lang="de"
    )
    assert {link.kind for link in info.links} >= {"wikidata", "osm"}  # still useful


async def test_a_language_can_only_ever_be_a_language_code_in_a_host_name():
    r = resolver()
    for bad in ("evil.com/x", "en\n", "EN", "e", "en-", "a@b", "en.evil", "../x", ""):
        with pytest.raises(ValueError):
            r._wikipedia_base(bad)
        with pytest.raises(ValueError):
            await r.info(wikidata=None, wikipedia=None, osm_id=None, website=None, lang=bad)
    assert r._wikipedia_base("de") == "https://de.wikipedia.example"
    assert r._wikipedia_base("zh-yue") == "https://zh-yue.wikipedia.example"
    # A title never ends up in the host: bad tags are dropped before any request.
    assert await r.wikidata_for_titles(["evil.com/x:Title", "de\n:Title"]) == {}
