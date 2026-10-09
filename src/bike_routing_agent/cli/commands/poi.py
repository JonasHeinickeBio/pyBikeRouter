"""``bike-router poi`` group: points of interest along a route or in an area.

Finds sights and services in OpenStreetMap, ranks them by how many Wikipedia languages describe
them, and reads up on one: the same as the web map's POI layer (docs/pois.md).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import IO, Any

from bike_routing_agent.cli._handlers import (
    EXIT_FAILURE,
    EXIT_OK,
    EXIT_USAGE,
    call_handler,
    dump_json,
    to_plain,
)
from bike_routing_agent.cli.render import poi_info_text, poi_text, table


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    poi = subparsers.add_parser("poi", help="points of interest: find, rank, read up")
    sub = poi.add_subparsers(dest="command", metavar="<command>")
    if sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--category",
            action="append",
            default=[],
            metavar="KIND",
            help="a kind to look for (see 'poi categories'); repeatable or comma-separated; "
            "default: all",
        )
        p.add_argument("--limit", type=int, default=None, help="most per kind (1-100)")
        p.add_argument("--format", choices=("text", "json"), default="text")

    categories = sub.add_parser("categories", help="list the kinds of POI")
    categories.add_argument("--format", choices=("text", "json"), default="text")

    along = sub.add_parser("along", help="POIs near a route")
    along.add_argument(
        "--route",
        type=Path,
        default=None,
        metavar="FILE",
        help="a plan saved with 'route plan --output', or a GeoJSON file with a line",
    )
    along.add_argument("--candidate", type=int, default=None, metavar="RANK")
    along.add_argument(
        "--from", dest="start", default=None, metavar="LAT,LON", help="a straight line start"
    )
    along.add_argument("--to", dest="end", default=None, metavar="LAT,LON")
    along.add_argument("--buffer-m", type=float, default=None, help="how far off the route")
    common(along)

    bbox = sub.add_parser("bbox", help="POIs inside an area")
    bbox.add_argument(
        "--bbox", required=True, metavar="W,S,E,N", help="min_lon,min_lat,max_lon,max_lat"
    )
    common(bbox)

    info = sub.add_parser("info", help="read up on one POI (Wikipedia, Wikidata, Wikivoyage)")
    info.add_argument("--wikidata", default=None, metavar="Q42")
    info.add_argument("--wikipedia", default=None, metavar="de:Title")
    info.add_argument("--osm-id", default=None, metavar="way/123")
    info.add_argument("--website", default=None)
    info.add_argument("--lang", default="en", help="language of the text (default: en)")
    info.add_argument("--format", choices=("text", "json"), default="text")
    return poi


def _categories(values: list[str]) -> list[str] | None:
    keys = [k.strip() for v in values for k in v.split(",") if k.strip()]
    return keys or None


# Parts of a MultiLineString that start within this distance of where the previous one ended
# are one continuous route; anything farther is a gap that must not be searched as if it were road.
_JOIN_TOLERANCE_M = 5.0


def _coordinates_from_file(path: Path, rank: int | None) -> list[list[float]]:
    """The route line from a saved plan or from GeoJSON (``ValueError`` when it is not one)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _line_of(data, rank, path)
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ValueError(
            f"{path} does not hold a route line ({type(exc).__name__}: {exc})"
        ) from exc


def _line_of(data: Any, rank: int | None, path: Path) -> list[list[float]]:
    geometry: Any
    if isinstance(data, dict) and "status" in data:  # a saved plan
        if data["status"] != "ready":
            raise ValueError(f"the saved plan has no route (status: {data['status']})")
        candidates = data.get("candidates") or [data["route"]]
        chosen = (
            data["route"]
            if rank is None
            else next((c for c in candidates if c.get("rank") == rank), None)
        )
        if chosen is None:
            raise ValueError(f"no alternative of rank {rank} in {path}")
        geometry = chosen["geometry_geojson"]
    elif isinstance(data, dict) and data.get("type") == "FeatureCollection":
        features = data.get("features") or []
        if not features:
            raise ValueError(f"{path} is a GeoJSON FeatureCollection without any feature")
        geometry = features[0]["geometry"]
    elif isinstance(data, dict) and data.get("type") == "Feature":
        geometry = data["geometry"]
    else:
        geometry = data
    if not isinstance(geometry, dict):
        raise ValueError("expected a GeoJSON LineString")
    if geometry.get("type") == "MultiLineString":
        return _joined(geometry["coordinates"])
    if geometry.get("type") != "LineString":
        raise ValueError("expected a LineString route")
    return [[float(p[0]), float(p[1])] for p in geometry["coordinates"]]


def _joined(parts: list[list[list[float]]]) -> list[list[float]]:
    """The parts of a MultiLineString as one line -- only when each starts where the last ended."""
    from bike_routing_agent.poi.geo import haversine_m

    line: list[list[float]] = []
    for part in parts:
        points = [[float(p[0]), float(p[1])] for p in part]
        if not points:
            continue
        if line and haversine_m(tuple(line[-1]), tuple(points[0])) > _JOIN_TOLERANCE_M:  # type: ignore[arg-type]
            raise ValueError(
                "the route is a MultiLineString whose parts are not connected; joining them would "
                "search a straight line through places the route never visits. Use a single line "
                "(or one part per run)"
            )
        line.extend(points if not line else points[1:])
    return line


def _lat_lon(text: str) -> list[float]:
    try:
        lat, lon = (float(p) for p in text.replace(" ", "").split(","))
    except ValueError:
        raise ValueError(f"{text!r} is not LAT,LON") from None
    return [lon, lat]


def run(args: argparse.Namespace, stdout: IO[str], stderr: IO[str]) -> int:
    from bike_routing_agent import api
    from bike_routing_agent.poi.categories import CATEGORIES
    from bike_routing_agent.poi.models import PoiAlongRouteRequest

    if args.command == "categories":
        rows = [{"key": c.key, "label": c.label, "kind": c.kind} for c in CATEGORIES]
        if args.format == "json":
            dump_json(rows, stdout)
        else:
            print(
                table([[r["key"], r["kind"], r["label"]] for r in rows], ["KIND", "TYPE", "WHAT"]),
                file=stdout,
            )
        return EXIT_OK

    if args.command == "along":
        try:
            if args.route is not None:
                if args.start or args.end:
                    raise ValueError("give --route, or --from and --to, not both")
                line = _coordinates_from_file(args.route, args.candidate)
            elif args.start and args.end:
                line = [_lat_lon(args.start), _lat_lon(args.end)]
            else:
                raise ValueError("give --route FILE, or --from LAT,LON and --to LAT,LON")
            request = PoiAlongRouteRequest(
                coordinates=line,
                categories=_categories(args.category),
                buffer_m=args.buffer_m,
                limit_per_category=args.limit,
            )
        except (OSError, ValueError) as exc:
            print(f"invalid request: {exc}", file=stderr)
            return EXIT_USAGE
        result, code = call_handler(lambda: api.pois_along_route(request), stderr)
    elif args.command == "bbox":
        result, code = call_handler(
            lambda: api.pois_in_bbox(
                args.bbox,
                ",".join(_categories(args.category) or []) or None,
                args.limit,
            ),
            stderr,
        )
    elif args.command == "info":
        if not (args.wikidata or args.wikipedia or args.osm_id):
            print("invalid request: give --wikidata, --wikipedia or --osm-id", file=stderr)
            return EXIT_USAGE
        info_result, info_code = call_handler(
            lambda: api.poi_info(
                args.wikidata, args.wikipedia, args.osm_id, args.website, args.lang
            ),
            stderr,
        )
        if info_result is None:
            return info_code
        info = to_plain(info_result)
        if args.format == "json":
            dump_json(info, stdout)
        else:
            print(poi_info_text(info), file=stdout)
        return EXIT_OK
    else:
        print("unknown poi command", file=stderr)
        return EXIT_USAGE

    if result is None:
        return code if code else EXIT_FAILURE
    plain = to_plain(result)
    if args.format == "json":
        dump_json(plain, stdout)
    else:
        print(
            poi_text(plain["pois"], truncated=plain["truncated"], fame_status=plain["fame_status"]),
            file=stdout,
        )
    return EXIT_OK
