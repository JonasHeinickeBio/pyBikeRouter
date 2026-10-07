"""Redis cache backend (issue #29): behaviour with fakeredis, failure injection included."""

import asyncio
import logging

import httpx
import pytest
import respx

fakeredis = pytest.importorskip("fakeredis")

from bike_routing_agent.errors import ProviderError  # noqa: E402
from bike_routing_agent.providers.base import NamespacedCache  # noqa: E402
from bike_routing_agent.providers.geocoder import NominatimGeocoder  # noqa: E402
from bike_routing_agent.providers.redis_cache import (  # noqa: E402
    SCHEMA_VERSION,
    RedisCacheBackend,
)

NOMINATIM = "https://nominatim.example/search"


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def server():
    return fakeredis.FakeServer()


def backend(server, **kwargs):
    client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    return RedisCacheBackend("redis://unused", client=client, **kwargs), client


class FailingClient:
    """A client whose every call raises; counts how often it was asked."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def get(self, *a, **k):
        self.calls += 1
        raise self.error

    async def set(self, *a, **k):
        self.calls += 1
        raise self.error

    async def ping(self, *a, **k):
        self.calls += 1
        raise self.error


# -- the cache contract ----------------------------------------------------------


async def test_miss_then_hit_round_trips_json(server):
    cache, _ = backend(server)
    assert await cache.get("k") is None
    value = [{"label": "Café, Düsseldorf", "coordinate": {"lon": 6.8, "lat": 51.2}}]
    await cache.set("k", value, ttl_s=60)
    assert await cache.get("k") == value


async def test_entries_expire(server):
    cache, _ = backend(server)
    await cache.set("k", {"a": 1}, ttl_s=0.05)
    assert await cache.get("k") == {"a": 1}
    await asyncio.sleep(0.15)
    assert await cache.get("k") is None


async def test_keys_carry_the_prefix_and_schema_version(server):
    cache, client = backend(server)
    await cache.set("abc", 1, ttl_s=60)
    assert await client.keys("*") == [f"bike-routing:v{SCHEMA_VERSION}:abc"]
    other, _ = backend(server, prefix="other")
    assert await other.get("abc") is None  # another prefix never sees it


async def test_a_ttl_is_always_set(server):
    cache, client = backend(server)
    await cache.set("k", 1, ttl_s=60)
    assert 0 < await client.pttl(f"bike-routing:v{SCHEMA_VERSION}:k") <= 60_000
    await cache.set("tiny", 1, ttl_s=0.0001)  # rounds up to 1 ms, never "no expiry"
    assert await client.pttl(f"bike-routing:v{SCHEMA_VERSION}:tiny") in (1, 0, -2)


async def test_values_that_are_not_json_are_skipped_not_raised(server, caplog):
    cache, client = backend(server)
    with caplog.at_level(logging.WARNING):
        await cache.set("k", {"x": object()}, ttl_s=60)
    assert await client.keys("*") == []
    assert "not JSON-serialisable" in caplog.text
    assert not cache.circuit_open  # a bad value is not an outage


async def test_a_corrupt_entry_is_a_miss_and_not_an_outage(server, caplog):
    cache, client = backend(server)
    await client.set(f"bike-routing:v{SCHEMA_VERSION}:k", "{not json")
    with caplog.at_level(logging.WARNING):
        assert await cache.get("k") is None
    assert "not valid JSON" in caplog.text and not cache.circuit_open


async def test_namespaced_views_do_not_collide(server):
    cache, client = backend(server)
    geocode, overpass = NamespacedCache(cache, "geocode"), NamespacedCache(cache, "overpass")
    await geocode.set("same", "g", ttl_s=60)
    await overpass.set("same", "o", ttl_s=60)
    assert (await geocode.get("same"), await overpass.get("same")) == ("g", "o")
    assert sorted(await client.keys("*")) == [
        f"bike-routing:v{SCHEMA_VERSION}:geocode:same",
        f"bike-routing:v{SCHEMA_VERSION}:overpass:same",
    ]


# -- failure behaviour -----------------------------------------------------------


@pytest.mark.parametrize(
    "error", [ConnectionError("refused"), TimeoutError("slow"), OSError("network down")]
)
async def test_redis_errors_degrade_to_a_miss_and_never_raise(error):
    cache = RedisCacheBackend("redis://x", client=FailingClient(error))
    assert await cache.get("k") is None
    await cache.set("k", 1, ttl_s=60)  # must not raise either


async def test_redis_specific_errors_are_also_swallowed():
    from redis.exceptions import ConnectionError as RedisConnectionError

    cache = RedisCacheBackend("redis://x", client=FailingClient(RedisConnectionError("gone")))
    assert await cache.get("k") is None


async def test_the_circuit_skips_redis_during_an_outage_then_retries():
    clock = Clock()
    client = FailingClient(ConnectionError("down"))
    cache = RedisCacheBackend("redis://x", client=client, retry_after_s=30, clock=clock)

    assert await cache.get("a") is None
    assert client.calls == 1 and cache.circuit_open
    for _ in range(5):  # a dead server must not add its timeout to every request
        assert await cache.get("b") is None
        await cache.set("c", 1, ttl_s=60)
    assert client.calls == 1

    clock.now += 31
    assert not cache.circuit_open
    assert await cache.get("d") is None
    assert client.calls == 2  # one probe after the pause


async def test_one_warning_per_outage(caplog):
    clock = Clock()
    cache = RedisCacheBackend(
        "redis://x", client=FailingClient(ConnectionError("down")), retry_after_s=30, clock=clock
    )
    with caplog.at_level(logging.WARNING):
        await cache.get("a")
        await cache.get("b")
        await cache.set("c", 1, ttl_s=1)
    assert caplog.text.count("redis cache get failed") == 1


async def test_ping_reports_status_without_raising(server):
    ok, _ = backend(server)
    assert await ok.ping() == {"status": "ok"}
    dead = RedisCacheBackend("redis://x", client=FailingClient(ConnectionError("down")))
    assert await dead.ping() == {"status": "unavailable"}


async def test_aclose_closes_the_client(server):
    cache, client = backend(server)
    await cache.aclose()  # fakeredis closes cleanly; calling it twice is fine too
    await cache.aclose()


def test_the_real_client_is_built_lazily_without_connecting():
    cache = RedisCacheBackend("redis://127.0.0.1:1/0", prefix="p", timeout_s=0.1)
    assert cache._client is not None  # no connection attempted at construction


# -- sharing between instances ----------------------------------------------------


def nominatim_payload(label="Braunschweig"):
    return [
        {
            "place_id": 1,
            "lat": "52.27",
            "lon": "10.52",
            "display_name": label,
            "importance": 0.9,
            "type": "city",
            "category": "boundary",
            "address": {},
        }
    ]


def geocoder(cache):
    return NominatimGeocoder(
        base_url="https://nominatim.example",
        user_agent="t/1",
        timeout_s=2.0,
        cache=NamespacedCache(cache, "geocode"),
    )


@respx.mock
async def test_a_second_instance_reuses_the_first_instances_geocode_result(server):
    route = respx.get(NOMINATIM).mock(return_value=httpx.Response(200, json=nominatim_payload()))
    instance_a, _ = backend(server)
    instance_b, _ = backend(server)

    first = await geocoder(instance_a).geocode("Braunschweig")
    second = await geocoder(instance_b).geocode("Braunschweig")

    assert route.call_count == 1  # instance B never asked Nominatim
    assert [c.label for c in second] == [c.label for c in first]


@respx.mock
async def test_geocoding_keeps_working_while_redis_is_down():
    route = respx.get(NOMINATIM).mock(return_value=httpx.Response(200, json=nominatim_payload()))
    dead = RedisCacheBackend("redis://x", client=FailingClient(ConnectionError("down")))

    gc = geocoder(dead)
    assert (await gc.geocode("Braunschweig"))[0].label == "Braunschweig"
    await gc.geocode("Braunschweig")
    assert route.call_count == 2  # uncached, but never failed


@respx.mock
async def test_http_errors_do_not_poison_the_cache(server):
    respx.get(NOMINATIM).mock(return_value=httpx.Response(500))
    cache, client = backend(server)
    with pytest.raises(ProviderError):
        await geocoder(cache).geocode("Braunschweig")
    assert await client.keys("*") == []
