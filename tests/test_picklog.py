"""The opt-in pick log: which compared route a person takes (optimizer/picklog.py)."""

from __future__ import annotations

import io
import json

import httpx
import pytest
from pydantic import ValidationError

import bike_routing_agent.api as api_module
from bike_routing_agent.cli import main
from bike_routing_agent.optimizer import picklog
from bike_routing_agent.optimizer.picklog import PickLog, PickRequest, build_record


def route(distance_m=10_000, traffic=0.3, paved=0.7, ascent=100, **extra):
    return {
        "metrics": {
            "distance_m": distance_m,
            "duration_s": distance_m / 4,
            "ascent_m": ascent,
            "traffic_index": traffic,
            "engine_surface_shares": {"paved": paved, "unpaved": round(1 - paved, 2)},
            "traffic_shares": {"busy": traffic / 2},
        },
        **extra,
    }


def body(**overrides):
    base = {
        "candidates": [route(), route(distance_m=12_000, traffic=0.1, paved=0.4)],
        "picked": 1,
        "selected": 0,
        "bike_type": "gravel",
        "goals": [{"metric": "traffic", "weight": 1.0}],
    }
    return {**base, **overrides}


# ------------------------------------------------------------------------------ the record


def test_a_pick_keeps_numbers_only_never_geometry_or_places():
    request = PickRequest.model_validate(
        body(
            candidates=[
                route(geometry_geojson={"type": "LineString", "coordinates": [[10.5, 52.2]]}),
                route(distance_m=12_000, provider="brouter", cues=[{"text": "Turn at Goslar"}]),
            ]
        )
    )
    record = build_record(request, now=5.04)
    assert set(record) == {"at", "bike_type", "goals", "picked", "selected", "values"}
    assert record["at"] == 5.0 and record["picked"] == 1 and record["selected"] == 0
    assert record["goals"] == [{"metric": "traffic", "weight": 1.0}]
    first = record["values"][0]
    assert first["distance"] == 10_000 and first["traffic"] == 0.3 and first["paved"] == 0.7
    assert first["pois"] is None  # an engine that gave no data stays "no data", not 0
    text = json.dumps(record)
    assert "Goslar" not in text and "coordinates" not in text and "52.2" not in text


@pytest.mark.parametrize(
    "bad",
    [
        {"picked": 2},
        {"selected": 5},
        {"picked": -1},
        {"candidates": [route()]},
        {"candidates": [route() for _ in range(picklog.MAX_CANDIDATES + 1)]},
        {"goals": [{"metric": "speed_of_light", "weight": 1}]},
        {"goals": [{"metric": "traffic", "weight": 0}]},
        {"bike_type": "x" * 41},
    ],
)
def test_a_report_that_does_not_fit_is_refused(bad):
    with pytest.raises(ValidationError):
        PickRequest.model_validate(body(**bad))


# ------------------------------------------------------------------------------ the file


def test_the_log_appends_rotates_and_clears(tmp_path):
    path = tmp_path / "deep" / "picks.jsonl"
    record = build_record(PickRequest.model_validate(body()), now=1.0)
    size = len(json.dumps(record, ensure_ascii=False)) + 1
    log = PickLog(
        path, max_bytes=3 * size
    )  # three picks fill the file, the fourth starts a new one
    for _ in range(6):
        assert log.record(record)
    assert path.with_name("picks.jsonl.1").exists()
    assert len(picklog.load(path)) == 6  # the rotated copy first, then the live file
    assert log.clear() == 2
    assert picklog.load(path) == []


def test_a_log_that_cannot_be_written_says_so_once_and_does_not_raise(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    log = PickLog(blocker / "picks.jsonl")  # the "folder" is a file
    record = build_record(PickRequest.model_validate(body()))
    assert not log.record(record) and not log.record(record)
    assert sum("cannot be written" in r.message for r in caplog.records) == 1


def test_loading_skips_lines_that_are_not_picks(tmp_path):
    path = tmp_path / "p.jsonl"
    good = json.dumps({"values": [{"traffic": 0.2}, {"traffic": 0.1}], "picked": 1, "selected": 0})
    path.write_text(
        "not json\n[1]\n"
        '{"values": [{"a": 1}], "picked": 0, "selected": 0}\n'  # one route is no comparison
        '{"values": [{"a": 1}, {"a": 2}], "picked": 2, "selected": 0}\n'  # index outside
        '{"values": [{"a": 1}, {"a": 2}], "picked": true, "selected": 0}\n'
        '{"values": [1, 2], "picked": 0, "selected": 1}\n'
        f"{good}\n\n",
        encoding="utf-8",
    )
    assert len(picklog.load(path)) == 1
    assert picklog.load(tmp_path / "missing.jsonl") == []


# ------------------------------------------------------------------------------ the summary


def pick(picked, selected, rows):
    return {"picked": picked, "selected": selected, "values": rows, "bike_type": "gravel"}


def test_the_summary_counts_kept_recommendations_and_reads_the_lean():
    quiet_but_long = [
        {"traffic": 0.3, "distance": 10_000, "paved": 0.7},
        {"traffic": 0.05, "distance": 13_000, "paved": 0.7},
    ]
    kept = [pick(0, 0, quiet_but_long)]
    other = [pick(1, 0, quiet_but_long)] * 3
    summary = picklog.summarise([*kept, *other])
    assert (summary["total"], summary["kept_recommendation"], summary["took_another"]) == (4, 1, 3)
    by_metric = {row["metric"]: row for row in summary["lean"]}
    assert by_metric["traffic"]["mean_gain"] > 0.5  # the taken route is the quieter one
    assert by_metric["distance"]["mean_gain"] < 0  # ... and the longer one
    assert by_metric["paved"]["mean_gain"] == 0
    assert summary["lean"][0]["metric"] == "traffic"  # the strongest lean first
    assert summary["by_bike_type"] == {"gravel": 4}
    assert summary["enough"] is False  # four picks are not a finding


def test_a_metric_one_of_the_two_routes_has_no_data_for_is_left_out():
    rows = [{"traffic": None, "distance": 10_000}, {"traffic": 0.1, "distance": 11_000}]
    summary = picklog.summarise([pick(1, 0, rows)])
    assert "traffic" not in {row["metric"] for row in summary["lean"]}
    assert "distance" in {row["metric"] for row in summary["lean"]}


def test_enough_needs_both_enough_picks_and_enough_of_another_route():
    rows = [{"traffic": 0.3}, {"traffic": 0.1}]
    only_kept = picklog.summarise([pick(0, 0, rows)] * 40)
    assert only_kept["enough"] is False and only_kept["lean"] == []
    mixed = picklog.summarise([pick(0, 0, rows)] * 20 + [pick(1, 0, rows)] * 5)
    assert mixed["enough"] is True


# ------------------------------------------------------------------------------ the endpoint


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=api_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_the_endpoint_is_404_while_the_log_is_off(client, monkeypatch):
    monkeypatch.setattr(api_module, "_pick_log", None)
    assert (await client.post("/v1/route/pick", json=body())).status_code == 404
    caps = (await client.get("/v1/capabilities")).json()
    assert caps["pick_log"] is False


async def test_the_endpoint_keeps_one_line_when_the_log_is_on(client, monkeypatch, tmp_path):
    path = tmp_path / "picks.jsonl"
    monkeypatch.setattr(api_module, "_pick_log", PickLog(path))
    assert (await client.get("/v1/capabilities")).json()["pick_log"] is True
    response = await client.post("/v1/route/pick", json=body())
    assert response.status_code == 204 and response.content == b""
    (line,) = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["picked"] == 1
    assert (await client.post("/v1/route/pick", json=body(picked=9))).status_code == 422
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


# ------------------------------------------------------------------------------ the CLI


def run(*argv: str):
    out, err = io.StringIO(), io.StringIO()
    rc = main(["route", "picks", *argv], stdout=out, stderr=err)
    return rc, out.getvalue(), err.getvalue()


@pytest.fixture(autouse=True)
def _no_pick_log_from_the_environment(monkeypatch):
    from bike_routing_agent.config import settings

    monkeypatch.setattr(settings, "pick_log_path", None)


def test_picks_without_a_log_says_how_to_switch_it_on():
    rc, _, err = run()
    assert rc == 2 and "PICK_LOG_PATH" in err


def test_picks_prints_the_counts_and_the_lean_and_says_when_it_is_too_early(tmp_path):
    path = tmp_path / "p.jsonl"
    log = PickLog(path)
    log.record(build_record(PickRequest.model_validate(body())))
    log.record(build_record(PickRequest.model_validate(body(picked=0))))
    rc, out, _ = run("--path", str(path))
    assert rc == 0
    assert "2 route(s) taken" in out and "the recommended one 1x, another 1x" in out
    assert "Too few to read a lean" in out
    assert "less traffic" in out


def test_picks_json_empty_log_and_the_configured_path(tmp_path, monkeypatch):
    path = tmp_path / "p.jsonl"
    rc, out, _ = run("--path", str(path))
    assert rc == 0 and "No picks recorded" in out
    PickLog(path).record(build_record(PickRequest.model_validate(body())))
    from bike_routing_agent.config import settings

    monkeypatch.setattr(settings, "pick_log_path", path)
    rc, out, _ = run("--format", "json")
    assert rc == 0 and json.loads(out)["total"] == 1


def test_picks_clear_deletes_the_log(tmp_path):
    path = tmp_path / "p.jsonl"
    PickLog(path).record(build_record(PickRequest.model_validate(body())))
    rc, out, _ = run("--path", str(path), "--clear")
    assert rc == 0 and "removed 1 file" in out and not path.exists()
