"""``bike-router route`` group: plan routes from the command line."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import IO, Any

EXIT_OK = 0
EXIT_FAILURE = 1


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    route = subparsers.add_parser("route", help="plan and inspect bike routes")
    route_sub = route.add_subparsers(dest="command", metavar="<command>")
    if route_sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")

    plan = route_sub.add_parser("plan", help="plan a route between two places")
    plan.add_argument("--origin", required=True, help="place text, or 'lat,lon'")
    plan.add_argument("--destination", required=True, help="place text, or 'lat,lon'")
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
    plan.add_argument("--loop", action="store_true", help="return to the origin")
    plan.add_argument(
        "--output",
        type=Path,
        default=None,
        help="also write the JSON response to this file",
    )
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


def build_request(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "origin": _place(args.origin),
        "destination": _place(args.destination),
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
        },
    }


def _default_graph_factory() -> Any:
    from bike_routing_agent.api import build_graph_for_settings
    from bike_routing_agent.config import settings

    return build_graph_for_settings(settings)


def run(
    args: argparse.Namespace,
    stdout: IO[str],
    stderr: IO[str],
    *,
    graph_factory: Any = None,
) -> int:
    if args.command != "plan":
        print("unknown route command", file=stderr)
        return 2

    request = build_request(args)
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
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    print(rendered, file=stdout)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")

    return EXIT_OK if payload["status"] == "ready" else EXIT_FAILURE


def _response_payload(final_state: dict[str, Any]) -> dict[str, Any]:
    status = final_state.get("status", "provider_failure")
    payload: dict[str, Any] = {
        "status": status,
        "explanation": final_state.get("explanation"),
        "errors": final_state.get("errors", []),
    }
    if status == "ready":
        selected = dict(final_state.get("selected_candidate") or {})
        selected.pop("raw_provider_response", None)
        payload["route"] = selected
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
