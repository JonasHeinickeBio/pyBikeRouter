"""The self-hosted Overpass service in docker/compose.yaml: what a reader can rely on."""

import os
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")  # a dependency of langchain-core, not declared by this project

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "docker" / "compose.yaml").read_text())
SERVICE = COMPOSE["services"]["overpass-self-hosted"]
ENV = SERVICE["environment"]


def test_it_is_opt_in_not_part_of_the_shared_self_hosted_profile():
    assert SERVICE["profiles"] == ["self-hosted-pois"]
    for name in ("ors-self-hosted", "nominatim-self-hosted"):
        assert "self-hosted-pois" not in COMPOSE["services"][name]["profiles"]


def test_the_image_is_pinned_to_a_release_not_latest():
    image = SERVICE["image"]
    assert image.startswith("wiktorn/overpass-api:v") and not image.endswith(":latest")


def test_it_binds_loopback_only():
    (port,) = SERVICE["ports"]
    assert port.startswith("127.0.0.1:") and port.endswith(":80")
    assert "${OVERPASS_LOCAL_PORT:-8083}" in port  # the port the bootstrap script and docs name


def test_it_imports_the_extract_the_other_services_share_read_only():
    assert ENV["OVERPASS_MODE"] == "init"
    assert ENV["OVERPASS_PLANET_URL"] == "file:///pbf/region.osm.pbf"
    assert "./self-hosted/data:/pbf:ro" in SERVICE["volumes"]
    # PBF -> the bz2 XML the image reads; the metadata the database does not keep is dropped
    preprocess = " ".join(str(ENV["OVERPASS_PLANET_PREPROCESS"]).split())
    assert "osmium cat -f osm.bz2,add_metadata=false -o /db/planet.osm.bz2" in preprocess
    assert ENV["OVERPASS_META"] == "no"


def test_it_keeps_serving_after_the_import_and_does_not_refuse_repeated_searches():
    assert ENV["OVERPASS_STOP_AFTER_INIT"] == "false"
    assert ENV["OVERPASS_ALLOW_DUPLICATE_QUERIES"] == "yes"
    assert "false" in ENV["OVERPASS_USE_AREAS"]  # the searches use boxes and distances, not areas


def test_the_data_directory_hook_that_makes_queries_work_is_mounted_and_runnable():
    """Without it every query fails with 'open64: 13 Permission denied' (see the script)."""
    hook = [
        v for v in SERVICE["volumes"] if v.endswith(":/docker-entrypoint-initdb.d/00-open-db.sh:ro")
    ]
    assert hook, "the hook is not mounted"
    script = ROOT / "docker" / hook[0].split(":")[0].removeprefix("./")
    assert script.exists() and os.access(script, os.X_OK)
    body = script.read_text()
    assert "chmod 755 /db" in body and "Permission denied" in body  # and it says why


def test_it_has_its_own_named_volume_and_a_memory_limit():
    assert "overpass-data:/db" in SERVICE["volumes"]
    assert COMPOSE["volumes"]["overpass-data"]["name"] == "pybikerouter-overpass-data"
    assert "OVERPASS_MEM_LIMIT" in SERVICE["deploy"]["resources"]["limits"]["memory"]


def test_the_healthcheck_asks_the_status_page_and_waits_out_the_import():
    check = SERVICE["healthcheck"]
    assert "http://localhost/api/status" in " ".join(check["test"]) and "Connected as" in " ".join(
        check["test"]
    )
    assert int(str(check["start_period"]).rstrip("s")) >= 300  # the first start imports


@pytest.mark.parametrize("name", ["ors-self-hosted", "nominatim-self-hosted"])
def test_the_older_services_are_untouched_by_it(name):
    assert COMPOSE["services"][name]["profiles"][0] == "self-hosted"
