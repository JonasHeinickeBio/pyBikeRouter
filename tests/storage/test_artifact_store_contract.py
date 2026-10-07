"""list/delete contract shared by every artifact store (issue #27)."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from bike_routing_agent.storage.artifacts import ArtifactInfo, LocalArtifactStore

pytest.importorskip("botocore")

from bike_routing_agent.storage.s3 import S3ArtifactStore  # noqa: E402

from .fake_s3 import FakeS3Client  # noqa: E402

A = "a" * 32 + ".geojson"
B = "b" * 32 + ".gpx"
C = "c" * 32 + ".geojson"
D = "d" * 32 + ".geojson"


@pytest.fixture(
    params=[
        "local",
        "s3",
        "s3-prefixed",
        pytest.param("postgres", marks=pytest.mark.live),
        pytest.param("s3-live", marks=pytest.mark.live),
    ],
)
def store(request, tmp_path):
    if request.param == "local":
        return LocalArtifactStore(tmp_path)
    if request.param == "s3":
        return S3ArtifactStore(bucket="bucket", client=FakeS3Client())
    if request.param == "s3-prefixed":
        return S3ArtifactStore(bucket="bucket", prefix="/exports/", client=FakeS3Client())
    if request.param == "s3-live":
        # A real S3-compatible server (SeaweedFS, Garage, AWS, ...):
        #   TEST_S3_ENDPOINT_URL=http://127.0.0.1:8333 TEST_S3_BUCKET=exports \
        #   AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... pytest -m live tests/storage
        endpoint = os.environ.get("TEST_S3_ENDPOINT_URL")
        if not endpoint:
            pytest.skip("TEST_S3_ENDPOINT_URL not set")
        # unique prefix per test: runs never see each other's objects
        return S3ArtifactStore(
            bucket=os.environ.get("TEST_S3_BUCKET", "exports"),
            prefix=f"contract-{uuid.uuid4().hex}",
            endpoint_url=endpoint,
            region=os.environ.get("TEST_S3_REGION", "us-east-1"),
            path_style=True,
        )
    from bike_routing_agent.storage.postgres import PostgresArtifactStore

    return PostgresArtifactStore(request.getfixturevalue("postgres_database"))


def test_listing_reports_names_sizes_and_ages(store):
    before = datetime.now(UTC) - timedelta(seconds=5)
    store.put(A, "12345")
    store.put(B, "123")

    infos = {i.name: i for i in store.list_artifacts()}

    assert set(infos) == {A, B}
    assert (infos[A].size, infos[B].size) == (5, 3)
    assert all(isinstance(i, ArtifactInfo) for i in infos.values())
    after = datetime.now(UTC) + timedelta(seconds=5)
    assert all(before <= i.created_at <= after for i in infos.values())


def test_listing_an_empty_store_is_empty(store):
    assert list(store.list_artifacts()) == []


def test_listing_spans_pages(store):
    names = {f"{i:032x}.geojson" for i in range(5)}
    for name in names:
        store.put(name, "x")
    assert {i.name for i in store.list_artifacts()} == names


def test_delete_removes_and_reports(store):
    store.put(A, "x")
    assert store.delete(A) is True
    assert store.get(A) is None
    assert store.delete(A) is False  # already gone: not an error


@pytest.mark.parametrize("name", ["../x.geojson", "sub/" + A, "not-ours.txt"])
def test_delete_ignores_names_it_did_not_generate(store, name):
    assert store.delete(name) is False


def test_other_objects_are_not_ours_to_list(store):
    store.put(A, "x")
    store.put("README.txt", "hello")  # same bucket/directory, not an export
    assert [i.name for i in store.list_artifacts()] == [A]
    assert store.get("README.txt") == b"hello"


def test_local_listing_uses_mtime_as_the_age(tmp_path):
    store = LocalArtifactStore(tmp_path)
    store.put(C, "x")
    old = (datetime.now(UTC) - timedelta(days=40)).timestamp()
    os.utime(tmp_path / C, (old, old))
    (info,) = list(store.list_artifacts())
    assert datetime.now(UTC) - info.created_at > timedelta(days=39)


def test_local_listing_of_a_missing_directory_is_empty(tmp_path):
    assert list(LocalArtifactStore(tmp_path / "never-created").list_artifacts()) == []
