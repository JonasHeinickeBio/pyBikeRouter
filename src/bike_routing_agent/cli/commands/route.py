"""``bike-router route`` group: plan routes from the command line."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any

from pydantic import ValidationError

from bike_routing_agent.cli.render import plan_text
from bike_routing_agent.models import MAX_FORECAST_DAYS, RouteCandidate, RoutePlanAPIRequest
from bike_routing_agent.storage.history import record_from_state

EXIT_OK = 0
EXIT_FAILURE = 1


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    route = subparsers.add_parser("route", help="plan and inspect bike routes")
    route_sub = route.add_subparsers(dest="command", metavar="<command>")
    if route_sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")

    plan = route_sub.add_parser("plan", help="plan a route between two places")
    plan.add_argument(
        "--text",
        default=None,
        metavar="REQUEST",
        help="describe the ride in plain words instead of --origin/--destination "
        '(e.g. "a 50 km gravel loop from Braunschweig, tomorrow at 8"); needs '
        "LLM_PARSER_ENABLED",
    )
    plan.add_argument(
        "--timezone",
        default=None,
        metavar="IANA",
        help="your time zone for --text (e.g. Europe/Berlin; default UTC)",
    )
    plan.add_argument("--origin", default=None, help="place text, or 'lat,lon'")
    plan.add_argument(
        "--destination",
        default=None,
        help="place text, or 'lat,lon' (omit with --loop: a loop ends where it starts)",
    )
    plan.add_argument(
        "--via",
        action="append",
        default=[],
        metavar="PLACE",
        help="optional waypoint (place text or 'lat,lon'); repeatable",
    )
    plan.add_argument(
        "--bike-type",
        default="gravel",
        help=(
            "road|gravel|touring|mountain|city|ebike|commuter|recumbent "
            "(see 'providers list' for the engine profile map)"
        ),
    )
    plan.add_argument("--target-distance-km", type=float, default=None)
    plan.add_argument("--max-distance-km", type=float, default=None)
    plan.add_argument("--max-ascent-m", type=float, default=None)
    plan.add_argument("--prefer-surfaces", default="", help="comma-separated surface names")
    plan.add_argument("--avoid-surfaces", default="", help="comma-separated surface names")
    no_traffic = plan.add_mutually_exclusive_group()
    no_traffic.add_argument(
        "--avoid-high-traffic",
        dest="avoid_high_traffic_roads",
        action="store_true",
        default=True,
    )
    no_traffic.add_argument(
        "--allow-high-traffic",
        dest="avoid_high_traffic_roads",
        action="store_false",
    )
    ferries = plan.add_mutually_exclusive_group()
    ferries.add_argument("--avoid-ferries", dest="avoid_ferries", action="store_true", default=True)
    ferries.add_argument("--allow-ferries", dest="avoid_ferries", action="store_false")
    plan.add_argument(
        "--loop",
        action="store_true",
        help="round trip back to the origin; omit --destination and set --target-distance-km",
    )
    plan.add_argument(
        "--loop-direction",
        choices=("clockwise", "counterclockwise"),
        default="clockwise",
        help="sweep direction for a loop whose shape is not drawn with --via (default: clockwise)",
    )
    plan.add_argument(
        "--max-alternatives",
        type=int,
        default=None,
        metavar="N",
        help="keep at most N distinct alternatives (1-5; default: every scored candidate)",
    )
    plan.add_argument(
        "--engine",
        action="append",
        default=[],
        choices=("ors", "brouter", "valhalla"),
        metavar="ENGINE",
        help="route with this engine for this plan instead of the configured ones "
        "(ors|brouter|valhalla; repeatable)",
    )
    plan.add_argument(
        "--poi-stops",
        type=int,
        default=None,
        metavar="N",
        help="route past the N best-known sights between origin and destination (1-5)",
    )
    plan.add_argument(
        "--poi-categories",
        default="",
        help="comma-separated sight kinds the stops may be (see 'poi categories'; default: all)",
    )
    plan.add_argument("--poi-corridor-km", type=float, default=None, help="how far off the line")
    plan.add_argument(
        "--poi-min-fame", type=int, default=None, help="at least this many Wikipedia languages"
    )
    plan.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="json (default; machine readable) or text (a readable summary)",
    )
    plan.add_argument(
        "--candidate",
        type=int,
        default=None,
        metavar="RANK",
        help="with --gpx/--geojson: export the alternative of this rank (default: the best)",
    )
    plan.add_argument("--gpx", type=Path, default=None, help="write the route as a GPX file")
    plan.add_argument("--geojson", type=Path, default=None, help="write the route as GeoJSON")
    plan.add_argument(
        "--output",
        type=Path,
        default=None,
        help="also write the JSON response to this file",
    )
    plan.add_argument(
        "--departure-time",
        default=None,
        metavar="ISO8601",
        help="when the ride starts, for the weather forecast (e.g. 2026-10-08T07:30:00Z; "
        "default: now; no UTC offset means UTC; at most 14 days ahead)",
    )
    plan.add_argument(
        "--no-record",
        action="store_true",
        help="do not record the plan in the route history even when DATABASE_URL is set",
    )

    show = route_sub.add_parser("show", help="show a saved plan (from 'plan --output') again")
    show.add_argument("file", type=Path, help="the JSON file written by 'route plan --output'")
    show.add_argument("--format", choices=("json", "text"), default="text")

    export = route_sub.add_parser("export", help="write a saved plan's route as GPX or GeoJSON")
    export.add_argument("file", type=Path, help="the JSON file written by 'route plan --output'")
    export.add_argument("--candidate", type=int, default=None, metavar="RANK")
    export.add_argument("--gpx", type=Path, default=None)
    export.add_argument("--geojson", type=Path, default=None)
    return route


def _place(text: str) -> Any:
    parts = [p.strip() for p in text.replace(" ", ",").split(",") if p.strip()]
    if len(parts) == 2:
        try:
            a, b = float(parts[0]), float(parts[1])
        except ValueError:
            return text
        # "lat,lon" as documented for the web UI.
        if -90 <= a <= 90 and -180 <= b <= 180:
            return {"lon": b, "lat": a}
    return text


def _csv(text: str) -> list[str]:
    return [s.strip() for s in text.split(",") if s.strip()]


def _loop_usage_error(args: argparse.Namespace) -> str | None:
    """Loop contract as CLI usage messages (issue #5); the API enforces the
    same rules through the request model."""
    if args.text is not None:
        if not args.text.strip():
            return "--text must not be empty"
        if args.origin or args.destination or args.via:
            return "--text replaces --origin/--destination/--via: use one or the other"
        return None
    if not args.origin:
        return "--origin is required (or describe the ride with --text)"
    if args.loop and args.destination:
        return "--loop takes no --destination: a loop starts and ends at --origin"
    if args.loop and args.target_distance_km is None:
        return "--loop requires --target-distance-km (loop size is not invented silently)"
    if not args.loop and not args.destination:
        return "--destination is required unless --loop is set"
    if args.departure_time is not None:
        try:
            parsed = datetime.fromisoformat(args.departure_time.replace("Z", "+00:00"))
        except ValueError:
            return f"--departure-time {args.departure_time!r} is not an ISO-8601 datetime"
        aware = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        if aware > datetime.now(UTC) + timedelta(days=MAX_FORECAST_DAYS):
            return f"--departure-time is more than {MAX_FORECAST_DAYS} days ahead"
    return None


def _model_error(request: dict[str, Any]) -> str | None:
    """The API's own request rules (loop vs stops, via limits, engines, ranges) as one message."""
    try:
        RoutePlanAPIRequest.model_validate(request)
    except ValidationError as exc:
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" if e["loc"] else str(e["msg"])
            for e in exc.errors()
        )
    return None


def build_request(args: argparse.Namespace) -> dict[str, Any]:
    if args.text is not None:
        return {"text": args.text, "timezone": args.timezone}
    request: dict[str, Any] = {
        "origin": _place(args.origin),
        "via": [_place(v) for v in args.via],
        "constraints": {
            "bike_type": args.bike_type,
            "target_distance_km": args.target_distance_km,
            "max_distance_km": args.max_distance_km,
            "max_ascent_m": args.max_ascent_m,
            "prefer_surfaces": _csv(args.prefer_surfaces),
            "avoid_surfaces": _csv(args.avoid_surfaces),
            "avoid_high_traffic_roads": args.avoid_high_traffic_roads,
            "avoid_ferries": args.avoid_ferries,
            "return_to_origin": args.loop,
            "loop_direction": args.loop_direction,
        },
    }
    if args.destination:
        request["destination"] = _place(args.destination)
    if args.departure_time is not None:
        request["departure_time"] = args.departure_time
    if args.max_alternatives is not None:
        request["max_alternatives"] = args.max_alternatives
    if args.engine:
        request["routing_engines"] = list(args.engine)
    if args.poi_stops is not None:
        stops: dict[str, Any] = {"count": args.poi_stops}
        if args.poi_categories:
            stops["categories"] = _csv(args.poi_categories)
        if args.poi_corridor_km is not None:
            stops["corridor_km"] = args.poi_corridor_km
        if args.poi_min_fame is not None:
            stops["min_fame"] = args.poi_min_fame
        request["poi_stops"] = stops
    return request


def _default_graph_factory() -> Any:
    from bike_routing_agent.api import build_graph_for_settings
    from bike_routing_agent.config import settings

    return build_graph_for_settings(settings)


def _default_history_factory() -> Any:
    """The configured route history, or ``None`` when no database is set."""
    from bike_routing_agent.api import build_storage
    from bike_routing_agent.config import settings

    if not settings.database_url:
        return None
    return build_storage(settings)[1]


def _record_plan(history_factory: Any, final_state: dict[str, Any], stderr: IO[str]) -> str | None:
    """Record the finished plan like the API does: best effort, never fatal.

    Returns the ``plan_id`` (the artifact id for ready plans) or ``None`` when
    there is no history or recording failed -- the plan was already computed,
    so a database outage is a warning, not a failed command.
    """
    try:
        history = history_factory()
        if history is None:
            return None
        plan_id = str(final_state.get("route_id") or uuid.uuid4().hex)
        history.save(record_from_state(plan_id, final_state))
        return plan_id
    except Exception as exc:  # noqa: BLE001 - history is optional; report and move on
        print(f"warning: could not record the plan in the history: {exc}", file=stderr)
        return None


def run(
    args: argparse.Namespace,
    stdout: IO[str],
    stderr: IO[str],
    *,
    graph_factory: Any = None,
    history_factory: Any = None,
) -> int:
    if args.command == "show":
        return _show(args, stdout, stderr)
    if args.command == "export":
        return _export_saved(args, stderr)
    if args.command != "plan":
        print("unknown route command", file=stderr)
        return 2

    usage_error = _loop_usage_error(args)
    if (
        usage_error is None
        and args.text is not None
        and (args.poi_stops is not None or args.engine or args.max_alternatives is not None)
    ):
        usage_error = (
            "--poi-stops, --engine and --max-alternatives apply to --origin/--destination "
            "plans; with --text, say it in the description (alternatives) or use the options "
            "without --text"
        )
    if usage_error is None and (args.candidate is not None) and not (args.gpx or args.geojson):
        usage_error = "--candidate only selects what --gpx / --geojson write"
    request: dict[str, Any] = {}
    if usage_error is None:
        request = build_request(args)
        if args.text is None:
            usage_error = _model_error(request)
    if usage_error is not None:
        print(f"invalid request: {usage_error}", file=stderr)
        return 2

    factory = graph_factory or _default_graph_factory
    try:
        graph = factory()
        final_state = asyncio.run(graph.ainvoke({"raw_input": request}))
    except ValueError as exc:
        print(f"invalid request: {exc}", file=stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports any provider/runtime failure
        print(f"routing failed: {exc}", file=stderr)
        return EXIT_FAILURE

    payload = _response_payload(final_state)
    payload["plan_id"] = (
        None
        if args.no_record
        else _record_plan(history_factory or _default_history_factory, final_state, stderr)
    )
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    print(plan_text(payload) if args.format == "text" else rendered, file=stdout)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")

    if (args.gpx or args.geojson) and payload["status"] == "ready":
        export_code = _write_exports(payload, args.candidate, args.gpx, args.geojson, stderr)
        if export_code != EXIT_OK:
            return export_code

    return EXIT_OK if payload["status"] == "ready" else EXIT_FAILURE


def _load_saved(path: Path, stderr: IO[str]) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read {path}: {exc}", file=stderr)
        return None
    if not isinstance(payload, dict) or "status" not in payload:
        print(f"{path} is not a plan written by 'route plan --output'", file=stderr)
        return None
    return payload


def _show(args: argparse.Namespace, stdout: IO[str], stderr: IO[str]) -> int:
    payload = _load_saved(args.file, stderr)
    if payload is None:
        return EXIT_FAILURE
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False), file=stdout)
    else:
        print(plan_text(payload), file=stdout)
    return EXIT_OK if payload.get("status") == "ready" else EXIT_FAILURE


def _export_saved(args: argparse.Namespace, stderr: IO[str]) -> int:
    payload = _load_saved(args.file, stderr)
    if payload is None:
        return EXIT_FAILURE
    if not (args.gpx or args.geojson):
        print("nothing to write: give --gpx and/or --geojson", file=stderr)
        return 2
    if payload.get("status") != "ready":
        print(f"the saved plan has no route (status: {payload.get('status')})", file=stderr)
        return EXIT_FAILURE
    return _write_exports(payload, args.candidate, args.gpx, args.geojson, stderr)


def _write_exports(
    payload: dict[str, Any],
    rank: int | None,
    gpx: Path | None,
    geojson: Path | None,
    stderr: IO[str],
) -> int:
    """Write the chosen alternative (default: the selected route) as GPX and/or GeoJSON."""
    from bike_routing_agent.exporters.geojson import to_geojson_str
    from bike_routing_agent.exporters.gpx import to_gpx_str

    candidates = payload.get("candidates") or [payload.get("route")]
    chosen = (
        payload.get("route")
        if rank is None
        else next((c for c in candidates if c and c.get("rank") == rank), None)
    )
    if not chosen:
        ranks = sorted(c["rank"] for c in candidates if c and c.get("rank"))
        print(f"no alternative of rank {rank}; ranks in this plan: {ranks}", file=stderr)
        return 2
    try:
        candidate = RouteCandidate.model_validate(chosen)
    except ValidationError as exc:
        missing = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
        print(
            f"the saved route is not a valid candidate (check: {', '.join(missing)})", file=stderr
        )
        return EXIT_FAILURE
    for path, render in ((gpx, to_gpx_str), (geojson, to_geojson_str)):
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render(candidate), encoding="utf-8")
            print(f"wrote {path}", file=stderr)
    return EXIT_OK


def _response_payload(final_state: dict[str, Any]) -> dict[str, Any]:
    status = final_state.get("status", "provider_failure")
    payload: dict[str, Any] = {
        "status": status,
        "explanation": final_state.get("explanation"),
        "errors": final_state.get("errors", []),
    }
    if final_state.get("interpretation"):
        # How a --text request was read, so a wrong parse is visible.
        payload["interpretation"] = final_state["interpretation"]
    if final_state.get("poi_stops_status") is not None:
        payload["poi_stops_status"] = final_state["poi_stops_status"]
    if status == "ready":
        selected = dict(final_state.get("selected_candidate") or {})
        selected.pop("raw_provider_response", None)
        payload["route"] = selected
        # Every ranked alternative (with its geometry, so any of them can be exported later).
        payload["candidates"] = sorted(
            (
                {k: v for k, v in c.items() if k != "raw_provider_response"}
                for c in final_state.get("candidates", [])
            ),
            key=lambda c: c.get("rank") if c.get("rank") is not None else 10**9,
        )
        payload["weather_status"] = final_state.get("weather_status")
        payload["poi_stops"] = final_state.get("poi_stops")
        exported = final_state.get("artifacts", {})
        artifacts: dict[str, str] = {}
        if "geojson_file" in exported:
            artifacts["geojson_file"] = str(Path(exported["geojson_file"]))
        if "gpx_file" in exported:
            artifacts["gpx_file"] = str(Path(exported["gpx_file"]))
        payload["artifacts"] = artifacts
    elif status == "awaiting_clarification":
        payload["clarification"] = final_state.get("clarification", [])
    return payload
