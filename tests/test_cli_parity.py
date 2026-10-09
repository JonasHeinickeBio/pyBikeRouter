"""The CLI covers what the API and the web form do: alternatives, engines, sight stops,
text/exports of any alternative, saved plans, points of interest, BRouter tiles, history and
status."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

import bike_routing_agent.api as api_module
from bike_routing_agent.cli import main
from bike_routing_agent.cli.commands import brouter as brouter_cmd
from bike_routing_agent.cli.commands import route as route_cmd
from bike_routing_agent.cli.main import build_parser
from bike_routing_agent.cli.render import megabytes, minutes, plan_text, poi_text, status_text
from bike_routing_agent.health import ComponentReport, HealthReport
from bike_routing_agent.poi.models import Poi, PoiInfo, PoiLink
from bike_routing_agent.poi.service import PoiSearchResult
from bike_routing_agent.storage.history import InMemoryRouteHistory, record_from_state


def cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def parse(*argv: str) -> Any:
    return build_parser().parse_args(list(argv))


@pytest.fixture(autouse=True)
def _no_history_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(route_cmd, "_default_history_factory", lambda: None)


# ------------------------------------------------------------------------ route plan


def cand(rank: int, provider="brouter", profile="custom_gravel-v2", **kw: Any) -> dict[str, Any]:
    return {
        "provider": provider,
        "provider_profile": profile,
        "rank": rank,
        "score": 0.95 - rank / 100,
        "geometry_geojson": {
            "type": "LineString",
            "coordinates": [[10.5 + rank / 100, 52.2], [10.6, 52.3]],
        },
        "metrics": {"distance_m": 30_000.0 - rank * 500, "duration_s": 4200.0, "ascent_m": 16.0},
        "pros": [f"pro of {rank}"],
        "cons": [f"con of {rank}"],
        "warnings": [],
        "raw_provider_response": {"secret": 1},
        **kw,
    }


def ready_state(**extra: Any) -> dict[str, Any]:
    candidates = [cand(1), cand(2, profile="mtb")]
    return {
        "status": "ready",
        "explanation": "direct route",
        "errors": [],
        "candidates": candidates,
        "selected_candidate": candidates[0],
        "artifacts": {"gpx_file": "a.gpx"},
        "weather_status": "ok",
        **extra,
    }


class FakeGraph:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.invoked_with: dict[str, Any] | None = None

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        self.invoked_with = state
        return self.state


def plan(*argv: str, state: dict[str, Any] | None = None) -> tuple[int, str, str, FakeGraph]:
    graph = FakeGraph(state or ready_state())
    out, err = io.StringIO(), io.StringIO()
    code = route_cmd.run(
        parse("route", "plan", "--origin", "52.2,10.5", "--destination", "52.3,10.6", *argv),
        out,
        err,
        graph_factory=lambda: graph,
    )
    return code, out.getvalue(), err.getvalue(), graph


def test_the_plan_request_carries_alternatives_engines_and_sight_stops():
    code, _, _, graph = plan(
        "--max-alternatives", "3", "--engine", "ors", "--engine", "brouter",
        "--poi-stops", "2", "--poi-categories", "castle_not,historic", "--poi-corridor-km", "4",
    )  # fmt: skip
    # "castle_not" is not a kind: the API's own request rules reject it before any routing.
    assert code == 2 and graph.invoked_with is None
    code, _, _, graph = plan(
        "--max-alternatives", "3", "--engine", "ors", "--engine", "brouter",
        "--poi-stops", "2", "--poi-categories", "historic,museum", "--poi-corridor-km", "4",
        "--poi-min-fame", "10",
    )  # fmt: skip
    request = (graph.invoked_with or {})["raw_input"]
    assert request["max_alternatives"] == 3
    assert request["routing_engines"] == ["ors", "brouter"]
    assert request["poi_stops"] == {
        "count": 2,
        "categories": ["historic", "museum"],
        "corridor_km": 4.0,
        "min_fame": 10,
    }
    assert code == 0


def test_without_the_new_options_the_request_is_unchanged():
    _, _, _, graph = plan()
    request = (graph.invoked_with or {})["raw_input"]
    assert not {"max_alternatives", "routing_engines", "poi_stops"} & set(request)


@pytest.mark.parametrize(
    ("extra", "fragment"),
    [
        (["--poi-stops", "9"], "poi_stops.count"),
        (["--max-alternatives", "9"], "max_alternatives"),
        (["--poi-stops", "1", "--poi-categories", "water"], "services"),
        (["--candidate", "2"], "--candidate only selects"),
    ],
)
def test_option_mistakes_are_usage_errors_before_any_routing(extra, fragment):
    code, _, err, graph = plan(*extra)
    assert code == 2 and fragment in err and graph.invoked_with is None


def test_stops_cannot_be_combined_with_a_loop_or_free_text():
    graph = FakeGraph(ready_state())
    out, err = io.StringIO(), io.StringIO()
    code = route_cmd.run(
        parse("route", "plan", "--origin", "52.2,10.5", "--loop", "--target-distance-km", "20",
              "--poi-stops", "2"),
        out, err, graph_factory=lambda: graph,
    )  # fmt: skip
    assert code == 2 and "return_to_origin" in err.getvalue() and graph.invoked_with is None
    code = route_cmd.run(
        parse("route", "plan", "--text", "a ride", "--poi-stops", "2"),
        out, err, graph_factory=lambda: graph,
    )  # fmt: skip
    assert code == 2 and "--text" in err.getvalue()


def test_the_json_output_has_every_ranked_alternative_without_raw_responses():
    code, out, _, _ = plan(state=ready_state(poi_stops=[{"name": "Burg"}], poi_stops_status="ok"))
    payload = json.loads(out)
    assert code == 0
    assert [c["rank"] for c in payload["candidates"]] == [1, 2]
    assert all("raw_provider_response" not in c for c in payload["candidates"])
    assert payload["poi_stops"] == [{"name": "Burg"}] and payload["poi_stops_status"] == "ok"
    assert payload["weather_status"] == "ok"


def test_text_format_is_a_readable_summary_with_alternatives_and_their_pros_and_cons():
    code, out, _, _ = plan("--format", "text")
    assert code == 0 and not out.lstrip().startswith("{")
    assert "Route ready: brouter/custom_gravel-v2  29.5 km  1 h 10 min" in out
    assert "Alternatives:" in out and "<- selected" in out
    assert "+ pro of 1" in out and "- con of 2" in out and "gpx_file: a.gpx" in out


def test_any_alternative_can_be_written_as_gpx_and_geojson(tmp_path: Path):
    gpx, geo = tmp_path / "second.gpx", tmp_path / "second.geojson"
    code, _, err, _ = plan("--gpx", str(gpx), "--geojson", str(geo), "--candidate", "2")
    assert code == 0 and "wrote" in err
    assert gpx.read_text().startswith("<?xml") and "<trkpt" in gpx.read_text()
    feature = json.loads(geo.read_text())
    assert feature["properties"]["provider_profile"] == "mtb"  # rank 2, not the selected rank 1
    import xml.etree.ElementTree as ET

    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    first = ET.fromstring(gpx.read_text()).find(".//g:trkpt", ns)
    assert first is not None and float(first.attrib["lon"]) == pytest.approx(10.52)  # rank 1: 10.51
    assert ET.fromstring(gpx.read_text()).findtext(".//g:provider_profile", namespaces=ns) == "mtb"


def test_asking_for_a_rank_that_does_not_exist_names_the_ranks(tmp_path: Path):
    code, _, err, _ = plan("--gpx", str(tmp_path / "x.gpx"), "--candidate", "7")
    assert code == 2 and "ranks in this plan: [1, 2]" in err and not (tmp_path / "x.gpx").exists()


def test_a_failed_plan_in_text_names_the_problem_and_the_ambiguous_places():
    state = {
        "status": "awaiting_clarification",
        "errors": [],
        "clarification": [
            {
                "field": "Springfield",
                "candidates": [
                    {"label": "Springfield, IL", "coordinate": {"lat": 39.8, "lon": -89.6}},
                    {"label": "Springfield, MA", "coordinate": {"lat": 42.1, "lon": -72.6}},
                ],
            }
        ],
    }
    code, out, _, _ = plan("--format", "text", state=state)
    assert code == 1
    assert "awaiting_clarification" in out and "39.80000,-89.60000  Springfield, IL" in out


def test_a_saved_plan_can_be_shown_and_exported_later(tmp_path: Path):
    saved = tmp_path / "plan.json"
    plan("--output", str(saved))
    code, out, _ = cli("route", "show", str(saved))
    assert code == 0 and "Route ready" in out and "Alternatives:" in out
    code, out, _ = cli("route", "show", str(saved), "--format", "json")
    assert json.loads(out)["status"] == "ready"
    gpx = tmp_path / "later.gpx"
    code, _, err = cli("route", "export", str(saved), "--candidate", "2", "--gpx", str(gpx))
    assert code == 0 and gpx.is_file() and "wrote" in err


def test_saved_plan_problems_are_reported_not_raised(tmp_path: Path):
    assert cli("route", "show", str(tmp_path / "missing.json"))[0] == 1
    junk = tmp_path / "junk.json"
    junk.write_text("[1, 2]")
    assert "not a plan" in cli("route", "show", str(junk))[2]
    failed = tmp_path / "failed.json"
    failed.write_text(json.dumps({"status": "no_route", "errors": []}))
    assert cli("route", "export", str(failed), "--gpx", str(tmp_path / "x.gpx"))[0] == 1
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps({"status": "ready", "route": cand(1)}))
    assert cli("route", "export", str(ok))[0] == 2  # nothing to write


def test_renderers_cope_with_missing_data():
    assert (
        minutes(None) == "-" and minutes(59 * 60) == "59 min" and minutes(3 * 3600) == "3 h 00 min"
    )
    assert megabytes(None) == "size unknown" and megabytes(139_294_448) == "139 MB"
    assert "Route ready" in plan_text({"status": "ready", "route": {"metrics": {}}})
    weather = {"summary": {"temperature_min_c": 4.0, "temperature_max_c": 11.0,
                           "headwind_mean_kmh": 7.0, "after_dark": True}}  # fmt: skip
    text = plan_text({"status": "ready", "route": {"metrics": {}, "weather": weather}})
    assert "4..11 C" in text and "headwind +7 km/h" in text and "after dark" in text


# ----------------------------------------------------------------------------- poi


def poi(ident="node/1", **kw: Any) -> Poi:
    base = {"category": "historic", "kind": "sight", "lon": 10.5, "lat": 52.2, "name": "Burg"}
    return Poi(id=ident, **{**base, **kw})


class StubPois:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def along_route(self, line, categories, *, buffer_m, per_category_limit=None, **kw):
        self.calls.append(("along", list(line), categories, buffer_m, per_category_limit))
        return PoiSearchResult(
            [poi(fame=36, distance_from_route_m=120.0, along_route_km=3.4)],
            truncated=True,
            fame_status="partial",
        )

    async def in_bbox(self, bbox, categories, *, per_category_limit=None):
        self.calls.append(("bbox", bbox, categories, per_category_limit))
        return PoiSearchResult([poi(fame=None)], truncated=False, fame_status="ok")

    async def info(self, **kw: Any) -> PoiInfo:
        self.calls.append(("info", kw))
        return PoiInfo(
            title="Burg",
            extract="Eine Burg.",
            links=[
                PoiLink(kind="wikipedia", label="Wikipedia (de)", url="https://de.wikipedia.org/x")
            ],
            attribution=["Map data: OpenStreetMap contributors (ODbL)"],
        )


@pytest.fixture
def pois(monkeypatch: pytest.MonkeyPatch) -> StubPois:
    stub = StubPois()
    monkeypatch.setattr(api_module, "_poi_service", stub)
    return stub


def test_poi_categories_are_listed_as_text_and_json():
    code, out, _ = cli("poi", "categories")
    assert code == 0 and "viewpoint" in out and "sight" in out and "service" in out
    assert {c["key"] for c in json.loads(cli("poi", "categories", "--format", "json")[1])} >= {
        "viewpoint",
        "water",
    }


def test_poi_along_reads_a_saved_plan_and_ranks_by_fame(pois: StubPois, tmp_path: Path):
    saved = tmp_path / "plan.json"
    saved.write_text(
        json.dumps({"status": "ready", "route": cand(1), "candidates": [cand(1), cand(2)]})
    )
    code, out, _ = cli(
        "poi", "along", "--route", str(saved), "--candidate", "2",
        "--category", "historic,museum", "--buffer-m", "900", "--limit", "5",
    )  # fmt: skip
    assert code == 0 and "Burg" in out and "36" in out and "partial" in out
    assert "best-known per kind" in out  # truncated
    kind, line, categories, buffer_m, limit = pois.calls[0]
    assert line[0][0] == pytest.approx(10.52) and categories == ["historic", "museum"]
    assert buffer_m == 900 and limit == 5


def test_poi_along_accepts_geojson_and_a_straight_line(pois: StubPois, tmp_path: Path):
    geo = tmp_path / "r.geojson"
    geo.write_text(json.dumps({"type": "Feature", "geometry": cand(1)["geometry_geojson"]}))
    assert cli("poi", "along", "--route", str(geo), "--format", "json")[0] == 0
    code, _, _ = cli("poi", "along", "--from", "52.2,10.5", "--to", "52.3,10.6")
    assert code == 0 and pois.calls[-1][1] == [(10.5, 52.2), (10.6, 52.3)]


@pytest.mark.parametrize(
    "argv",
    [
        ("poi", "along"),
        ("poi", "along", "--from", "52.2,10.5"),
        ("poi", "along", "--from", "nope", "--to", "52.3,10.6"),
        ("poi", "along", "--route", "/does/not/exist.json"),
        ("poi", "along", "--from", "52,10", "--to", "52,11", "--category", "casino"),
        ("poi", "bbox", "--bbox", "10,52,12,53"),
        ("poi", "info"),
    ],
)
def test_poi_usage_mistakes_exit_2(pois: StubPois, argv):
    assert cli(*argv)[0] == 2


def test_poi_bbox_and_info(pois: StubPois):
    code, out, _ = cli("poi", "bbox", "--bbox", "10.5,52.2,10.6,52.3", "--category", "historic")
    assert code == 0 and "(unnamed)" not in out and "Burg" in out
    assert pois.calls[-1][0] == "bbox"
    code, out, _ = cli("poi", "info", "--wikidata", "Q4152", "--lang", "de")
    assert code == 0 and "Eine Burg." in out and "Wikipedia (de): https://de.wikipedia.org/x" in out
    assert "OpenStreetMap contributors" in out
    assert pois.calls[-1][1]["lang"] == "de"
    assert (
        json.loads(cli("poi", "info", "--wikidata", "Q4152", "--format", "json")[1])["title"]
        == "Burg"
    )


def test_poi_commands_say_so_when_pois_are_switched_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(api_module, "_poi_service", None)
    code, _, err = cli("poi", "bbox", "--bbox", "10.5,52.2,10.6,52.3")
    assert code == 1 and "switched off" in err


def test_poi_text_table_handles_unnamed_and_empty():
    assert "No points of interest" in poi_text([])
    text = poi_text([{"id": "node/1", "category": "viewpoint", "lat": 1.0, "lon": 2.0}])
    assert "(unnamed)" in text and "node/1" in text


# --------------------------------------------------------------------------- brouter

SOURCE = "https://brouter.de/brouter/segments4/"


def test_brouter_needed_lists_the_tiles_of_a_trip_and_whether_they_are_on_disk(tmp_path: Path):
    (tmp_path / "E10_N50.rd5").write_bytes(b"x")
    code, out, _ = cli(
        "brouter", "needed", "--origin", "51.75,-1.25", "--destination", "52.26,10.52",
        "--dir", str(tmp_path),
    )  # fmt: skip
    assert code == 0
    assert "W5_N50" in out and "MISSING" in out and "E10_N50" in out and "on disk" in out
    assert f"{SOURCE}W5_N50.rd5" in out
    code, out, _ = cli("brouter", "needed", "--origin", "51.75,-1.25", "--destination", "52,10")
    assert "unknown" in out  # no folder known: presence is not guessed


def test_brouter_needed_wants_coordinates():
    code, _, err = cli("brouter", "needed", "--origin", "Oxford", "--destination", "52,10")
    assert code == 2 and "not LAT,LON" in err


@respx.mock
def test_brouter_info_shows_presence_size_and_free_space(tmp_path: Path):
    respx.head(f"{SOURCE}W5_N50.rd5").mock(
        return_value=httpx.Response(200, headers={"content-length": "139294448"})
    )
    code, out, _ = cli("brouter", "info", "W5_N50", "--dir", str(tmp_path))
    assert code == 0 and "missing" in out and "139 MB" in out and "free disk space" in out
    assert cli("brouter", "info", "../x", "--dir", str(tmp_path))[0] == 2


def test_brouter_needs_a_tile_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("BROUTER_SEGMENTS_DIR", raising=False)
    code, _, err = cli("brouter", "info", "W5_N50", "--dir", str(tmp_path / "nope"))
    assert code == 1 and "tile folder" in err


def mock_tile(name: str, content: bytes) -> None:
    respx.head(f"{SOURCE}{name}.rd5").mock(
        return_value=httpx.Response(200, headers={"content-length": str(len(content))})
    )
    respx.get(f"{SOURCE}{name}.rd5").mock(return_value=httpx.Response(200, content=content))


@respx.mock
def test_brouter_download_asks_first_then_downloads_atomically(tmp_path: Path):
    mock_tile("W5_N50", b"t" * 5000)
    asked: list[str] = []
    out, err = io.StringIO(), io.StringIO()
    code = brouter_cmd.run(
        parse("brouter", "download", "W5_N50", "--dir", str(tmp_path)),
        out, err,
        confirm=lambda q: asked.append(q) or True,
        poll_interval_s=0.01,
    )  # fmt: skip
    assert code == 0 and (tmp_path / "W5_N50.rd5").read_bytes() == b"t" * 5000
    assert not list(tmp_path.glob("*.part"))
    assert "W5_N50" in asked[0] and "into" in asked[0] and "restart BRouter" in out.getvalue()


@respx.mock
def test_brouter_download_declined_downloads_nothing(tmp_path: Path):
    mock_tile("W5_N50", b"t")
    get = respx.get(f"{SOURCE}W5_N50.rd5")
    out, err = io.StringIO(), io.StringIO()
    code = brouter_cmd.run(
        parse("brouter", "download", "W5_N50", "--dir", str(tmp_path)),
        out, err, confirm=lambda q: False,
    )  # fmt: skip
    assert code == 1 and "not downloaded" in err.getvalue()
    assert not get.called and not list(tmp_path.iterdir())


@respx.mock
def test_brouter_download_without_a_terminal_needs_yes(tmp_path: Path, monkeypatch):
    mock_tile("W5_N50", b"t" * 10)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code, _, err = cli("brouter", "download", "W5_N50", "--dir", str(tmp_path))
    assert code == 1 and "--yes" in err and not list(tmp_path.iterdir())
    code, out, _ = cli("brouter", "download", "W5_N50", "--dir", str(tmp_path), "--yes")
    assert code == 0 and (tmp_path / "W5_N50.rd5").is_file()


@respx.mock
def test_brouter_download_skips_present_tiles_and_reports_failures(tmp_path: Path):
    (tmp_path / "E10_N50.rd5").write_bytes(b"mine")
    respx.head(f"{SOURCE}E10_N50.rd5").mock(
        return_value=httpx.Response(200, headers={"content-length": "4"})
    )
    code, out, _ = cli("brouter", "download", "E10_N50", "--dir", str(tmp_path), "--yes")
    assert code == 0 and "nothing to download" in out
    respx.head(f"{SOURCE}W5_N50.rd5").mock(
        return_value=httpx.Response(200, headers={"content-length": "10"})
    )
    respx.get(f"{SOURCE}W5_N50.rd5").mock(return_value=httpx.Response(500))
    code, out, err = cli("brouter", "download", "W5_N50", "--dir", str(tmp_path), "--yes")
    assert code == 1 and "failed" in out and "HTTP 500" in err
    assert not (tmp_path / "W5_N50.rd5").exists()


# ----------------------------------------------------------------------- history, status


def seeded_history(monkeypatch: pytest.MonkeyPatch) -> InMemoryRouteHistory:
    history = InMemoryRouteHistory()
    state = {
        "status": "ready",
        "raw_input": {"origin": "A", "destination": "B"},
        "constraints": {"bike_type": "gravel"},
        "resolved_origin": {"lon": 10.5, "lat": 52.3},
        "resolved_destination": {"lon": 10.6, "lat": 52.4},
        "candidates": [cand(1)],
        "selected_candidate": cand(1),
        "explanation": "x",
        "artifacts": {},
        "route_id": "x",
    }
    state["candidates"] = [{**cand(1), "metrics": {"distance_m": 12_000.0}}]
    record = record_from_state("a" * 32, state)
    history.save(record.model_copy(update={"created_at": datetime(2026, 9, 1, 12, tzinfo=UTC)}))
    monkeypatch.setattr(api_module, "_history", history)
    return history


def test_history_list_show_and_stats(monkeypatch: pytest.MonkeyPatch):
    seeded_history(monkeypatch)
    code, out, _ = cli("history", "list")
    assert code == 0 and "a" * 32 in out and "ready" in out and "gravel" in out and "12.0 km" in out
    assert cli("history", "list", "--status", "no_route")[1].startswith("no plans recorded")
    assert json.loads(cli("history", "list", "--format", "json")[1])[0]["plan_id"] == "a" * 32
    code, out, _ = cli("history", "show", "a" * 32)
    assert code == 0 and json.loads(out)["plan_id"] == "a" * 32
    code, out, _ = cli("history", "stats")
    assert code == 0 and "1 plans" in out and "100 % ready" in out
    assert json.loads(cli("history", "stats", "--format", "json")[1])["total_plans"] == 1


def test_history_errors(monkeypatch: pytest.MonkeyPatch):
    seeded_history(monkeypatch)
    assert cli("history", "show", "f" * 32)[0] == 1
    assert "not found" in cli("history", "show", "nope")[2]
    assert cli("history", "list", "--bbox", "bad")[0] == 2
    assert cli("history", "list", "--since", "yesterday-ish")[0] == 2  # argparse type error
    monkeypatch.setattr(api_module, "_history", None)
    code, _, err = cli("history", "list")
    assert code == 1 and "DATABASE_URL" in err


class StubMonitor:
    def __init__(self, ready: bool) -> None:
        self.ready = ready

    async def check(self) -> HealthReport:
        now = datetime.now(UTC)
        components = [
            ComponentReport("brouter", "routing", True, "ok" if self.ready else "unavailable",
                            12, None if self.ready else "down", now),
            ComponentReport("weather", "weather", False, "ok", 30, None, now),
        ]  # fmt: skip
        return HealthReport("ok" if self.ready else "unavailable", self.ready, now, components)


@pytest.mark.parametrize(("ready", "code"), [(True, 0), (False, 1)])
def test_status_probes_the_components_and_exits_by_readiness(monkeypatch, ready, code):
    monkeypatch.setattr(api_module, "_health_monitor", StubMonitor(ready))
    got, out, _ = cli("status", "show")
    assert got == code
    assert f"Ready: {'yes' if ready else 'NO'}" in out and "brouter" in out
    assert "weather" in out and "(optional)" in out and "Features:" in out
    data = json.loads(cli("status", "show", "--format", "json")[1])
    assert data["readiness"]["ready"] is ready and "engines" in data["capabilities"]


def test_status_text_lists_features_and_engines():
    text = status_text(
        {"ready": True, "status": "ok", "components": []},
        {"pois": True, "weather": False, "engines": ["brouter", "ors"]},
    )
    assert "pois: on" in text and "weather: off" in text and "engines: brouter, ors" in text


def test_every_group_is_registered_and_has_help():
    out = build_parser().format_help()
    for group in ("route", "poi", "brouter", "history", "status"):
        assert group in out
    for group in ("poi", "brouter", "history", "status"):
        assert cli(group)[0] == 2  # a group without a command prints its help


def test_a_hand_edited_saved_route_is_reported_not_a_traceback(tmp_path: Path):
    broken = tmp_path / "broken.json"
    route = {k: v for k, v in cand(1).items() if k != "geometry_geojson"}
    broken.write_text(json.dumps({"status": "ready", "route": route}))
    code, _, err = cli("route", "export", str(broken), "--gpx", str(tmp_path / "x.gpx"))
    assert code == 1 and "not a valid candidate" in err and "geometry_geojson" in err
    assert not (tmp_path / "x.gpx").exists()


def test_history_pagination_is_checked_before_anything_runs(monkeypatch: pytest.MonkeyPatch):
    seeded_history(monkeypatch)
    for argv in (
        ["--limit", "0"], ["--limit", "201"], ["--limit", "ten"], ["--offset", "-1"],
    ):  # fmt: skip
        out, err = io.StringIO(), io.StringIO()
        code = main(["history", "list", *argv], stdout=out, stderr=err)
        assert code == 2 and out.getvalue() == ""
    assert cli("history", "list", "--limit", "200", "--offset", "0")[0] == 0


def test_a_validation_error_inside_an_endpoint_function_is_a_usage_error_not_a_crash():
    from pydantic import BaseModel

    from bike_routing_agent.cli._handlers import call_handler

    class Strict(BaseModel):
        count: int

    async def raises() -> None:
        Strict(count="many")  # type: ignore[arg-type]

    err = io.StringIO()
    result, code = call_handler(raises, err)
    assert result is None and code == 2 and "invalid request: count" in err.getvalue()


def line_file(tmp_path: Path, data: Any) -> Path:
    path = tmp_path / "route.geojson"
    path.write_text(json.dumps(data))
    return path


def test_an_empty_feature_collection_is_not_a_route(pois: StubPois, tmp_path: Path):
    empty = line_file(tmp_path, {"type": "FeatureCollection", "features": []})
    code, _, err = cli("poi", "along", "--route", str(empty))
    assert code == 2 and "without any feature" in err and "Traceback" not in err
    assert pois.calls == []


@pytest.mark.parametrize(
    "data",
    [
        {"type": "FeatureCollection", "features": [{"type": "Feature"}]},
        {"type": "Feature", "geometry": None},
        {"type": "Point", "coordinates": [1, 2]},
        [1, 2, 3],
        {"status": "ready"},
    ],
)
def test_other_unusable_route_files_are_usage_errors_too(pois: StubPois, tmp_path: Path, data):
    code, _, err = cli("poi", "along", "--route", str(line_file(tmp_path, data)))
    assert code == 2 and "invalid request" in err and "Traceback" not in err


def test_the_parts_of_a_multilinestring_are_joined_only_when_they_connect(
    pois: StubPois, tmp_path: Path
):
    connected = {
        "type": "MultiLineString",
        "coordinates": [[[10.0, 52.0], [10.1, 52.0]], [[10.1, 52.0], [10.2, 52.0]]],
    }
    assert cli("poi", "along", "--route", str(line_file(tmp_path, connected)))[0] == 0
    assert pois.calls[-1][1] == [(10.0, 52.0), (10.1, 52.0), (10.2, 52.0)]  # no duplicate point
    gap = {
        "type": "MultiLineString",
        "coordinates": [[[10.0, 52.0], [10.1, 52.0]], [[11.0, 53.0], [11.1, 53.0]]],
    }
    calls = len(pois.calls)
    code, _, err = cli("poi", "along", "--route", str(line_file(tmp_path, gap)))
    assert code == 2 and "not connected" in err and len(pois.calls) == calls  # nothing searched
