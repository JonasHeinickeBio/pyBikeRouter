import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.fixture
def ors_directions_response() -> dict:
    return load_fixture("ors_directions_response.json")


@pytest.fixture
def brouter_route_response() -> dict:
    return load_fixture("brouter_route_response.json")


@pytest.fixture
def valhalla_route_response() -> dict:
    return load_fixture("valhalla_route_response.json")


@pytest.fixture
def valhalla_route_response_multileg() -> dict:
    return load_fixture("valhalla_route_response_multileg.json")


@pytest.fixture
def valhalla_route_response_inline_shape() -> dict:
    return load_fixture("valhalla_route_response_inline_shape.json")


@pytest.fixture
def valhalla_no_route_response() -> dict:
    return load_fixture("valhalla_no_route_response.json")


@pytest.fixture
def ors_no_route_response() -> dict:
    return load_fixture("ors_no_route_response.json")


@pytest.fixture
def overpass_surface_response() -> dict:
    return load_fixture("overpass_surface_response.json")


@pytest.fixture
def overpass_empty_response() -> dict:
    return load_fixture("overpass_empty_response.json")


@pytest.fixture
def nominatim_single_response() -> list:
    return load_fixture("nominatim_single_response.json")


@pytest.fixture
def nominatim_ambiguous_response() -> list:
    return load_fixture("nominatim_ambiguous_response.json")
