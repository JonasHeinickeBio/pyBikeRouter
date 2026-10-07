"""Readiness monitor and /readyz (issue #25). No network: probes are fakes."""

import asyncio
from pathlib import Path

import httpx
import pytest
import respx

from bike_routing_agent.health import (
    Component,
    HealthMonitor,
    build_health_monitor,
    summarize,
)
from bike_routing_agent.providers.geocoder import NominatimGeocoder
from bike_routing_agent.providers.pelias import PeliasGeocoder
from bike_routing_agent.storage.artifacts import LocalArtifactStore


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def probe_returning(payload, calls=None):
    async def probe():
        if calls is not None:
            calls.append(1)
        return payload

    return probe


def monitor(*components, ttl_s=30.0, timeout_s=1.0, clock=None):
    return HealthMonitor(components, timeout_s=timeout_s, ttl_s=ttl_s, clock=clock or Clock())


async def verdict(*components, **kw):
    report = await monitor(*components, **kw).check()
    return report.status, report.ready


OK = {"status": "ok"}
DOWN = {"status": "unavailable", "error": "connect refused to http://secret-host:8080"}


# -- aggregation rules ---------------------------------------------------------


async def test_single_engine_must_be_ready():
    geocoder = Component("nominatim", "geocoder", probe_returning(OK))
    assert await verdict(Component("ors", "routing", probe_returning(OK)), geocoder) == (
        "ok",
        True,
    )
    assert await verdict(Component("ors", "routing", probe_returning(DOWN)), geocoder) == (
        "unavailable",
        False,
    )


async def test_multi_engine_one_working_engine_is_ready_but_degraded():
    status, ready = await verdict(
        Component("ors", "routing", probe_returning(OK)),
        Component("brouter", "routing", probe_returning(DOWN)),
        Component(
            "valhalla", "routing", probe_returning({"status": "degraded", "status_code": 503})
        ),
        Component("nominatim", "geocoder", probe_returning(OK)),
    )
    assert (status, ready) == ("degraded", True)


async def test_all_engines_down_is_unavailable():
    assert await verdict(
        Component("ors", "routing", probe_returning(DOWN)),
        Component("brouter", "routing", probe_returning(DOWN)),
        Component("nominatim", "geocoder", probe_returning(OK)),
    ) == ("unavailable", False)


async def test_geocoder_down_makes_the_instance_unready():
    assert await verdict(
        Component("ors", "routing", probe_returning(OK)),
        Component("nominatim", "geocoder", probe_returning(DOWN)),
    ) == ("unavailable", False)


async def test_unknown_is_ready_not_a_failure():
    """The public ORS has no health endpoint: absent evidence is not an outage."""
    assert await verdict(
        Component("ors", "routing", probe_returning({"status": "unknown"})),
        Component("nominatim", "geocoder", probe_returning(OK)),
    ) == ("ok", True)


async def test_optional_component_failure_degrades_but_never_gates():
    components = [
        Component("ors", "routing", probe_returning(OK)),
        Component("nominatim", "geocoder", probe_returning(OK)),
        Component("database", "database", probe_returning(DOWN), required=False),
    ]
    assert await verdict(*components) == ("degraded", True)
    components[2] = Component("database", "database", probe_returning(DOWN), required=True)
    assert await verdict(*components) == ("unavailable", False)


def test_summarize_without_routing_components_depends_on_the_rest():
    assert summarize([]).ready is True


# -- sanitising ----------------------------------------------------------------


async def test_adapter_text_never_reaches_the_report():
    report = await monitor(
        Component("ors", "routing", probe_returning(DOWN)),
        Component("v", "routing", probe_returning({"status": "unknown", "detail": "http://x/v2"})),
    ).check()
    dumped = str(report.as_dict())
    assert "secret-host" not in dumped and "http://" not in dumped
    assert {c.detail for c in report.components} == {
        "unreachable",
        "no health signal from this deployment",
    }


async def test_degraded_reports_only_the_status_code():
    report = await monitor(
        Component("v", "routing", probe_returning({"status": "degraded", "status_code": 503})),
        Component("w", "routing", probe_returning({"status": "degraded", "status_code": True})),
    ).check()
    assert [c.detail for c in report.components] == ["HTTP 503", "degraded"]


async def test_garbage_probe_results_become_unknown():
    report = await monitor(
        Component("a", "routing", probe_returning({"status": "fantastic"})),
        Component("b", "routing", probe_returning({})),
    ).check()
    assert [c.status for c in report.components] == ["unknown", "unknown"]


async def test_probe_exception_is_unavailable_without_leaking_the_message():
    async def boom():
        raise RuntimeError("password=hunter2 at http://db.internal")

    report = await monitor(Component("ors", "routing", boom)).check()
    only = report.components[0]
    assert (only.status, only.detail) == ("unavailable", "probe failed")
    assert "hunter2" not in str(report.as_dict())


async def test_probe_timeout_is_unavailable():
    async def slow():
        await asyncio.sleep(5)
        return OK

    report = await monitor(Component("ors", "routing", slow), timeout_s=0.05).check()
    assert (report.components[0].status, report.components[0].detail) == (
        "unavailable",
        "probe timed out",
    )


# -- caching -------------------------------------------------------------------


async def test_results_are_cached_for_the_ttl_then_refreshed():
    calls: list[int] = []
    clock = Clock()
    m = monitor(Component("ors", "routing", probe_returning(OK, calls)), ttl_s=30, clock=clock)
    await m.check()
    await m.check()
    assert len(calls) == 1
    clock.now += 31
    await m.check()
    assert len(calls) == 2


async def test_concurrent_checks_probe_each_component_once():
    calls: list[int] = []

    async def slowish():
        calls.append(1)
        await asyncio.sleep(0.05)
        return OK

    m = monitor(Component("ors", "routing", slowish))
    await asyncio.gather(m.check(), m.check(), m.check())
    assert len(calls) == 1


async def test_component_ttl_overrides_the_default():
    calls: list[int] = []
    clock = Clock()
    m = monitor(
        Component("g", "geocoder", probe_returning(OK, calls), ttl_s=300),
        ttl_s=30,
        clock=clock,
    )
    await m.check()
    clock.now += 100
    await m.check()
    assert len(calls) == 1
    clock.now += 250
    await m.check()
    assert len(calls) == 2


async def test_failures_are_cached_too_so_a_dead_upstream_is_not_hammered():
    calls: list[int] = []
    m = monitor(Component("ors", "routing", probe_returning(DOWN, calls)))
    await m.check()
    await m.check()
    assert len(calls) == 1


# -- composition ---------------------------------------------------------------


class _Engine:
    def __init__(self, name, result):
        self.name = name
        self._result = result

    async def health(self):
        return self._result


class _NoHealth:
    name = "plain-geocoder"


class _Store:
    def __init__(self, result):
        self._result = result

    def ping(self):
        return self._result


def build(**overrides):
    kwargs = dict(
        geocoder=_Engine("nominatim", OK),
        routing_providers=[_Engine("ors", OK), _Engine("brouter", DOWN)],
        artifact_store=_Store(OK),
        history=None,
        database_is_critical=False,
        timeout_s=1.0,
        ttl_s=30.0,
        geocoder_ttl_s=300.0,
    )
    kwargs.update(overrides)
    return build_health_monitor(**kwargs)


async def test_build_health_monitor_covers_the_running_objects():
    report = await build().check()
    assert [(c.kind, c.name) for c in report.components] == [
        ("routing", "ors"),
        ("routing", "brouter"),
        ("geocoder", "nominatim"),
        ("artifact_store", "artifact_store"),
    ]
    assert (report.status, report.ready) == ("degraded", True)
    # several engines: none is individually required; a lone one is
    assert [c.required for c in report.components if c.kind == "routing"] == [False, False]
    lone = await build(routing_providers=[_Engine("ors", OK)]).check()
    assert [c.required for c in lone.components if c.kind == "routing"] == [True]


async def test_objects_without_a_probe_are_unknown_not_failed():
    report = await build(geocoder=_NoHealth(), artifact_store=object()).check()
    by_name = {c.name: c.status for c in report.components}
    assert by_name["plain-geocoder"] == "unknown" and by_name["artifact_store"] == "unknown"
    assert report.ready is True


async def test_database_gates_readiness_only_when_exports_live_there():
    history = _Store(DOWN)
    soft = await build(history=history, database_is_critical=False).check()
    assert (soft.status, soft.ready) == ("degraded", True)
    hard = await build(history=history, database_is_critical=True).check()
    assert (hard.status, hard.ready) == ("unavailable", False)


# -- real probes -----------------------------------------------------------------


def test_local_artifact_store_ping_checks_writability(tmp_path: Path):
    assert LocalArtifactStore(tmp_path).ping() == {"status": "ok"}
    # directory not created yet: judged by its nearest existing parent
    assert LocalArtifactStore(tmp_path / "later" / "deeper").ping() == {"status": "ok"}
    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o500)
    try:
        import os

        if os.geteuid() != 0:  # root ignores permission bits
            assert LocalArtifactStore(read_only).ping() == {"status": "unavailable"}
    finally:
        read_only.chmod(0o700)


def test_local_artifact_store_ping_on_a_file_is_unavailable(tmp_path: Path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert LocalArtifactStore(blocker).ping() == {"status": "unavailable"}


@respx.mock
async def test_nominatim_health_uses_status_not_search():
    route = respx.get("https://nominatim.example/status").mock(
        return_value=httpx.Response(200, text="OK")
    )
    geocoder = NominatimGeocoder(
        base_url="https://nominatim.example", user_agent="t/1", timeout_s=2.0
    )
    assert await geocoder.health() == {"status": "ok"}
    assert route.calls.last.request.headers["user-agent"] == "t/1"

    respx.get("https://nominatim.example/status").mock(return_value=httpx.Response(503))
    assert await geocoder.health() == {"status": "degraded", "status_code": 503}

    respx.get("https://nominatim.example/status").mock(side_effect=httpx.ConnectError("no"))
    assert await geocoder.health() == {"status": "unavailable"}


async def test_nominatim_health_uses_an_injected_client():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/status"
        return httpx.Response(200, text="OK")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        geocoder = NominatimGeocoder(
            base_url="https://n.example", user_agent="t", timeout_s=1.0, client=client
        )
        assert await geocoder.health() == {"status": "ok"}


async def test_pelias_health_is_the_ors_health():
    class _Client:
        async def health(self):
            return {"status": "ok"}

    assert await PeliasGeocoder(client=_Client()).health() == {"status": "ok"}


def test_postgres_ping_never_raises(monkeypatch):
    from bike_routing_agent.storage import postgres
    from bike_routing_agent.storage.postgres import PostgresDatabase

    db = PostgresDatabase("postgresql://nowhere/none")

    def explode():
        raise RuntimeError("could not connect to server at secret-host")

    monkeypatch.setattr(postgres, "_import_psycopg", explode)
    assert db.ping() == {"status": "unavailable"}


def test_postgres_ping_uses_the_open_pool_and_leaves_closed_pools_closed(monkeypatch):
    from bike_routing_agent.storage import postgres
    from bike_routing_agent.storage.postgres import (
        PostgresArtifactStore,
        PostgresDatabase,
        PostgresRouteHistory,
    )

    class _Conn:
        def execute(self, sql):
            assert sql == "SELECT 1"

    class _Ctx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *exc):
            return False

    class _Pool:
        def connection(self, timeout=None):
            assert timeout == 5.0
            return _Ctx()

    db = PostgresDatabase("postgresql://x/y")
    db._pool = _Pool()
    assert db.ping() == {"status": "ok"}
    assert PostgresRouteHistory(db).ping() == {"status": "ok"}
    assert PostgresArtifactStore(db).ping() == {"status": "ok"}

    # closed pool: a single direct connection, and the pool stays closed
    class _Driver:
        @staticmethod
        def connect(url, connect_timeout):
            assert (url, connect_timeout) == ("postgresql://x/y", 5)
            return _Ctx()

    closed = PostgresDatabase("postgresql://x/y")
    monkeypatch.setattr(postgres, "_import_psycopg", lambda: (_Driver, None))
    assert closed.ping() == {"status": "ok"}
    assert closed._pool is None


# -- endpoint ------------------------------------------------------------------


@pytest.fixture
async def client():
    from bike_routing_agent.api import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_readyz_200_when_ready_and_503_when_not(client, monkeypatch):
    import bike_routing_agent.api as api_module

    good = monitor(
        Component("ors", "routing", probe_returning(OK)),
        Component("nominatim", "geocoder", probe_returning(OK)),
    )
    monkeypatch.setattr(api_module, "_health_monitor", good)
    response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["ready"]) == ("ok", True)
    assert {c["name"] for c in body["components"]} == {"ors", "nominatim"}

    bad = monitor(
        Component("ors", "routing", probe_returning(DOWN)),
        Component("nominatim", "geocoder", probe_returning(OK)),
    )
    monkeypatch.setattr(api_module, "_health_monitor", bad)
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert "secret-host" not in response.text


async def test_healthz_stays_a_pure_liveness_check(client, monkeypatch):
    import bike_routing_agent.api as api_module

    monkeypatch.setattr(
        api_module,
        "_health_monitor",
        monitor(Component("ors", "routing", probe_returning(DOWN))),
    )
    response = await client.get("/healthz")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


async def test_default_app_monitor_reports_the_configured_components(client, monkeypatch):
    """The real wiring: component set follows the running configuration."""
    import bike_routing_agent.api as api_module

    names = {c.name for c in api_module._health_monitor._components}
    assert "artifact_store" in names
    assert api_module._geocode_provider.name in names
    assert {p.name for p in api_module._routing_providers} <= names


async def test_a_dead_cache_degrades_but_never_makes_the_instance_unready():
    class _Cache:
        async def ping(self):
            return DOWN

    report = await build(cache=_Cache()).check()
    cache = next(c for c in report.components if c.kind == "cache")
    assert (cache.status, cache.required) == ("unavailable", False)
    assert (report.status, report.ready) == ("degraded", True)


async def test_an_in_memory_cache_has_nothing_to_probe():
    class _Plain:
        pass

    report = await build(cache=_Plain()).check()
    assert all(c.kind != "cache" for c in report.components)


async def test_a_weather_outage_degrades_but_never_makes_the_instance_unready():
    class _Weather:
        async def health(self):
            return DOWN

    report = await build(weather=_Weather()).check()
    weather = next(c for c in report.components if c.kind == "weather")
    assert (weather.status, weather.required) == ("unavailable", False)
    assert (report.status, report.ready) == ("degraded", True)
