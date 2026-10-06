"""Fixtures for the PostGIS-backed storage tests.

These run only with ``pytest -m live`` and a PostGIS server reachable at
``TEST_DATABASE_URL`` (the tests drop and recreate their tables), e.g.::

    docker run --rm -d -p 127.0.0.1:55432:5432 -e POSTGRES_USER=test \\
        -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test postgis/postgis:16-3.4
    TEST_DATABASE_URL=postgresql://test:test@127.0.0.1:55432/test pytest -m live tests/storage
"""

import os

import pytest


@pytest.fixture
def postgres_database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    psycopg = pytest.importorskip("psycopg")
    from bike_routing_agent.storage.postgres import PostgresDatabase

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "DROP TABLE IF EXISTS candidates, plans, artifacts, schema_migrations CASCADE"
        )
    database = PostgresDatabase(url, max_size=2)  # migrates (recreates the schema) on first use
    yield database
    database.close()


@pytest.fixture
def empty_database_url():
    """A database URL with every table of ours dropped (migration tests)."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "DROP TABLE IF EXISTS candidates, plans, artifacts, schema_migrations CASCADE"
        )
    return url
