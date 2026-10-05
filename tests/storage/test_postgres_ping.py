"""Readiness ping against a real PostGIS (live tier, needs TEST_DATABASE_URL)."""

import pytest

pytestmark = pytest.mark.live


def test_ping_ok_against_a_real_database(postgres_database):
    assert postgres_database.ping() == {"status": "ok"}


def test_ping_reports_unavailable_for_a_dead_server():
    pytest.importorskip("psycopg")
    from bike_routing_agent.storage.postgres import PostgresDatabase

    # closed port on localhost: refuses immediately instead of timing out
    dead = PostgresDatabase("postgresql://u:p@127.0.0.1:1/none?connect_timeout=1")
    assert dead.ping(timeout_s=2.0) == {"status": "unavailable"}
