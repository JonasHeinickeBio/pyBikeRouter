"""Unit tests for the validate_request node."""

from bike_routing_agent.nodes.validate import validate_request


def test_already_invalid_state_passes_through_unchanged():
    update = validate_request({"status": "invalid", "constraints": {"bike_type": "gravel"}})

    assert update == {}


def test_malformed_constraints_are_invalid_with_loc_info():
    update = validate_request({"constraints": {"target_distance_km": -5}})

    assert update["status"] == "invalid"
    error = update["errors"][0]
    assert error["code"] == "invalid_constraints"
    assert error["loc"] == ["target_distance_km"]


def test_conflicting_surface_preferences_are_invalid():
    update = validate_request(
        {
            "constraints": {
                "prefer_surfaces": ["paved"],
                "avoid_surfaces": ["paved"],
            }
        }
    )

    assert update["status"] == "invalid"
    assert "both prefer and avoid" in update["errors"][0]["message"]


def test_missing_origin_and_destination_yield_clarification_for_both():
    update = validate_request({"constraints": {}, "origin_input": None, "destination_input": None})

    assert update["status"] == "awaiting_clarification"
    assert [c["field"] for c in update["clarification"]] == ["origin", "destination"]
    assert [e["code"] for e in update["errors"]] == ["missing_place", "missing_place"]


def test_valid_state_proceeds_in_progress():
    update = validate_request(
        {
            "constraints": {"bike_type": "road"},
            "origin_input": {"query": "A"},
            "destination_input": {"query": "B"},
        }
    )

    assert update == {"status": "in_progress"}
