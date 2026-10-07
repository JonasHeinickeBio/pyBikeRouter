"""S3ArtifactStore specifics (issue #27): the contract itself is in test_artifact_store_contract."""

import pytest

pytest.importorskip("botocore")

from bike_routing_agent.storage.s3 import S3ArtifactStore  # noqa: E402

from .fake_s3 import FakeS3Client  # noqa: E402

NAME = "a" * 32 + ".geojson"


def test_objects_are_written_under_the_prefix_with_a_media_type():
    client = FakeS3Client()
    store = S3ArtifactStore(bucket="bucket", prefix="routes/v1", client=client)
    store.put(NAME, "{}")
    assert list(client.objects) == [f"routes/v1/{NAME}"]
    assert client.objects[f"routes/v1/{NAME}"][2] == "application/geo+json"
    assert store.get(NAME) == b"{}"


def test_missing_object_is_none_but_other_errors_surface():
    client = FakeS3Client()
    store = S3ArtifactStore(bucket="bucket", client=client)
    assert store.get(NAME) is None
    client.bucket_exists = False  # NoSuchBucket is a misconfiguration, not "not found"
    with pytest.raises(Exception, match="NoSuchBucket"):
        store.get(NAME)


def test_delete_looks_before_deleting_and_surfaces_real_errors():
    client = FakeS3Client()
    store = S3ArtifactStore(bucket="bucket", client=client)
    assert store.delete(NAME) is False
    assert "delete_object" not in client.calls
    store.put(NAME, "x")
    client.fail_delete.add(NAME)
    with pytest.raises(Exception, match="InternalError"):
        store.delete(NAME)


def test_ping_reflects_bucket_reachability():
    client = FakeS3Client()
    store = S3ArtifactStore(bucket="bucket", client=client)
    assert store.ping() == {"status": "ok"}
    client.bucket_exists = False
    assert store.ping() == {"status": "unavailable"}


def test_presigned_url_names_the_object_and_ttl():
    store = S3ArtifactStore(bucket="bucket", prefix="p", client=FakeS3Client())
    assert store.presigned_url(NAME, 120) == f"https://s3.example/bucket/p/{NAME}?expires=120"


def test_real_client_is_built_with_endpoint_region_and_path_style():
    store = S3ArtifactStore(
        bucket="bucket",
        endpoint_url="http://127.0.0.1:9",
        region="eu-central-1",
        path_style=True,
    )
    client = store._client
    assert client.meta.endpoint_url == "http://127.0.0.1:9"
    assert client.meta.region_name == "eu-central-1"
    assert client.meta.config.s3["addressing_style"] == "path"
