"""PostGIS-specific behavior (issue #7): schema, artifacts, spatial columns.

Shared history semantics live in test_history.py (parametrized over both
backends). Everything here needs a real server -- see conftest.py.
"""

import httpx
import pytest
import respx

from bike_routing_agent.storage.history import record_from_state
from bike_routing_agent.storage.postgres import PostgresArtifactStore, PostgresRouteHistory

from .test_history import candidate, pid, ready_state

pytestmark = pytest.mark.live


def test_artifact_store_round_trip_and_overwrite(postgres_database):
    store = PostgresArtifactStore(postgres_database)

    assert store.get("a.geojson") is None
    store.put("a.geojson", '{"type": "Feature"}')
    store.put("a.geojson", '{"type": "Feature", "v": 2}')

    assert store.get("a.geojson") == b'{"type": "Feature", "v": 2}'


def test_schema_creation_is_idempotent(postgres_database):
    with postgres_database.connection() as conn:
        conn.execute("SELECT 1")
    # A second database object against the same server re-runs the DDL.
    from bike_routing_agent.storage.postgres import PostgresDatabase

    again = PostgresDatabase(postgres_database._url)
    with again.connection() as conn:
        assert conn.execute("SELECT PostGIS_Version()").fetchone() is not None
    again.close()


def test_candidate_geometry_is_stored_as_indexed_2d_linestring(postgres_database):
    history = PostgresRouteHistory(postgres_database)
    history.save(record_from_state(pid(1), ready_state([candidate()])))

    with postgres_database.connection() as conn:
        row = conn.execute(
            "SELECT GeometryType(geom), ST_NDims(geom), ST_SRID(geom), ST_NPoints(geom) "
            "FROM candidates"
        ).fetchone()

    assert row == ("LINESTRING", 2, 4326, 2)


def test_multilinestring_geometry_is_merged_when_connected(postgres_database):
    history = PostgresRouteHistory(postgres_database)
    multi = candidate()
    multi["geometry_geojson"] = {
        "type": "MultiLineString",
        "coordinates": [[[10.0, 52.0], [10.1, 52.1]], [[10.1, 52.1], [10.2, 52.2]]],
    }
    history.save(record_from_state(pid(1), ready_state([multi])))

    with postgres_database.connection() as conn:
        row = conn.execute("SELECT GeometryType(geom), ST_NPoints(geom) FROM candidates").fetchone()

    assert row == ("LINESTRING", 3)


def test_unmergeable_geometry_is_kept_in_json_with_null_spatial_column(postgres_database):
    history = PostgresRouteHistory(postgres_database)
    broken = candidate()
    broken["geometry_geojson"] = {
        "type": "MultiLineString",
        "coordinates": [[[10.0, 52.0], [10.1, 52.1]], [[11.0, 53.0], [11.1, 53.1]]],
    }
    history.save(record_from_state(pid(1), ready_state([broken])))

    with postgres_database.connection() as conn:
        geom = conn.execute("SELECT geom FROM candidates").fetchone()[0]
    record = history.get(pid(1))

    assert geom is None
    assert record is not None
    assert record.candidates[0].candidate.geometry_geojson["type"] == "MultiLineString"


def test_replacing_a_plan_cascades_its_candidates(postgres_database):
    history = PostgresRouteHistory(postgres_database)
    history.save(record_from_state(pid(1), ready_state([candidate(), candidate("brouter")])))
    history.save(record_from_state(pid(1), ready_state([candidate()])))

    with postgres_database.connection() as conn:
        count = conn.execute("SELECT count(*) FROM candidates").fetchone()[0]

    assert count == 1


@respx.mock
async def test_api_end_to_end_with_database_history_and_artifacts(
    postgres_database, monkeypatch, ors_directions_response, nominatim_single_response
):
    """The real app, graph and PostGIS: plan -> artifact download -> provenance query."""
    from bike_routing_agent import api as api_module

    monkeypatch.setattr(api_module, "_history", PostgresRouteHistory(postgres_database))
    store = PostgresArtifactStore(postgres_database)
    monkeypatch.setattr(api_module, "_artifact_store", store)
    # The graph captured the original store at import time; rebuild it on ours.
    monkeypatch.setattr(
        api_module,
        "_graph",
        api_module.build_graph(
            geocode_provider=api_module._geocode_provider,
            routing_providers=api_module._routing_providers,
            artifact_store=store,
        ),
    )
    respx.get("https://nominatim.openstreetmap.org/search").mock(
        return_value=httpx.Response(200, json=nominatim_single_response[:1])
    )
    respx.post("https://api.openrouteservice.org/v2/directions/cycling-regular/geojson").mock(
        return_value=httpx.Response(200, json=ors_directions_response)
    )

    transport = httpx.ASGITransport(app=api_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        plan = (
            await client.post(
                "/v1/route/plan", json={"origin": "Braunschweig", "destination": "Wolfenbuettel"}
            )
        ).json()
        download = await client.get(plan["artifacts"]["gpx_url"])
        history = (
            await client.get(
                "/v1/history/plans", params={"provider": "ors", "bbox": "-180,-90,180,90"}
            )
        ).json()
        detail = (await client.get(f"/v1/history/plans/{plan['plan_id']}")).json()

    assert plan["status"] == "ready"
    assert b"<gpx" in download.content
    assert [p["plan_id"] for p in history] == [plan["plan_id"]]
    assert detail["candidates"][0]["candidate"]["provider"] == "ors"
