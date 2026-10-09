"""Full-stack API tests: real FastAPI app + real graph wiring, with only the
outbound HTTP calls to Nominatim/ORS mocked at the transport layer.

Unlike tests/graph/test_graph.py (which drives the graph directly with fake
providers), these exercise bike_routing_agent.api end to end -- dependency
construction, the compiled graph, and the request/response mapping -- the
same code path a real client hits.
"""

import shutil

import httpx
import pytest
import respx

from bike_routing_agent.api import _export_dir, app

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ORS_URL = "https://api.openrouteservice.org/v2/directions/cycling-regular/geojson"


@pytest.fixture(autouse=True)
def _clean_export_dir():
    yield
    shutil.rmtree(_export_dir, ignore_errors=True)


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_healthz(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_frontend_index_is_served_at_root(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "pyBikeRouter" in response.text


async def test_frontend_static_assets_are_served(client):
    for path, marker in [
        ("/app.js", "route/plan"),
        ("/app.js", "max_alternatives"),
        ("/app.js", "departure_time"),
        ("/weather.js", "BikeWeather"),
        ("/text-planning.js", "BikeText"),
        ("/alternatives.js", "BikeAlternatives"),
        ("/export.js", "BikeExport"),
        ("/pois.js", "BikePois"),
        ("/coverage.js", "BikeCoverage"),
        ("/index.html", 'id="coverage-card"'),
        ("/index.html", 'src="coverage.js"'),
        ("/app.js", "v1/pois/along-route"),
        ("/index.html", 'id="poi-categories"'),
        ("/index.html", 'src="pois.js"'),
        ("/index.html", 'id="alt-download"'),
        ("/index.html", 'id="alternatives-list"'),
        ("/app.js", "route/plan-text"),
        ("/app.js", "v1/capabilities"),
        ("/index.html", 'id="text-input"'),
        ("/index.html", 'id="departure-preset"'),
        ("/index.html", 'id="weather-card"'),
        ("/index.html", 'id="max-alternatives"'),
        ("/styles.css", "--accent"),
    ]:
        response = await client.get(path)
        assert response.status_code == 200
        assert marker in response.text


async def test_plan_route_missing_field_rejected_before_graph_runs(client):
    response = await client.post("/v1/route/plan", json={"origin": "Berlin"})
    assert response.status_code == 422
    # The destination requirement is a model-level contract now (issue #5),
    # so the error speaks about the whole body, not one field.
    assert any("destination is required" in err["msg"] for err in response.json()["detail"])


async def test_plan_route_invalid_constraints_rejected_before_graph_runs(client):
    response = await client.post(
        "/v1/route/plan",
        json={
            "origin": "Berlin",
            "destination": "Hamburg",
            "constraints": {"target_distance_km": -5},
        },
    )
    assert response.status_code == 422


@respx.mock
async def test_plan_route_ready_end_to_end(
    client, ors_directions_response, nominatim_single_response
):
    respx.get(NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_single_response[:1])
    )
    respx.post(ORS_URL).mock(return_value=httpx.Response(200, json=ors_directions_response))

    shutil.rmtree(_export_dir, ignore_errors=True)
    response = await client.post(
        "/v1/route/plan",
        json={
            "origin": "Braunschweig Hauptbahnhof",
            "destination": "Wolfenbuettel",
            "constraints": {"bike_type": "gravel", "max_ascent_m": 500},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["route"]["provider"] == "ors"
    assert body["route"]["raw_provider_response"] is None
    assert body["explanation"]
    assert set(body["artifacts"]) == {"geojson_url", "gpx_url"}

    geojson_resp = await client.get(body["artifacts"]["geojson_url"])
    assert geojson_resp.status_code == 200
    assert geojson_resp.json()["type"] == "Feature"

    gpx_resp = await client.get(body["artifacts"]["gpx_url"])
    assert gpx_resp.status_code == 200
    assert b"<gpx" in gpx_resp.content

    # Issue #6: single-engine mode still exposes the candidate list -- the
    # selected route, repeated, with raw provider payloads stripped.
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["provider"] == "ors"
    assert body["candidates"][0]["raw_provider_response"] is None
    assert body["route"] == body["candidates"][0]


@respx.mock
async def test_plan_route_ambiguous_geocoding_returns_clarification(
    client, nominatim_ambiguous_response
):
    respx.get(NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_ambiguous_response)
    )

    response = await client.post(
        "/v1/route/plan", json={"origin": "Springfield", "destination": "Chicago"}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "awaiting_clarification"
    assert len(body["clarification"][0]["candidates"]) == len(nominatim_ambiguous_response)
    assert body["route"] is None


@respx.mock
async def test_plan_route_ors_failure_becomes_structured_error(client, nominatim_single_response):
    respx.get(NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_single_response[:1])
    )
    respx.post(ORS_URL).mock(
        return_value=httpx.Response(401, json={"error": "Authorization field missing"})
    )

    response = await client.post(
        "/v1/route/plan",
        json={"origin": "Braunschweig Hauptbahnhof", "destination": "Wolfenbuettel"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "provider_failure"
    assert body["errors"][0]["provider"] == "ors"


async def test_get_unknown_artifact_returns_404(client):
    response = await client.get("/v1/routes/deadbeefdeadbeefdeadbeefdeadbeef.geojson")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/v1/routes/nope.geojson",
        "/v1/routes/deadbeef.geojson",
        "/v1/routes/deadbeefdeadbeefdeadbeefdeadbeee.geojson",
        "/v1/routes/deadbeefdeadbeefdeadbeefdeadbeef.exe",
        "/v1/routes/deadbeefdeadbeefdeadbeefdeadbeef.geojson.txt",
    ],
)
async def test_artifact_route_rejects_anything_off_the_safe_filename_pattern(client, path):
    response = await client.get(path)
    assert response.status_code == 404


async def test_artifact_route_rejects_path_traversal(client):
    response = await client.get("/v1/routes/..%2F..%2F..%2Fetc%2Fpasswd.geojson")
    assert response.status_code == 404


# ------------------------------------------------- candidates (issue #6)


class _StubGraph:
    """Stands in for the compiled LangGraph: returns a fixed final state."""

    def __init__(self, final_state: dict) -> None:
        self._final_state = final_state

    async def ainvoke(self, _initial_state: dict) -> dict:
        return self._final_state


def _candidate_dict(provider: str, score: float | None, geometry: dict) -> dict:
    return {
        "provider": provider,
        "provider_profile": f"{provider}-profile",
        "geometry_geojson": geometry,
        "metrics": {"distance_m": 1000.0, "duration_s": 600.0},
        "score": score,
        "score_breakdown": {},
        "warnings": [],
        "provenance": {},
        "raw_provider_response": {"internal": "must-not-leak"},
    }


def _line(coords: list[tuple[float, float]]) -> dict:
    return {"type": "LineString", "coordinates": [list(c) for c in coords]}


def _ready_state(entries: list[tuple[str, float | None]]) -> dict:
    """A ready final state where each provider scores its own line."""
    candidates = [
        _candidate_dict(provider, score, _line([(0.0, float(i)), (1.0, float(i))]))
        for i, (provider, score) in enumerate(entries)
    ]
    best = max(
        candidates,
        key=lambda c: c["score"] if c["score"] is not None else float("-inf"),
    )
    return {
        "status": "ready",
        "selected_candidate": best,
        "candidates": candidates,
        "explanation": "ok",
        "artifacts": {},
        "errors": [],
    }


def _best_first(entries: list[tuple[str, float | None]]) -> list[str]:
    return [
        provider
        for provider, _ in sorted(
            entries,
            key=lambda entry: entry[1] if entry[1] is not None else float("-inf"),
            reverse=True,
        )
    ]


@pytest.mark.parametrize(
    "entries",
    [
        [("ors", 0.9), ("brouter", 0.7), ("valhalla", 0.5)],
        # Input order deliberately shuffled: sorting must not depend on it.
        [("valhalla", 0.8), ("ors", 0.9), ("brouter", 0.6)],
        # A null-scored candidate (scorer could not score it) sorts last.
        [("ors", 0.9), ("brouter", None), ("valhalla", 0.7)],
    ],
)
async def test_plan_route_candidates_sorted_best_first(client, monkeypatch, entries) -> None:
    import bike_routing_agent.api as api_module

    monkeypatch.setattr(api_module, "_graph", _StubGraph(_ready_state(entries)))

    response = await client.post(
        "/v1/route/plan",
        json={"origin": "1.0, 2.0", "destination": "3.0, 4.0"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert [c["provider"] for c in body["candidates"]] == _best_first(entries)
    # The selected candidate is repeated verbatim as the first entry.
    assert body["route"] == body["candidates"][0]
    # Raw provider payloads never leave the API.
    assert body["route"]["raw_provider_response"] is None
    for cand in body["candidates"]:
        assert cand["raw_provider_response"] is None


# ---------------------------------------------------------------- loops (issue #5)


async def test_loop_with_destination_is_rejected(client):
    response = await client.post(
        "/v1/route/plan",
        json={
            "origin": "Berlin",
            "destination": "Potsdam",
            "constraints": {"return_to_origin": True, "target_distance_km": 15},
        },
    )
    assert response.status_code == 422
    assert any("destination must be omitted" in err["msg"] for err in response.json()["detail"])


async def test_loop_without_target_distance_is_rejected(client):
    response = await client.post(
        "/v1/route/plan",
        json={"origin": "Berlin", "constraints": {"return_to_origin": True}},
    )
    assert response.status_code == 422
    assert any("target_distance_km" in err["msg"] for err in response.json()["detail"])


@respx.mock
async def test_plan_route_loop_ready_end_to_end(
    client, ors_directions_response, nominatim_single_response
):
    respx.get(NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_single_response[:1])
    )
    respx.post(ORS_URL).mock(return_value=httpx.Response(200, json=ors_directions_response))

    shutil.rmtree(_export_dir, ignore_errors=True)
    response = await client.post(
        "/v1/route/plan",
        json={
            "origin": "Braunschweig Hauptbahnhof",
            "constraints": {"return_to_origin": True, "target_distance_km": 15},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert "loop back to the start" in body["explanation"]


# ---------------------------------------------------------------------------
# Route history and artifact store wiring (issue #7)
# ---------------------------------------------------------------------------


@pytest.fixture
def history(monkeypatch):
    from bike_routing_agent import api as api_module
    from bike_routing_agent.storage.history import InMemoryRouteHistory

    store = InMemoryRouteHistory()
    monkeypatch.setattr(api_module, "_history", store)
    return store


async def _plan_ready(client, nominatim_single_response, ors_directions_response):
    respx.get(NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_single_response[:1])
    )
    respx.post(ORS_URL).mock(return_value=httpx.Response(200, json=ors_directions_response))
    response = await client.post(
        "/v1/route/plan",
        json={"origin": "Braunschweig Hauptbahnhof", "destination": "Wolfenbuettel"},
    )
    assert response.status_code == 200
    return response.json()


@respx.mock
async def test_ready_plan_is_recorded_and_queryable(
    client, history, ors_directions_response, nominatim_single_response
):
    body = await _plan_ready(client, nominatim_single_response, ors_directions_response)

    # The history id is the artifact id, so a download links back to its plan.
    assert body["plan_id"] is not None
    assert body["artifacts"]["geojson_url"] == f"/v1/routes/{body['plan_id']}.geojson"

    listing = await client.get("/v1/history/plans", params={"provider": "ors"})
    assert listing.status_code == 200
    [summary] = listing.json()
    assert summary["plan_id"] == body["plan_id"]
    assert summary["status"] == "ready"
    assert summary["candidates"][0]["provider"] == "ors"
    assert summary["candidates"][0]["selected"] is True
    assert "geometry_geojson" not in summary["candidates"][0]

    detail = await client.get(f"/v1/history/plans/{body['plan_id']}")
    assert detail.status_code == 200
    record = detail.json()
    assert record["request"]["origin"] == "Braunschweig Hauptbahnhof"
    assert record["artifacts"]["geojson_file"] == f"{body['plan_id']}.geojson"
    stored = record["candidates"][0]["candidate"]
    assert stored["geometry_geojson"]["type"] == "LineString"
    assert stored["raw_provider_response"] is None


@respx.mock
async def test_non_ready_plans_are_recorded_too(client, history, nominatim_ambiguous_response):
    respx.get(NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_ambiguous_response)
    )

    response = await client.post(
        "/v1/route/plan", json={"origin": "Springfield", "destination": "Chicago"}
    )

    body = response.json()
    assert body["status"] == "awaiting_clarification"
    assert body["plan_id"] is not None
    listing = await client.get("/v1/history/plans", params={"status": "awaiting_clarification"})
    assert [p["plan_id"] for p in listing.json()] == [body["plan_id"]]
    assert listing.json()[0]["candidates"] == []


@respx.mock
async def test_history_failure_never_fails_the_plan(
    client, monkeypatch, caplog, ors_directions_response, nominatim_single_response
):
    from bike_routing_agent import api as api_module

    class BrokenHistory:
        def save(self, record):
            raise RuntimeError("database is down")

    monkeypatch.setattr(api_module, "_history", BrokenHistory())

    body = await _plan_ready(client, nominatim_single_response, ors_directions_response)

    assert body["status"] == "ready"
    assert body["plan_id"] is None
    assert "failed to record plan" in caplog.text


@respx.mock
async def test_plan_id_is_null_without_history(
    client, monkeypatch, ors_directions_response, nominatim_single_response
):
    from bike_routing_agent import api as api_module

    monkeypatch.setattr(api_module, "_history", None)

    body = await _plan_ready(client, nominatim_single_response, ors_directions_response)

    assert body["status"] == "ready"
    assert body["plan_id"] is None


@pytest.mark.parametrize(
    "path", ["/v1/history/plans", "/v1/history/stats", f"/v1/history/plans/{'a' * 32}"]
)
async def test_history_endpoints_are_503_without_a_database(client, monkeypatch, path):
    from bike_routing_agent import api as api_module

    monkeypatch.setattr(api_module, "_history", None)

    response = await client.get(path)

    assert response.status_code == 503
    assert "DATABASE_URL" in response.json()["detail"]


async def test_history_unknown_or_malformed_plan_id_is_404(client, history):
    assert (await client.get(f"/v1/history/plans/{'a' * 32}")).status_code == 404
    assert (await client.get("/v1/history/plans/not-an-id")).status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {"bbox": "1,2,3"},
        {"bbox": "a,b,c,d"},
        {"bbox": "5,0,1,1"},
        {"bbox": "0,0,1,95"},
        {"limit": 0},
        {"limit": 1000},
        {"offset": -1},
        {"status": "bogus"},
    ],
)
async def test_history_query_validation(client, history, params):
    response = await client.get("/v1/history/plans", params=params)

    assert response.status_code == 422


async def test_history_query_passes_every_filter_through(client, history):
    seen = {}
    history.query = lambda f: seen.setdefault("filter", f) and []

    response = await client.get(
        "/v1/history/plans",
        params={
            "provider": "ors",
            "profile": "cycling-road",
            "status": "ready",
            "bike_type": "road",
            "selected_only": "true",
            "since": "2026-09-01T00:00:00Z",
            "until": "2026-10-01T00:00:00Z",
            "bbox": "10,52,11,53",
            "limit": 5,
            "offset": 10,
        },
    )

    assert response.status_code == 200
    f = seen["filter"]
    assert (f.provider, f.profile) == ("ors", "cycling-road")
    assert (f.status, f.bike_type) == ("ready", "road")
    assert f.selected_only is True
    assert f.bbox == (10.0, 52.0, 11.0, 53.0)
    assert (f.limit, f.offset) == (5, 10)
    assert f.since is not None and f.since.tzinfo is not None and f.until is not None


async def test_artifacts_are_served_through_the_artifact_store(client, monkeypatch):
    from bike_routing_agent import api as api_module

    class DictStore:
        data = {"a" * 32 + ".geojson": b"{}", "a" * 32 + ".gpx": b"<gpx/>"}

        def put(self, name, content):
            raise AssertionError("read-only here")

        def get(self, name):
            return self.data.get(name)

    monkeypatch.setattr(api_module, "_artifact_store", DictStore())

    geojson = await client.get(f"/v1/routes/{'a' * 32}.geojson")
    gpx = await client.get(f"/v1/routes/{'a' * 32}.gpx")
    missing = await client.get(f"/v1/routes/{'b' * 32}.geojson")

    assert geojson.status_code == 200
    assert geojson.headers["content-type"].startswith("application/geo+json")
    assert gpx.headers["content-type"].startswith("application/gpx+xml")
    assert missing.status_code == 404


@respx.mock
async def test_history_stats_reflect_recorded_plans(
    client, history, ors_directions_response, nominatim_single_response
):
    await _plan_ready(client, nominatim_single_response, ors_directions_response)

    response = await client.get("/v1/history/stats")

    assert response.status_code == 200
    stats = response.json()
    assert stats["total_plans"] == 1
    assert stats["by_status"] == {"ready": 1}
    assert stats["ready_rate"] == 1.0
    [ors] = stats["providers"]
    assert (ors["provider"], ors["candidates"], ors["selected"], ors["win_rate"]) == (
        "ors",
        1,
        1,
        1.0,
    )
    assert ors["mean_score"] is not None
    assert len(stats["daily"]) == 1 and stats["daily"][0]["total"] == 1


async def test_history_stats_passes_filters_through(client, history):
    seen = {}

    def fake_stats(stats_filter):
        seen["filter"] = stats_filter
        return history.__class__().stats(stats_filter)

    history.stats = fake_stats

    response = await client.get(
        "/v1/history/stats",
        params={
            "bike_type": "road",
            "since": "2026-09-01T00:00:00Z",
            "until": "2026-10-01T00:00:00Z",
        },
    )

    assert response.status_code == 200
    f = seen["filter"]
    assert f.bike_type == "road"
    assert f.since is not None and f.until is not None


async def test_history_stats_rejects_malformed_timestamps(client, history):
    assert (await client.get("/v1/history/stats", params={"since": "yesterday"})).status_code == 422


async def test_dashboard_page_is_served(client):
    page = await client.get("/dashboard.html")
    script = await client.get("/dashboard.js")

    assert page.status_code == 200
    assert "history/stats" in script.text
    assert "dashboard.js" in page.text


# ---------------------------------------------------------------------------
# iPhone / home-screen app (docs/mobile.md)
# ---------------------------------------------------------------------------


async def test_pwa_manifest_is_served_and_references_real_icons(client):
    response = await client.get("/manifest.webmanifest")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    manifest = response.json()
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "./"
    for icon in manifest["icons"]:
        asset = await client.get("/" + icon["src"])
        assert asset.status_code == 200
        assert asset.headers["content-type"] == "image/png"


async def test_index_declares_the_iphone_home_screen_app(client):
    page = (await client.get("/")).text

    assert "viewport-fit=cover" in page
    assert 'name="apple-mobile-web-app-capable"' in page
    assert 'rel="manifest" href="manifest.webmanifest"' in page
    apple_icon = await client.get("/icons/apple-touch-icon.png")
    assert apple_icon.status_code == 200
    assert 'rel="apple-touch-icon" href="icons/apple-touch-icon.png"' in page


# ------------------------------------------- ranked alternatives (issue #24)


class _CapturingGraph(_StubGraph):
    def __init__(self, final_state: dict) -> None:
        super().__init__(final_state)
        self.initial_states: list[dict] = []

    async def ainvoke(self, initial_state: dict) -> dict:
        self.initial_states.append(initial_state)
        return await super().ainvoke(initial_state)


@pytest.mark.parametrize("bad", [0, 6, -1, "many"])
async def test_max_alternatives_is_validated_before_the_graph_runs(client, bad):
    response = await client.post(
        "/v1/route/plan",
        json={"origin": "A", "destination": "B", "max_alternatives": bad},
    )
    assert response.status_code == 422


async def test_max_alternatives_reaches_the_graph(client, monkeypatch):
    import bike_routing_agent.api as api_module

    graph = _CapturingGraph(_ready_state([("ors", 0.9)]))
    monkeypatch.setattr(api_module, "_graph", graph)

    await client.post("/v1/route/plan", json={"origin": "1.0, 2.0", "destination": "3.0, 4.0"})
    await client.post(
        "/v1/route/plan",
        json={"origin": "1.0, 2.0", "destination": "3.0, 4.0", "max_alternatives": 3},
    )
    assert graph.initial_states[0]["raw_input"]["max_alternatives"] is None
    assert graph.initial_states[1]["raw_input"]["max_alternatives"] == 3


async def test_response_orders_by_rank_and_exposes_rank_fields(client, monkeypatch):
    import bike_routing_agent.api as api_module

    state = _ready_state([("ors", 0.9), ("brouter", 0.8)])
    # Ranked by the score node: the better-scored candidate is NOT first here,
    # proving the API trusts rank over a re-sort by score.
    state["candidates"][0].update(rank=2, rank_rationale="rank 2: x", duplicate_of="b/p")
    state["candidates"][1].update(
        rank=1, rank_rationale="rank 1: y", duplicates=["ors/ors-profile"]
    )
    monkeypatch.setattr(api_module, "_graph", _StubGraph(state))

    body = (
        await client.post("/v1/route/plan", json={"origin": "1.0, 2.0", "destination": "3.0, 4.0"})
    ).json()
    assert [c["provider"] for c in body["candidates"]] == ["brouter", "ors"]
    assert [c["rank"] for c in body["candidates"]] == [1, 2]
    assert body["candidates"][0]["duplicates"] == ["ors/ors-profile"]
    assert body["candidates"][1]["duplicate_of"] == "b/p"
    assert body["candidates"][1]["rank_rationale"] == "rank 2: x"


async def test_full_graph_ranks_duplicate_engine_results(client, monkeypatch):
    """Real score node inside the real graph: two engines, one street."""
    import bike_routing_agent.api as api_module
    from bike_routing_agent.graph import build_graph

    class _Geocoder:
        name = "fake"

        async def geocode(self, query, *, limit=5):  # pragma: no cover - coordinates only
            raise AssertionError("coordinates need no geocoding")

    class _Engine:
        def __init__(self, name, lat_offset, distance_m):
            self.name = name
            self._offset = lat_offset
            self._distance = distance_m

        async def route(self, request):
            from bike_routing_agent.models import RouteCandidate, RouteMetrics

            lat = 52.0 + self._offset
            return RouteCandidate(
                provider=self.name,
                provider_profile="p",
                geometry_geojson={
                    "type": "LineString",
                    "coordinates": [[10.0, lat], [10.01, lat], [10.02, lat]],
                },
                metrics=RouteMetrics(distance_m=self._distance, ascent_m=5.0),
            )

        async def health(self):  # pragma: no cover
            return {"status": "ok"}

    graph = build_graph(
        geocode_provider=_Geocoder(),
        routing_providers=[
            _Engine("ors", 0.0, 1_400.0),
            _Engine("brouter", 0.00003, 1_401.0),  # ~3 m north: the same street
            _Engine("valhalla", 0.01, 1_600.0),  # ~1.1 km north: a different route
        ],
        artifact_store=api_module._artifact_store,
    )
    monkeypatch.setattr(api_module, "_graph", graph)
    request = {
        "origin": {"lon": 10.0, "lat": 52.0},
        "destination": {"lon": 10.02, "lat": 52.0},
        "constraints": {"target_distance_km": 1.4},
    }

    everything = (await client.post("/v1/route/plan", json=request)).json()
    assert [c["provider"] for c in everything["candidates"]] == ["ors", "brouter", "valhalla"]
    assert everything["candidates"][1]["duplicate_of"] == "ors/p"

    distinct = (await client.post("/v1/route/plan", json={**request, "max_alternatives": 5})).json()
    assert [c["provider"] for c in distinct["candidates"]] == ["ors", "valhalla"]
    assert distinct["candidates"][0]["duplicates"] == ["brouter/p"]
    assert distinct["route"] == distinct["candidates"][0]


# ------------------------------------------- S3 artifacts (issue #27)


class _FakeS3Store:
    def __init__(self) -> None:
        self.gets: list[str] = []

    def get(self, name: str) -> bytes | None:
        self.gets.append(name)
        return b"{}"

    def presigned_url(self, name: str, ttl_s: int) -> str:
        return f"https://s3.example/bucket/{name}?expires={ttl_s}"


_ARTIFACT = "a" * 32 + ".geojson"


async def test_artifacts_are_streamed_by_default(client, monkeypatch):
    import bike_routing_agent.api as api_module

    store = _FakeS3Store()
    monkeypatch.setattr(api_module, "_artifact_store", store)
    monkeypatch.setattr(api_module.settings, "s3_presigned_url_ttl_s", None)

    response = await client.get(f"/v1/routes/{_ARTIFACT}")

    assert response.status_code == 200 and response.content == b"{}"
    assert store.gets == [_ARTIFACT]


async def test_presigned_redirect_is_opt_in(client, monkeypatch):
    import bike_routing_agent.api as api_module

    store = _FakeS3Store()
    monkeypatch.setattr(api_module, "_artifact_store", store)
    monkeypatch.setattr(api_module.settings, "s3_presigned_url_ttl_s", 120)

    response = await client.get(f"/v1/routes/{_ARTIFACT}")

    assert response.status_code == 307
    assert response.headers["location"] == f"https://s3.example/bucket/{_ARTIFACT}?expires=120"
    assert store.gets == []  # the bytes never pass through this process


async def test_presign_setting_is_ignored_by_stores_that_cannot_presign(client, monkeypatch):
    import bike_routing_agent.api as api_module

    class _Plain:
        def get(self, name):
            return b"x"

    monkeypatch.setattr(api_module, "_artifact_store", _Plain())
    monkeypatch.setattr(api_module.settings, "s3_presigned_url_ttl_s", 120)

    response = await client.get(f"/v1/routes/{_ARTIFACT}")
    assert response.status_code == 200 and response.content == b"x"


async def test_malformed_names_never_reach_the_presigner(client, monkeypatch):
    import bike_routing_agent.api as api_module

    store = _FakeS3Store()
    monkeypatch.setattr(api_module, "_artifact_store", store)
    monkeypatch.setattr(api_module.settings, "s3_presigned_url_ttl_s", 120)

    assert (await client.get("/v1/routes/..%2Fsecret.geojson")).status_code == 404
    assert (await client.get("/v1/routes/short.geojson")).status_code == 404


# --------------------------------------------------------------- weather


async def test_a_departure_too_far_ahead_is_rejected_before_the_graph_runs(client):
    from datetime import UTC, datetime, timedelta

    far = (datetime.now(UTC) + timedelta(days=40)).isoformat()
    response = await client.post(
        "/v1/route/plan", json={"origin": "A", "destination": "B", "departure_time": far}
    )
    assert response.status_code == 422
    assert any("departure_time" in str(e) for e in response.json()["detail"])


async def test_departure_time_reaches_the_graph_as_utc_iso(client, monkeypatch):
    import bike_routing_agent.api as api_module

    graph = _CapturingGraph(_ready_state([("ors", 0.9)]))
    monkeypatch.setattr(api_module, "_graph", graph)
    for sent in ("2026-10-07T15:00:00Z", "2026-10-07T17:00:00+02:00", "2026-10-07T15:00:00"):
        await client.post(
            "/v1/route/plan",
            json={"origin": "1.0, 2.0", "destination": "3.0, 4.0", "departure_time": sent},
        )
    await client.post("/v1/route/plan", json={"origin": "1.0, 2.0", "destination": "3.0, 4.0"})
    sent_values = [s["raw_input"]["departure_time"] for s in graph.initial_states]
    assert sent_values[0] == "2026-10-07T15:00:00+00:00"
    assert sent_values[1] == "2026-10-07T17:00:00+02:00"
    assert sent_values[2] == "2026-10-07T15:00:00+00:00"  # naive = UTC
    assert sent_values[3] is None


async def test_weather_status_is_part_of_the_response(client, monkeypatch):
    import bike_routing_agent.api as api_module

    state = {**_ready_state([("ors", 0.9)]), "weather_status": "unavailable"}
    monkeypatch.setattr(api_module, "_graph", _StubGraph(state))
    body = (
        await client.post("/v1/route/plan", json={"origin": "1.0, 2.0", "destination": "3.0, 4.0"})
    ).json()
    assert body["weather_status"] == "unavailable" and body["route"]["weather"] is None


async def test_a_full_plan_carries_weather_and_mentions_it_in_the_explanation(client, monkeypatch):
    import bike_routing_agent.api as api_module
    from bike_routing_agent.graph import build_graph
    from bike_routing_agent.weather.models import HourlyWeather
    from bike_routing_agent.weather.service import WeatherService

    class _Geocoder:
        name = "fake"

        async def geocode(self, query, *, limit=5):  # pragma: no cover - coordinates only
            raise AssertionError

    class _Engine:
        name = "ors"

        async def route(self, request):
            from bike_routing_agent.models import RouteCandidate, RouteMetrics

            return RouteCandidate(
                provider="ors",
                provider_profile="p",
                geometry_geojson={
                    "type": "LineString",
                    "coordinates": [[10.0, 52.0], [10.0, 52.1], [10.0, 52.2]],
                },
                metrics=RouteMetrics(distance_m=22_000, duration_s=4_800, ascent_m=40),
            )

        async def health(self):  # pragma: no cover
            return {"status": "ok"}

    class _Weather:
        name = "fake-weather"
        attribution = "Weather by Fake (CC BY 4.0)"

        async def forecast(self, points, start, end):
            from datetime import timedelta

            return [
                [
                    HourlyWeather(
                        time=start + timedelta(hours=i),
                        temperature_c=9.0 + i,
                        wind_speed_kmh=24.0,
                        wind_gust_kmh=55.0,
                        wind_from_deg=0.0,  # from the north: a headwind for a northbound ride
                        precipitation_probability=70,
                        condition="rain",
                    )
                    for i in range(6)
                ]
                for _ in points
            ]

        async def health(self):  # pragma: no cover
            return {"status": "ok"}

    graph = build_graph(
        geocode_provider=_Geocoder(),
        routing_providers=[_Engine()],
        artifact_store=api_module._artifact_store,
        weather_service=WeatherService([_Weather()]),
    )
    monkeypatch.setattr(api_module, "_graph", graph)

    body = (
        await client.post(
            "/v1/route/plan",
            json={
                "origin": {"lon": 10.0, "lat": 52.0},
                "destination": {"lon": 10.0, "lat": 52.2},
                "departure_time": "2099-01-01T08:00:00Z",
            },
        )
    ).json()
    # a departure in 2099 is beyond the forecast range of the validator
    assert "detail" in body

    from datetime import UTC, datetime, timedelta

    soon = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    body = (
        await client.post(
            "/v1/route/plan",
            json={
                "origin": {"lon": 10.0, "lat": 52.0},
                "destination": {"lon": 10.0, "lat": 52.2},
                "departure_time": soon,
            },
        )
    ).json()

    assert body["status"] == "ready" and body["weather_status"] == "ok"
    weather = body["route"]["weather"]
    assert weather["provider"] == "fake-weather" and weather["attribution"].startswith("Weather by")
    assert weather["summary"]["headwind_mean_kmh"] == pytest.approx(24, abs=0.5)
    assert weather["summary"]["wind_gust_max_kmh"] == 55
    assert any("headwind" in note for note in weather["advisories"])
    assert body["candidates"][0]["weather"] == weather
    assert (
        "Forecast for a" in body["explanation"]
        and "70% chance of precipitation" in body["explanation"]
    )
    assert "safe" not in " ".join(weather["advisories"]).lower()


# ------------------------------------------------- free text (issue #30)


class _NamedGeocoder:
    """Resolves the two places the text tests use; an ambiguous one on demand."""

    name = "fake"

    def __init__(self, ambiguous: str | None = None) -> None:
        self.ambiguous = ambiguous
        self.queries: list[str] = []

    async def geocode(self, query, *, limit=5):
        from bike_routing_agent.models import Coordinate, GeocodeCandidate

        self.queries.append(query)
        coords = {"Braunschweig": (10.5267, 52.2689), "Wolfenbüttel": (10.5361, 52.1688)}
        lon, lat = coords.get(query, (10.0, 52.0))
        first = GeocodeCandidate(
            label=query, coordinate=Coordinate(lon=lon, lat=lat), confidence=0.9, source="fake"
        )
        if query == self.ambiguous:
            second = GeocodeCandidate(
                label=f"{query} (other)",
                coordinate=Coordinate(lon=lon + 1, lat=lat),
                confidence=0.89,
                source="fake",
            )
            return [first, second]
        return [first]


class _LineEngine:
    name = "ors"

    async def route(self, request):
        from bike_routing_agent.models import RouteCandidate, RouteMetrics

        return RouteCandidate(
            provider="ors",
            provider_profile="p",
            geometry_geojson={
                "type": "LineString",
                "coordinates": [[10.5, 52.2], [10.52, 52.17], [10.54, 52.16]],
            },
            metrics=RouteMetrics(distance_m=14_000, duration_s=2400, ascent_m=20),
        )

    async def health(self):  # pragma: no cover
        return {"status": "ok"}


def _install_text_graph(monkeypatch, parser, geocoder=None):
    import bike_routing_agent.api as api_module
    from bike_routing_agent.graph import build_graph

    geocoder = geocoder or _NamedGeocoder()
    graph = build_graph(
        geocode_provider=geocoder,
        routing_providers=[_LineEngine()],
        artifact_store=api_module._artifact_store,
        llm_parser=parser,
    )
    monkeypatch.setattr(api_module, "_graph", graph)
    monkeypatch.setattr(api_module, "_llm_parser", parser)
    return geocoder


def _scripted_parser(**result):
    calls = []

    def parse(text, **kwargs):
        calls.append((text, kwargs))
        return {
            "origin": "Braunschweig",
            "destination": "Wolfenbüttel",
            "via": [],
            "constraints": {"bike_type": "road"},
            "notes": ["scenic is not expressible"],
            "provenance": {"parser": "llm", "model": "claude-test", "prompt_version": "1"},
            **result,
        }

    parse.calls = calls
    return parse


async def test_plan_text_is_503_when_no_parser_is_configured(client, monkeypatch):
    import bike_routing_agent.api as api_module

    monkeypatch.setattr(api_module, "_llm_parser", None)
    response = await client.post(
        "/v1/route/plan-text", json={"text": "Braunschweig to Wolfenbüttel"}
    )
    assert response.status_code == 503
    assert "LLM_PARSER_ENABLED" in response.json()["detail"]


async def test_plan_text_runs_the_whole_pipeline_and_explains_how_it_read_the_text(
    client, monkeypatch
):
    geocoder = _install_text_graph(monkeypatch, _scripted_parser())

    response = await client.post(
        "/v1/route/plan-text",
        json={"text": "a road ride Braunschweig to Wolfenbüttel", "timezone": "Europe/Berlin"},
    )

    body = response.json()
    assert response.status_code == 200 and body["status"] == "ready"
    assert geocoder.queries == ["Braunschweig", "Wolfenbüttel"]  # places are looked up, not guessed
    assert body["route"]["provider_profile"] == "p"
    interpretation = body["interpretation"]
    assert interpretation["request"]["origin"] == "Braunschweig"
    assert interpretation["request"]["constraints"] == {"bike_type": "road"}
    assert interpretation["notes"] == ["scenic is not expressible"]
    assert interpretation["parser"]["model"] == "claude-test"


async def test_the_timezone_and_the_text_reach_the_parser(client, monkeypatch):
    parser = _scripted_parser()
    _install_text_graph(monkeypatch, parser)
    await client.post("/v1/route/plan-text", json={"text": "my words", "timezone": "Europe/Berlin"})
    assert parser.calls == [("my words", {"timezone": "Europe/Berlin"})]


async def test_ambiguous_places_still_ask_for_clarification_instead_of_guessing(
    client, monkeypatch
):
    _install_text_graph(monkeypatch, _scripted_parser(), _NamedGeocoder(ambiguous="Braunschweig"))
    body = (await client.post("/v1/route/plan-text", json={"text": "ride"})).json()
    assert body["status"] == "awaiting_clarification" and body["route"] is None
    assert body["clarification"][0]["field"] == "Braunschweig"
    assert body["interpretation"]["request"]["origin"] == "Braunschweig"  # the UI can show it


async def test_parser_failures_are_structured_invalid_results(client, monkeypatch):
    from bike_routing_agent.llm.parser import LLMParseError

    def declining(text, **kwargs):
        raise LLMParseError("llm_parser_declined", "the language model declined this request")

    _install_text_graph(monkeypatch, declining)
    response = await client.post("/v1/route/plan-text", json={"text": "ride"})
    body = response.json()
    assert response.status_code == 200 and body["status"] == "invalid"
    assert body["errors"][0]["code"] == "llm_parser_declined" and body["interpretation"] is None


async def test_a_loop_without_a_distance_is_rejected_by_the_normal_validation(client, monkeypatch):
    parser = _scripted_parser(
        destination=None,
        constraints={"return_to_origin": True},  # loop, but no target distance
    )
    _install_text_graph(monkeypatch, parser)
    body = (await client.post("/v1/route/plan-text", json={"text": "a loop"})).json()
    assert body["status"] == "invalid"  # the parser's output gets no special treatment


@pytest.mark.parametrize(
    "payload",
    [
        {"text": ""},
        {"text": "   "},
        {"text": "x" * 501},
        {"text": "ok", "timezone": "Mars/Olympus"},
        {"text": "ok", "max_alternatives": 0},
        {"text": "ok", "origin": "sneaky extra field"},
        {},
    ],
)
async def test_plan_text_validates_its_body_before_anything_runs(client, monkeypatch, payload):
    parser = _scripted_parser()
    _install_text_graph(monkeypatch, parser)
    assert (await client.post("/v1/route/plan-text", json=payload)).status_code == 422
    assert parser.calls == []


async def test_max_alternatives_is_honoured_for_text_requests(client, monkeypatch):
    import bike_routing_agent.api as api_module

    graph = _CapturingGraph(_ready_state([("ors", 0.9)]))
    monkeypatch.setattr(api_module, "_graph", graph)
    monkeypatch.setattr(api_module, "_llm_parser", _scripted_parser())
    await client.post("/v1/route/plan-text", json={"text": "ride", "max_alternatives": 3})
    assert graph.initial_states[0]["raw_input"] == {
        "text": "ride",
        "timezone": None,
        "max_alternatives": 3,
    }


async def test_capabilities_report_what_this_instance_can_do(client, monkeypatch):
    import bike_routing_agent.api as api_module

    monkeypatch.setattr(api_module, "_llm_parser", None)
    off = (await client.get("/v1/capabilities")).json()
    assert off["text_planning"] is False and set(off) == {
        "text_planning",
        "weather",
        "history",
        "pois",
    }
    monkeypatch.setattr(api_module, "_llm_parser", _scripted_parser())
    assert (await client.get("/v1/capabilities")).json()["text_planning"] is True
