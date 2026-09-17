import pytest
from pydantic import ValidationError

from bike_routing_agent.models import (
    Coordinate,
    RouteConstraints,
    RoutePlanAPIRequest,
)


def test_coordinate_rejects_out_of_range_values():
    with pytest.raises(ValidationError):
        Coordinate(lon=200.0, lat=52.0)
    with pytest.raises(ValidationError):
        Coordinate(lon=10.0, lat=-100.0)


def test_route_constraints_rejects_negative_distance():
    with pytest.raises(ValidationError):
        RouteConstraints(target_distance_km=-5)


def test_route_constraints_rejects_unsupported_bike_type():
    with pytest.raises(ValidationError):
        RouteConstraints(bike_type="unicycle")


def test_route_constraints_rejects_oversized_ascent():
    with pytest.raises(ValidationError):
        RouteConstraints(max_ascent_m=999_999)


def test_route_constraints_rejects_target_exceeding_max():
    with pytest.raises(ValidationError):
        RouteConstraints(target_distance_km=100, max_distance_km=50)


def test_route_constraints_rejects_conflicting_surface_prefs():
    with pytest.raises(ValidationError):
        RouteConstraints(prefer_surfaces=["gravel"], avoid_surfaces=["gravel"])


def test_route_plan_api_request_accepts_place_strings():
    request = RoutePlanAPIRequest(origin="Braunschweig", destination="Wolfenbüttel")
    assert request.origin == "Braunschweig"
    assert request.constraints.bike_type.value == "gravel"


def test_route_plan_api_request_accepts_coordinates():
    request = RoutePlanAPIRequest(
        origin={"lon": 10.5, "lat": 52.3},
        destination={"lon": 10.6, "lat": 52.4},
    )
    assert isinstance(request.origin, Coordinate)


def test_route_plan_api_request_rejects_oversized_via_list():
    with pytest.raises(ValidationError):
        RoutePlanAPIRequest(origin="A", destination="B", via=[f"stop{i}" for i in range(11)])


def test_route_plan_api_request_rejects_blank_place_string():
    with pytest.raises(ValidationError):
        RoutePlanAPIRequest(origin="Braunschweig", destination="")
    with pytest.raises(ValidationError):
        RoutePlanAPIRequest(origin="   ", destination="Wolfenbüttel")
