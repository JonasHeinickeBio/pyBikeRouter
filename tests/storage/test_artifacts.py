"""Artifact storage abstraction (issue #7): local backend and media types."""

import pytest

from bike_routing_agent.storage.artifacts import LocalArtifactStore, media_type_for


def test_local_store_round_trip_creates_directory_on_demand(tmp_path):
    store = LocalArtifactStore(tmp_path / "nested" / "exports")

    store.put("a" * 32 + ".geojson", '{"type": "Feature"}')

    assert store.get("a" * 32 + ".geojson") == b'{"type": "Feature"}'
    assert (tmp_path / "nested" / "exports" / ("a" * 32 + ".geojson")).is_file()


def test_local_store_put_replaces_existing_content(tmp_path):
    store = LocalArtifactStore(tmp_path)
    store.put("x.gpx", "one")
    store.put("x.gpx", "two")

    assert store.get("x.gpx") == b"two"


def test_local_store_missing_artifact_is_none(tmp_path):
    assert LocalArtifactStore(tmp_path).get("nope.geojson") is None


@pytest.mark.parametrize("name", ["../secret.geojson", "sub/x.geojson", "/etc/passwd"])
def test_local_store_never_reads_outside_its_directory(tmp_path, name):
    (tmp_path / "exports").mkdir()
    (tmp_path / "secret.geojson").write_text("secret")

    assert LocalArtifactStore(tmp_path / "exports").get(name) is None


@pytest.mark.parametrize(
    ("name", "media_type"),
    [
        ("r.geojson", "application/geo+json"),
        ("r.GPX", "application/gpx+xml"),
        ("r.bin", "application/octet-stream"),
    ],
)
def test_media_type_by_extension(name, media_type):
    assert media_type_for(name) == media_type
