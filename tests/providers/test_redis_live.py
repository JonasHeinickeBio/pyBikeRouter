"""Redis cache against a real server (live tier; needs TEST_REDIS_URL).

    docker compose -f docker/compose.yaml --profile cache up -d redis
    TEST_REDIS_URL=redis://127.0.0.1:6379/0 pytest -m live tests/providers/test_redis_live.py

Uses a random key prefix per test and removes its keys afterwards; the server's
other data is never touched.
"""

import asyncio
import os
import uuid

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("TEST_REDIS_URL"), reason="TEST_REDIS_URL not set"),
]

redis = pytest.importorskip("redis")

from bike_routing_agent.providers.redis_cache import RedisCacheBackend  # noqa: E402


@pytest.fixture
async def cache():
    backend = RedisCacheBackend(
        os.environ["TEST_REDIS_URL"], prefix=f"test-{uuid.uuid4().hex}", timeout_s=2.0
    )
    yield backend
    async for key in backend._client.scan_iter(match=f"{backend._prefix}*"):
        await backend._client.delete(key)
    await backend.aclose()


async def test_round_trip_ttl_and_ping(cache):
    assert await cache.ping() == {"status": "ok"}
    assert await cache.get("k") is None
    await cache.set("k", {"label": "Köln", "n": [1, 2]}, ttl_s=60)
    assert await cache.get("k") == {"label": "Köln", "n": [1, 2]}
    assert 0 < await cache._client.pttl(cache._key("k")) <= 60_000
    await cache.set("short", 1, ttl_s=0.1)
    await asyncio.sleep(0.3)
    assert await cache.get("short") is None


async def test_two_instances_share_entries(cache):
    other = RedisCacheBackend(os.environ["TEST_REDIS_URL"], prefix=cache._prefix.rsplit(":v", 1)[0])
    try:
        await cache.set("shared", "from-a", ttl_s=60)
        assert await other.get("shared") == "from-a"
    finally:
        await other.aclose()


async def test_an_unreachable_server_degrades_to_misses_quickly():
    dead = RedisCacheBackend("redis://127.0.0.1:1/0", timeout_s=0.5)
    try:
        assert await dead.get("k") is None
        await dead.set("k", 1, ttl_s=60)
        assert dead.circuit_open
        assert await dead.ping() == {"status": "unavailable"}
    finally:
        await dead.aclose()
