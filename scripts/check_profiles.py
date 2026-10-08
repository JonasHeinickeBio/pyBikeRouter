"""Check what each routing profile actually does on a set of routes.

Routes every bike type (or the profiles named with ``--profile``) over the five
benchmark routes plus a few extra regional ones through a local BRouter, and
reads the OSM tags BRouter returns for every segment it chose: surface and road
class shares, traffic exposure and climbing. No Overpass needed (the public one
is too unreliable for 40+ lookups), and it is the data the profile itself saw.

    poetry run python scripts/check_profiles.py                    # all bike types
    poetry run python scripts/check_profiles.py --profile custom_gravel-v1 custom_gravel-v2
    poetry run python scripts/check_profiles.py --out profiles.json

Needs a BRouter with segments (docker/brouter); findings: docs/profile-evaluation.md.
Metrics that are proxies, not truth: "traffic" is the share of the route on
trunk/primary/secondary/tertiary roads *without* a mapped cycle lane/track or
bicycle=designated; "unknown" is length with no usable surface tag.
"""

from __future__ import annotations

import argparse
import collections
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from bike_routing_agent.config import BROUTER_PROFILE_MAP, SURFACE_TAXONOMY, Settings

BENCHMARK = Path(__file__).resolve().parents[1] / "benchmarks" / "core-v1.json"
# Extra regional routes (Lower Saxony / Harz): hills, forest and flat farmland, where
# profile differences show up; the benchmark routes are mostly town.
EXTRA_ROUTES: dict[str, tuple[dict[str, float], dict[str, float]]] = {
    "elm-ride": ({"lon": 10.8167, "lat": 52.25}, {"lon": 11.0, "lat": 52.13}),
    "goslar-clausthal": ({"lon": 10.4296, "lat": 51.9060}, {"lon": 10.34, "lat": 51.805}),
    "wolfenbuettel-lehre": ({"lon": 10.5361, "lat": 52.1688}, {"lon": 10.66, "lat": 52.32}),
    "schoeppenstedt-elm": ({"lon": 10.765, "lat": 52.145}, {"lon": 10.90, "lat": 52.22}),
}
BIKE_TYPES = ["road", "gravel", "touring", "mountain", "city", "ebike", "commuter", "recumbent"]
TRAFFIC_ROADS = {
    "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link",
}  # fmt: skip
PROTECTED = ("lane", "track", "shared_busway", "opposite_lane", "opposite_track", "separate")


def _tags(raw: str) -> dict[str, str]:
    return dict(kv.split("=", 1) for kv in raw.split() if "=" in kv)


def route_metrics(base_url: str, profile: str, origin: dict, dest: dict) -> dict[str, Any]:
    lonlats = f"{origin['lon']},{origin['lat']}|{dest['lon']},{dest['lat']}"
    query = urllib.parse.urlencode(
        {"lonlats": lonlats, "profile": profile, "alternativeidx": 0, "format": "geojson"}
    )
    with urllib.request.urlopen(f"{base_url}/brouter?{query}", timeout=180) as response:
        props = json.load(response)["features"][0]["properties"]
    surfaces: dict[str, float] = collections.defaultdict(float)
    roads: dict[str, float] = collections.defaultdict(float)
    unprotected = 0.0
    total = 0.0
    for row in props["messages"][1:]:  # row 0 is the header
        length, tags = float(row[3]), _tags(row[9])
        total += length
        highway = tags.get("highway", "?")
        roads[highway] += length
        raw = tags.get("surface")
        surfaces[SURFACE_TAXONOMY.get(raw.split(":")[0], "unknown") if raw else "unknown"] += length
        protected = any(
            tags.get(k) in PROTECTED
            for k in ("cycleway", "cycleway:right", "cycleway:left", "cycleway:both")
        )
        if highway in TRAFFIC_ROADS and not protected and tags.get("bicycle") != "designated":
            unprotected += length
    pct = lambda n: round(100 * n / total) if total else 0  # noqa: E731
    return {
        "km": round(float(props["track-length"]) / 1000, 2),
        "min": round(float(props["total-time"]) / 60, 1),
        "ascent_m": float(props["filtered ascend"]),
        "traffic_pct": pct(unprotected),
        "paved_pct": pct(surfaces["paved"]),
        "compacted_pct": pct(surfaces["compacted"]),
        "loose_pct": pct(surfaces["loose"] + surfaces["natural_soft"]),
        "cobbles_pct": pct(surfaces["masonry"]),
        "unknown_pct": pct(surfaces["unknown"]),
        "track_path_pct": pct(roads["track"] + roads["path"]),
    }


def header() -> str:
    cols = ("km", "min", "climb", "traf%", "pave", "cmp", "loose", "cobl", "unk", "trk+pth")
    return f"{'':10}{'profile':28}" + "".join(f"{c:>8}" for c in cols)


def row(label: str, profile: str, m: dict[str, Any], explicit: list[str] | None) -> str:
    values = (
        m["km"], m["min"], f"{m['ascent_m']:.0f}", m["traffic_pct"], m["paved_pct"],
        m["compacted_pct"], m["loose_pct"], m["cobbles_pct"], m["unknown_pct"], m["track_path_pct"],
    )  # fmt: skip
    first = f"{label:10}{'' if explicit else profile:28}"
    return first + "".join(f"{v:>8}" for v in values)


def routes() -> dict[str, tuple[dict, dict]]:
    cases = json.loads(BENCHMARK.read_text())["cases"][:5]
    found = {c["id"]: (c["request"]["origin"], c["request"]["destination"]) for c in cases}
    return {**found, **EXTRA_ROUTES}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--profile", nargs="*", help="BRouter profile names (default: per bike type)"
    )
    parser.add_argument("--base-url", default=Settings().brouter_base_url)
    parser.add_argument("--out", type=Path, help="write the results as JSON")
    options = parser.parse_args()

    targets = (
        [(p, p) for p in options.profile]
        if options.profile
        else [(bt, BROUTER_PROFILE_MAP[bt]) for bt in BIKE_TYPES]
    )
    results: dict[str, dict[str, Any]] = {}
    for name, (origin, dest) in routes().items():
        results[name] = {}
        print(f"\n== {name}")
        print(header())
        for label, profile in targets:
            m = route_metrics(options.base_url, profile, origin, dest)
            results[name][label] = {"profile": profile, **m}
            print(row(profile if options.profile else label, profile, m, options.profile))
    if options.out:
        options.out.write_text(json.dumps(results, indent=1))
        print(f"\nwrote {options.out}")


if __name__ == "__main__":
    main()
