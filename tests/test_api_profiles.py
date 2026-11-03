"""GET /v1/profiles/settings: the parameter space of the routing profiles, for clients to read."""

import httpx
import pytest
from httpx import ASGITransport

import bike_routing_agent.api as api_module


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=ASGITransport(app=api_module.app), base_url="http://test"
    ) as c:
        yield c


async def test_the_settings_of_a_bike_types_profile(client):
    response = await client.get("/v1/profiles/settings", params={"bike_type": "touring"})
    assert response.status_code == 200
    (entry,) = response.json()["profiles"]
    assert entry["profile"] == "custom_touring-v1" and entry["bike_types"] == ["touring"]
    by_name = {s["name"]: s for s in entry["settings"]}
    assert by_name["uphillcost"]["maximum"] == 200 and by_name["uphillcost"]["affects"] == "route"
    assert by_name["bikerPower"]["affects"] == "time"
    assert "json_schema" not in entry


async def test_the_json_schema_is_there_on_request(client):
    response = await client.get(
        "/v1/profiles/settings", params={"profile": "custom_gravel-v2", "schema": "true"}
    )
    (entry,) = response.json()["profiles"]
    assert entry["json_schema"]["additionalProperties"] is False
    assert "prefer_unpaved_paths" in entry["json_schema"]["properties"]


async def test_without_a_target_every_profile_with_settings_comes_back(client):
    profiles = (await client.get("/v1/profiles/settings")).json()["profiles"]
    names = {p["profile"] for p in profiles}
    assert {"custom_touring-v1", "custom_commuter-v1", "custom_gravel-v2", "trekking"} <= names
    gravel = next(p for p in profiles if p["profile"] == "custom_gravel-v2")
    assert gravel["bike_types"] == ["gravel"]


async def test_a_profile_without_settings_is_empty_not_an_error(client):
    response = await client.get("/v1/profiles/settings", params={"bike_type": "mountain"})
    (entry,) = response.json()["profiles"]
    assert entry["profile"] == "mtb" and entry["settings"] == []


async def test_bad_questions_are_refused(client):
    both = await client.get(
        "/v1/profiles/settings", params={"bike_type": "touring", "profile": "trekking"}
    )
    assert both.status_code == 422
    assert (
        await client.get("/v1/profiles/settings", params={"bike_type": "tank"})
    ).status_code == 422
    assert (
        await client.get("/v1/profiles/settings", params={"profile": "nope"})
    ).status_code == 404


async def test_the_plan_endpoint_accepts_the_settings_it_lists(client):
    from bike_routing_agent.models import RoutePlanAPIRequest

    request = RoutePlanAPIRequest.model_validate(
        {
            "origin": {"lon": 10.5, "lat": 52.2},
            "destination": {"lon": 10.6, "lat": 52.3},
            "constraints": {"bike_type": "touring", "profile_params": {"uphillcost": 90}},
        }
    )
    assert request.constraints.profile_params == {"uphillcost": 90}
