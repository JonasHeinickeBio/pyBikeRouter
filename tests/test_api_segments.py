"""BRouter map tile downloads and the per-request engine choice, through the API."""

import httpx
import pytest
from httpx import ASGITransport

import bike_routing_agent.api as api_module
from bike_routing_agent.config import Settings
from bike_routing_agent.models import RoutePlanAPIRequest
from bike_routing_agent.providers.brouter_downloads import (
    DownloadBusyError,
    DownloadJob,
    SegmentInfo,
    SegmentProgress,
)


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=ASGITransport(app=api_module.app), base_url="http://test"
    ) as c:
        yield c


class StubDownloader:
    def __init__(self, *, busy=False):
        self.busy = busy
        self.started = []
        self.job_state = DownloadJob(
            id="j1", state="running", segments=[SegmentProgress(name="W5_N50")]
        )

    def free_bytes(self):
        return 5 * 2**30

    async def inspect(self, names):
        if any(not n.startswith(("E", "W")) for n in names):
            raise ValueError("not BRouter tile names")
        return [SegmentInfo(name=n, present=n == "E10_N50", size_bytes=139_294_448) for n in names]

    def start(self, names):
        if self.busy:
            raise DownloadBusyError("a map download is already running")
        self.started.append(names)
        return self.job_state

    def job(self, job_id):
        return self.job_state if job_id == "j1" else None


@pytest.fixture
def stub(monkeypatch):
    downloader = StubDownloader()
    monkeypatch.setattr(api_module, "_segment_downloader", downloader)
    return downloader


async def test_segments_info_tells_presence_and_size_before_anything_is_downloaded(client, stub):
    body = (await client.get("/v1/routing/segments?names=W5_N50,E10_N50")).json()
    assert body["free_bytes"] == 5 * 2**30
    assert [(s["name"], s["present"], s["size_bytes"]) for s in body["segments"]] == [
        ("W5_N50", False, 139_294_448),
        ("E10_N50", True, 139_294_448),
    ]
    assert stub.started == []
    assert (await client.get("/v1/routing/segments?names=nonsense")).status_code == 422


async def test_a_download_is_started_and_polled(client, stub):
    started = await client.post("/v1/routing/segments/download", json={"segments": ["W5_N50"]})
    assert started.status_code == 202 and started.json()["id"] == "j1"
    assert stub.started == [["W5_N50"]]
    polled = (await client.get("/v1/routing/segments/download/j1")).json()
    assert polled["state"] == "running" and polled["segments"][0]["name"] == "W5_N50"
    assert (await client.get("/v1/routing/segments/download/other")).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"segments": []},
        {"segments": ["../W5_N50"]},
        {"segments": ["W5_N50; rm -rf /"]},
        {"segments": ["w5_n50"]},
        {"segments": [f"E{5 * i}_N50" for i in range(5)]},
        {},
    ],
)
async def test_bad_download_requests_never_reach_the_downloader(client, stub, body):
    response = await client.post("/v1/routing/segments/download", json=body)
    assert response.status_code == 422 and stub.started == []


async def test_a_second_download_while_one_runs_is_a_conflict(client, monkeypatch):
    monkeypatch.setattr(api_module, "_segment_downloader", StubDownloader(busy=True))
    response = await client.post("/v1/routing/segments/download", json={"segments": ["W5_N50"]})
    assert response.status_code == 409 and "already running" in response.json()["detail"]


async def test_the_endpoints_are_503_when_downloads_are_not_set_up(client, monkeypatch):
    monkeypatch.setattr(api_module, "_segment_downloader", None)
    assert (await client.get("/v1/routing/segments?names=W5_N50")).status_code == 503
    post = await client.post("/v1/routing/segments/download", json={"segments": ["W5_N50"]})
    assert post.status_code == 503
    assert (await client.get("/v1/routing/segments/download/j1")).status_code == 503
    assert (await client.get("/v1/capabilities")).json()["segment_downloads"] is False


async def test_capabilities_say_downloads_are_possible_and_which_engines_can_be_named(client, stub):
    caps = (await client.get("/v1/capabilities")).json()
    assert caps["segment_downloads"] is True
    assert set(caps["engines"]) <= {"ors", "brouter", "valhalla"} and caps["engines"]


def test_downloads_need_a_writable_tile_folder(tmp_path):
    assert api_module.build_segment_downloader(Settings(_env_file=None)) is None
    assert (
        api_module.build_segment_downloader(
            Settings(_env_file=None, brouter_segments_dir=str(tmp_path / "missing"))
        )
        is None
    )
    ok = api_module.build_segment_downloader(
        Settings(_env_file=None, brouter_segments_dir=str(tmp_path), brouter_segments_max_mb=300)
    )
    assert ok is not None and ok._max_bytes == 300 * 2**20


def test_segment_settings_are_validated():
    for bad in (
        {"brouter_segments_max_mb": 0},
        {"brouter_segments_url": "http://evil.example/tiles/"},
        {"brouter_segments_url": "ftp://x/"},
    ):
        with pytest.raises(ValueError):
            Settings(_env_file=None, **bad)
    Settings(_env_file=None, brouter_segments_url="http://127.0.0.1:9000/tiles/")  # a mirror


# ------------------------------------------------------------- per-request engines


def test_routing_engines_are_validated():
    base = {"origin": "A", "destination": "B"}
    assert RoutePlanAPIRequest.model_validate({**base, "routing_engines": ["ors"]}).routing_engines
    for bad in ([], ["ors", "ors"], ["osrm"], "ors"):
        with pytest.raises(ValueError):
            RoutePlanAPIRequest.model_validate({**base, "routing_engines": bad})
    assert RoutePlanAPIRequest.model_validate(base).routing_engines is None


class RecordingGraph:
    def __init__(self):
        self.inputs = []

    async def ainvoke(self, state):
        self.inputs.append(state["raw_input"])
        return {"status": "no_route", "errors": []}


async def test_the_engine_choice_reaches_the_graph(client, monkeypatch):
    graph = RecordingGraph()
    monkeypatch.setattr(api_module, "_graph", graph)
    await client.post(
        "/v1/route/plan", json={"origin": "A", "destination": "B", "routing_engines": ["ors"]}
    )
    await client.post("/v1/route/plan", json={"origin": "A", "destination": "B"})
    assert graph.inputs[0]["routing_engines"] == ["ors"]
    assert graph.inputs[1]["routing_engines"] is None


def test_optional_engines_are_the_ones_that_can_work_and_are_not_configured_yet():
    configured = api_module.build_routing_providers(
        Settings(_env_file=None, routing_provider="brouter")
    )
    names = lambda cfg, have: [  # noqa: E731
        p.name for p in api_module.build_optional_routing_providers(cfg, have)
    ]
    # BRouter configured: ORS is offered only with a key (or a self-hosted URL), never Valhalla.
    assert names(Settings(_env_file=None, routing_provider="brouter"), configured) == []
    assert names(
        Settings(_env_file=None, routing_provider="brouter", ors_api_key="k"), configured
    ) == ["ors"]
    assert names(
        Settings(_env_file=None, routing_provider="brouter", ors_base_url="http://ors.lan:8080"),
        configured,
    ) == ["ors"]
    # ORS configured: BRouter is offered.
    ors = api_module.build_routing_providers(Settings(_env_file=None, ors_api_key="k"))
    assert names(Settings(_env_file=None, ors_api_key="k"), ors) == ["brouter"]
    # Everything configured: nothing left to offer.
    every = api_module.build_routing_providers(Settings(_env_file=None, routing_provider="all"))
    assert names(Settings(_env_file=None, routing_provider="all"), every) == []
