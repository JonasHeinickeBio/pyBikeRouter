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
def ors_no_route_response() -> dict:
    return load_fixture("ors_no_route_response.json")


@pytest.fixture
def nominatim_single_response() -> list:
    return load_fixture("nominatim_single_response.json")


@pytest.fixture
def nominatim_ambiguous_response() -> list:
    return load_fixture("nominatim_ambiguous_response.json")
