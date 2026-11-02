# ruff: noqa: E501  (markdown table rows are kept on one line each)
"""Route ten routes through every BRouter profile and score what comes back.

Two steps, so the (slow) routing and the (instant) scoring can be re-run apart:

    python scripts/profile_experiment.py fetch   --out DIR       # third-party profiles, pinned
    python scripts/profile_experiment.py prepare --upstream DIR --image DIR --ours DIR \\
        --community DIR --out DIR
    python scripts/profile_experiment.py run   --out benchmarks/profile-experiment-v1/raw.json
    python scripts/profile_experiment.py score --raw benchmarks/profile-experiment-v1/raw.json
    python scripts/profile_experiment.py score --raw ... --groups cycling,ours,community

BRouter only returns the OSM tags a profile itself reads, so a profile that ignores
``cycleway:left`` would look as if it routed past cycle tracks it never saw. ``prepare``
therefore writes a copy of every profile with ``processUnusedTags = true`` (costs, and so
the routes, are unchanged: ``same-routes`` checks that) into ``--out``, which is mounted
as the custom-profile directory of a BRouter on the project's segments; ``run`` asks that
server (``--url``) for ``custom_t_<name>`` (``custom_t_<name>_image`` for the stock profile
as the container image ships it). Method, scoring rubric and findings:
docs/profile-experiment.md. The rubric below was fixed before the first run.

Everything is read from the OSM tags BRouter returns for each segment it chose, like
scripts/check_profiles.py. Proxies, not truth: nobody rode these routes.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import re
import socket
import statistics
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from bike_routing_agent.config import BROUTER_PROFILE_MAP, SURFACE_TAXONOMY

# (lon, lat) origin, destination. Spread over the segments the local BRouter has: city,
# flat farmland, river valley, a big climb, alpine valley, city-to-lake, forest hills,
# inter-city, a UK railway path and the Harz climb of docs/profile-evaluation.md.
ROUTES: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {
    "berlin-commute": ((13.3696, 52.5251), (13.4030, 52.4730)),
    "muenster-warendorf": ((7.6340, 51.9560), (8.0000, 51.9553)),
    "koeln-bonn": ((6.9640, 50.9380), (7.0982, 50.7343)),
    "freiburg-schauinsland": ((7.8420, 47.9950), (7.9069, 47.9143)),
    "garmisch-mittenwald": ((11.0949, 47.4921), (11.2611, 47.4400)),
    "muenchen-starnberg": ((11.5754, 48.1374), (11.3430, 48.0000)),
    "eisenach-brotterode": ((10.3198, 50.9740), (10.4430, 50.8600)),
    "leipzig-halle": ((12.3731, 51.3397), (11.9696, 51.4825)),
    "bath-bristol": ((-2.3590, 51.3811), (-2.5879, 51.4545)),
    "harz-climb": ((10.5555, 51.8883), (10.5816, 51.7269)),
}

# Upstream misc/profiles2 (the "stock" profiles) by what they are made for.
CYCLING = [
    "trekking", "fastbike", "fastbike-verylowtraffic", "gravel", "mtb",
    "vm-forum-liegerad-schnell", "vm-forum-velomobil-schnell",
]  # fmt: skip
# Not cycling profiles: "shortest" is the distance-only baseline, the rest are for other
# modes. They run through the same rubric as a reference and are left out of the ranking.
OTHER = [
    "shortest", "all", "dummy", "softaccess", "skating", "car-vario", "car-vario-nocost",
    "moped", "rail", "river", "hiking-mountain",
]  # fmt: skip
OURS = ["touring-v1", "commuter-v1", "gravel-v1", "gravel-v2"]  # docker/brouter/profiles
# Third-party bicycle profiles from GitHub, pinned by commit and sha256 (no licence, so not
# stored here): label, repo, sha, path.
MANIFEST = (
    Path(__file__).resolve().parents[1] / "benchmarks/profile-experiment-v1/community-sources.json"
)


def community() -> list[dict[str, str]]:
    return json.loads(MANIFEST.read_text())["profiles"] if MANIFEST.exists() else []


# Stock profiles whose text differs between the image and upstream master (checked by md5).
DRIFTED = [
    "fastbike", "fastbike-verylowtraffic", "gravel", "mtb", "trekking", "shortest",
    "vm-forum-liegerad-schnell", "vm-forum-velomobil-schnell",
]  # fmt: skip

TRAFFIC_ROADS = {
    "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link",
}  # fmt: skip
PROTECTED = ("lane", "track", "shared_busway", "opposite_lane", "opposite_track", "separate")
BIKE_OK = ("yes", "designated", "permissive")

# ---- rubric (fixed before the first run) -------------------------------------------------
# Each component is 0..1; a route that could not be found scores 0 on every persona.
#   D directness  1 at the shortest cycling route, 0 at +50 % length
#   T time        1 at the fastest cycling route (BRouter's own estimate), 0 at +100 %
#   S safety      1 - (unprotected main-road share + 3 x share where cycling is forbidden)
#   C climbing    1 at the least ascent of any cycling route, 0 at that + (itself + 50 m)
#   P smoothness  surface-weighted: paved 1, compacted .7, cobbles .3, loose .2, unknown .5
#   G gravel fit  (compacted + half of loose) share, 1 from 50 %
#   O off-road    share of track + path ways, 1 from 60 %
# Weights per persona (= bike type), each row sums to 1.
PERSONAS: dict[str, dict[str, float]] = {
    "road": {"T": 0.30, "S": 0.30, "P": 0.30, "C": 0.10},
    "ebike": {"S": 0.35, "T": 0.25, "P": 0.20, "D": 0.20},
    "commuter": {"S": 0.40, "T": 0.30, "P": 0.20, "D": 0.10},
    "city": {"S": 0.40, "P": 0.25, "D": 0.25, "T": 0.10},
    "touring": {"S": 0.35, "P": 0.25, "D": 0.15, "C": 0.25},
    "gravel": {"G": 0.35, "S": 0.25, "D": 0.20, "C": 0.20},
    "mountain": {"O": 0.40, "G": 0.15, "S": 0.25, "D": 0.20},
    "recumbent": {"P": 0.35, "S": 0.30, "C": 0.20, "T": 0.15},
}
SENSITIVITY_DRAWS = 2000
SENSITIVITY_SPREAD = (0.5, 1.5)  # each weight scaled by U(0.5, 1.5), then renormalised


def _tags(raw: str) -> dict[str, str]:
    return dict(kv.split("=", 1) for kv in raw.split() if "=" in kv)


def route_metrics(base_url: str, profile: str, origin: tuple, dest: tuple) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "lonlats": f"{origin[0]},{origin[1]}|{dest[0]},{dest[1]}",
            "profile": profile,
            "alternativeidx": 0,
            "format": "geojson",
        }
    )
    try:
        with urllib.request.urlopen(f"{base_url}/brouter?{query}", timeout=300) as response:
            props = json.load(response)["features"][0]["properties"]
    except urllib.error.HTTPError as exc:
        return {"error": exc.read().decode(errors="replace").strip()[:120] or str(exc)}
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:120]}
    surfaces: dict[str, float] = collections.defaultdict(float)
    roads: dict[str, float] = collections.defaultdict(float)
    unprotected = forbidden = ferry = total = 0.0
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
        bicycle = tags.get("bicycle")
        if (
            highway in ("motorway", "motorway_link", "steps")
            or bicycle in ("no", "dismount")
            or (highway in ("footway", "pedestrian") and bicycle not in BIKE_OK)
        ):
            forbidden += length
        if tags.get("route") == "ferry":
            ferry += length
    pct = lambda n: round(100 * n / total, 1) if total else 0.0  # noqa: E731
    return {
        "km": round(float(props["track-length"]) / 1000, 3),
        "min": round(float(props["total-time"]) / 60, 1),
        "ascent_m": float(props["filtered ascend"]),
        "traffic_pct": pct(unprotected),
        "forbidden_pct": pct(forbidden),
        "ferry_pct": pct(ferry),
        "paved_pct": pct(surfaces["paved"]),
        "compacted_pct": pct(surfaces["compacted"]),
        "loose_pct": pct(surfaces["loose"] + surfaces["natural_soft"]),
        "cobbles_pct": pct(surfaces["masonry"]),
        "unknown_pct": pct(surfaces["unknown"]),
        "track_path_pct": pct(roads["track"] + roads["path"]),
    }


# ---- run -------------------------------------------------------------------------------
def targets(url: str) -> dict[str, dict[str, str]]:
    """label -> how to ask the experiment BRouter, for every profile of the experiment."""
    found: dict[str, dict[str, str]] = {}
    names_by_group = (
        ("cycling", CYCLING),
        ("other", OTHER),
        ("ours", OURS),
        ("community", [row["label"] for row in community()]),
    )
    for group, names in names_by_group:
        for name in names:
            found[name] = {"group": group, "url": url, "profile": f"custom_t_{name}"}
    for name in DRIFTED:  # the same stock profile as the container image still ships it
        found[f"{name}@image"] = {"group": "image", "url": url, "profile": f"custom_t_{name}_image"}
    return found


_UNUSED_TAGS = re.compile(r"^\s*assign\s+processUnusedTags\b.*$", re.MULTILINE)
_GLOBAL = re.compile(r"^\s*---context:global.*$", re.MULTILINE)


def with_all_tags(text: str) -> str:
    """The profile with ``processUnusedTags = true``; nothing else changes."""
    line = "assign processUnusedTags = true  # profile_experiment.py: report every tag"
    if _UNUSED_TAGS.search(text):
        return _UNUSED_TAGS.sub(line, text)
    return _GLOBAL.sub(lambda m: f"{m.group(0)}\n{line}", text, count=1)


def prepare(options: argparse.Namespace) -> None:
    options.out.mkdir(parents=True, exist_ok=True)
    jobs = [(options.upstream / f"{n}.brf", f"t_{n}") for n in CYCLING + OTHER]
    jobs += [(options.ours / f"{n}.brf", f"t_{n}") for n in OURS]
    jobs += [(options.image / f"{n}.brf", f"t_{n}_image") for n in DRIFTED]
    if options.community:
        jobs += [
            (options.community / f"{row['label']}.brf", f"t_{row['label']}") for row in community()
        ]
    for source, name in jobs:
        text = source.read_text(errors="replace")
        tagged = with_all_tags(text)
        if "processUnusedTags = true" not in tagged:
            print(f"skipped {source.name}: no global context to put processUnusedTags into")
            continue
        (options.out / f"{name}.brf").write_text(tagged)
    print(f"wrote {len(list(options.out.glob('*.brf')))} profiles to {options.out}")


def fetch(options: argparse.Namespace) -> None:
    """Download the third-party profiles at their pinned commits and check the hashes."""
    options.out.mkdir(parents=True, exist_ok=True)
    bad = 0
    # raw.githubusercontent.com over IPv6 is slow or stalls on some networks: use IPv4 here
    real_getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = (
        lambda host, *rest, **kw: real_getaddrinfo(  # type: ignore[assignment]
            host, rest[0] if rest else None, socket.AF_INET, *rest[2:], **kw
        )
    )
    for row in community():
        url = f"https://raw.githubusercontent.com/{row['repo']}/{row['sha']}/{urllib.parse.quote(row['path'])}"
        data = b""
        for _attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=15) as response:
                    data = response.read()
                break
            except (urllib.error.URLError, TimeoutError):
                continue
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            bad += 1
            print(f"HASH MISMATCH {row['label']} ({url})")
            continue
        (options.out / f"{row['label']}.brf").write_bytes(data)
    socket.getaddrinfo = real_getaddrinfo
    print(f"fetched {len(community()) - bad} of {len(community())} profiles to {options.out}")


def same_routes(options: argparse.Namespace) -> None:
    """Did adding the tags change a single route? Compare two raw files label by label."""
    a, b = (json.loads(path.read_text())["results"] for path in (options.a, options.b))
    same = changed = 0
    for route, row in a.items():
        for label, m in row.items():
            other = b[route].get(label)
            if other is None or "error" in m or "error" in other:
                continue
            if (m["km"], m["min"], m["ascent_m"]) == (other["km"], other["min"], other["ascent_m"]):
                same += 1
            else:
                changed += 1
                print(f"changed: {route} {label}: {m['km']} -> {other['km']} km")
    print(f"{same} routes identical in km, minutes and ascent, {changed} changed")


def run(options: argparse.Namespace) -> None:
    raw: dict[str, Any] = json.loads(options.out.read_text()) if options.out.exists() else {}
    wanted = targets(options.url)
    raw["targets"] = wanted
    raw.setdefault(
        "routes", {name: {"origin": o, "destination": d} for name, (o, d) in ROUTES.items()}
    )
    results = raw.setdefault("results", {})
    for route, (origin, dest) in ROUTES.items():
        done = results.setdefault(route, {})
        for label, target in wanted.items():
            if label in done and "error" not in done[label]:
                continue  # recorded errors are retried: the server may have recovered
            done[label] = route_metrics(target["url"], target["profile"], origin, dest)
            shown = done[label].get("error") or f"{done[label]['km']} km, {done[label]['min']} min"
            print(f"{route:24}{label:34}{shown}", flush=True)
        options.out.write_text(json.dumps(raw, indent=1))
    print(f"wrote {options.out}")


# ---- score -----------------------------------------------------------------------------
def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def components(m: dict[str, Any], ref: dict[str, float]) -> dict[str, float]:
    smooth = (
        m["paved_pct"] + 0.7 * m["compacted_pct"] + 0.3 * m["cobbles_pct"]
        + 0.2 * m["loose_pct"] + 0.5 * m["unknown_pct"]
    )  # fmt: skip
    return {
        "D": clamp(1 - (m["km"] / ref["km"] - 1) / 0.5),
        "T": clamp(1 - (m["min"] / ref["min"] - 1) / 1.0),
        "S": clamp(1 - (m["traffic_pct"] + 3 * m["forbidden_pct"]) / 100),
        "C": clamp(1 - (m["ascent_m"] - ref["ascent_m"]) / (ref["ascent_m"] + 50)),
        "P": clamp(smooth / 100),
        "G": clamp((m["compacted_pct"] + 0.5 * m["loose_pct"]) / 50),
        "O": clamp(m["track_path_pct"] / 60),
    }


def persona_score(comp: dict[str, float], weights: dict[str, float]) -> float:
    return 100 * sum(w * comp[k] for k, w in weights.items())


def references(results: dict[str, Any], labels: list[str]) -> dict[str, dict[str, float]]:
    """Per route: the least km / minutes / ascent of any cycling profile that found a route."""
    refs = {}
    for route, row in results.items():
        ok = [row[lab] for lab in labels if lab in row and "error" not in row[lab]]
        refs[route] = {
            "km": min(m["km"] for m in ok),
            "min": min(m["min"] for m in ok),
            "ascent_m": min(m["ascent_m"] for m in ok),
        }
    return refs


def score_all(raw: dict[str, Any]) -> dict[str, Any]:
    results, wanted = raw["results"], raw["targets"]
    ranked = [lab for lab, t in wanted.items() if t["group"] in ("cycling", "ours")]
    refs = references(results, ranked)
    table: dict[str, dict[str, Any]] = {}
    for label in wanted:
        per_route = {}
        for route, row in results.items():
            m = row[label]
            if "error" in m:
                per_route[route] = {"failed": m["error"], "scores": dict.fromkeys(PERSONAS, 0.0)}
                continue
            comp = components(m, refs[route])
            scores = {p: persona_score(comp, w) for p, w in PERSONAS.items()}
            per_route[route] = {"comp": comp, "scores": scores, "m": m}
        table[label] = per_route
    return {"refs": refs, "table": table}


def mean_scores(per_route: dict[str, Any]) -> dict[str, float]:
    return {p: statistics.fmean(r["scores"][p] for r in per_route.values()) for p in PERSONAS}


def sensitivity(scored: dict[str, Any], pool: list[str]) -> dict[str, dict[str, dict[str, float]]]:
    """Share of random weight draws in which each profile is first / in the top three."""
    rng = random.Random(7)
    wins = {p: collections.Counter() for p in PERSONAS}
    top3 = {p: collections.Counter() for p in PERSONAS}
    for _ in range(SENSITIVITY_DRAWS):
        for persona, weights in PERSONAS.items():
            drawn = {k: w * rng.uniform(*SENSITIVITY_SPREAD) for k, w in weights.items()}
            norm = sum(drawn.values())
            drawn = {k: v / norm for k, v in drawn.items()}
            means = {}
            for lab in pool:
                per_route = scored["table"][lab]
                means[lab] = statistics.fmean(
                    0.0 if "failed" in r else persona_score(r["comp"], drawn)
                    for r in per_route.values()
                )
            order = sorted(pool, key=lambda lab: -means[lab])
            wins[persona][order[0]] += 1
            top3[persona].update(order[:3])
    return {
        p: {
            lab: {
                "first": wins[p][lab] / SENSITIVITY_DRAWS,
                "top3": top3[p][lab] / SENSITIVITY_DRAWS,
            }
            for lab in pool
        }
        for p in PERSONAS
    }


def render(raw: dict[str, Any], groups: set[str]) -> str:  # noqa: C901 - one flat report
    scored = score_all(raw)  # references always come from the cycling + ours profiles
    table = scored["table"]
    wanted = {
        lab: t for lab, t in raw["targets"].items() if t["group"] in groups | {"other", "image"}
    }
    pool = [lab for lab, t in wanted.items() if t["group"] in groups]
    mapped = {bt: BROUTER_PROFILE_MAP[bt].removeprefix("custom_") for bt in PERSONAS}
    sens = sensitivity(scored, pool)
    out: list[str] = []

    def mean_of(lab: str, key: str) -> float:
        rows = [r["m"][key] for r in table[lab].values() if "m" in r]
        return statistics.fmean(rows) if rows else float("nan")

    out.append("## Mean score per persona (0-100, failed route = 0)\n")
    out.append("| profile | group | ok | " + " | ".join(PERSONAS) + " | mean | worst route score |")
    out.append("|---|---|---:|" + "---:|" * (len(PERSONAS) + 2))
    order = sorted(
        (lab for lab in wanted if wanted[lab]["group"] != "image"),
        key=lambda lab: (
            wanted[lab]["group"] == "other",
            -statistics.fmean(mean_scores(table[lab]).values()),
        ),
    )
    for lab in order:
        means = mean_scores(table[lab])
        ok = sum("m" in r for r in table[lab].values())
        overall = statistics.fmean(means.values())
        worst = min(statistics.fmean(r["scores"].values()) for r in table[lab].values())
        cells = " | ".join(f"{means[p]:.0f}" for p in PERSONAS)
        out.append(
            f"| {lab} | {wanted[lab]['group']} | {ok}/{len(ROUTES)} | {cells} | "
            f"**{overall:.0f}** | {worst:.0f} |"
        )

    out.append("\n## What each bike type uses today, against the best profile\n")
    out.append(
        "The mapped profile is scored as the service runs it (stock profiles from the container "
        f"image, `@image`); rank is its place among the {len(pool)} ranked profiles.\n"
    )
    out.append(
        "| bike type | mapped profile | its score | rank | best | best score | gap | best is #1 in weight draws |"
    )
    out.append("|---|---|---:|---:|---|---:|---:|---:|")
    for persona in PERSONAS:
        means = {lab: mean_scores(table[lab])[persona] for lab in pool}
        best = max(means, key=means.get)
        use = mapped[persona]
        served = f"{use}@image" if f"{use}@image" in table else use
        served_score = mean_scores(table[served])[persona]
        rank = 1 + sum(score > served_score for score in means.values())
        out.append(
            f"| {persona} | {served} | {served_score:.1f} | {rank} | {best} | "
            f"{means[best]:.1f} | {means[best] - served_score:+.1f} | {sens[persona][best]['first']:.0%} |"
        )

    out.append("\n## Top three per persona (cycling profiles, with how often they stay there)\n")
    out.append("| persona | 1st | 2nd | 3rd |")
    out.append("|---|---|---|---|")
    for persona in PERSONAS:
        means = {lab: mean_scores(table[lab])[persona] for lab in pool}
        top = sorted(means, key=lambda lab: -means[lab])[:3]
        cells = [f"{lab} {means[lab]:.0f} ({sens[persona][lab]['top3']:.0%} top-3)" for lab in top]
        out.append(f"| {persona} | " + " | ".join(cells) + " |")

    out.append("\n## Routes won per persona (of 10; ties go to the profile listed first)\n")
    out.append("| persona | wins |")
    out.append("|---|---|")
    for persona in PERSONAS:
        won: collections.Counter[str] = collections.Counter()
        for route in ROUTES:
            won[max(pool, key=lambda lab: table[lab][route]["scores"][persona])] += 1
        out.append(
            f"| {persona} | " + ", ".join(f"{lab} {n}" for lab, n in won.most_common()) + " |"
        )

    out.append("\n## Components, mean over the routes (0-100; the weights are not involved)\n")
    out.append(
        "| profile | D direct | T time | S safe | C climb | P smooth | G gravel | O off-road |"
    )
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for lab in order:
        if wanted[lab]["group"] == "other":
            continue
        cells = " | ".join(
            f"{100 * statistics.fmean(0.0 if 'failed' in r else r['comp'][k] for r in table[lab].values()):.0f}"
            for k in "DTSCPGO"
        )
        out.append(f"| {lab} | {cells} |")

    out.append("\n## Post-hoc: the same ranking with components removed\n")
    out.append(
        "Not part of the rubric fixed before the run. S is the traffic proxy; T compares each "
        "profile's own speed model (a velomobil profile assumes a faster vehicle), so it is the "
        "weakest component. The remaining weights are scaled up to sum to 1.\n"
    )
    out.append("| persona | without | 1st | 2nd | 3rd |")
    out.append("|---|---|---|---|---|")
    for persona, weights in PERSONAS.items():
        for dropped, name in (({"S"}, "S"), ({"T"}, "T"), ({"S", "T"}, "S and T")):
            rest = {k: w for k, w in weights.items() if k not in dropped}
            if len(rest) == len(weights) or not rest:
                continue  # nothing to remove from this persona
            norm = sum(rest.values())
            rest = {k: w / norm for k, w in rest.items()}
            means = {
                lab: statistics.fmean(
                    0.0 if "failed" in r else persona_score(r["comp"], rest)
                    for r in table[lab].values()
                )
                for lab in pool
            }
            top = sorted(means, key=lambda lab: -means[lab])[:3]
            cells = " | ".join(f"{lab} {means[lab]:.0f}" for lab in top)
            out.append(f"| {persona} | {name} | {cells} |")

    out.append("\n## What the profiles do (mean over the routes that were found)\n")
    out.append(
        "| profile | km vs shortest cycling | traffic % | forbidden % | paved | compacted | loose | cobbles | unknown | track+path | climb m |"
    )
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for lab in order:
        detours = [
            100 * (r["m"]["km"] / scored["refs"][route]["km"] - 1)
            for route, r in table[lab].items()
            if "m" in r
        ]
        if not detours:
            out.append(f"| {lab} | no route on any of the {len(ROUTES)} | | | | | | | | | |")
            continue
        cols = ["traffic_pct", "forbidden_pct", "paved_pct", "compacted_pct", "loose_pct",
                "cobbles_pct", "unknown_pct", "track_path_pct", "ascent_m"]  # fmt: skip
        values = " | ".join(f"{mean_of(lab, key):.0f}" for key in cols)
        out.append(f"| {lab} | {statistics.fmean(detours):+.0f} % | {values} |")

    out.append("\n## Stock profiles: container image vs upstream master\n")
    out.append(
        "| profile | mean score image | mean score master | change | km image | km master | traffic % image | master | routes whose length differs (>5 m) |"
    )
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name in DRIFTED:
        a, b = table[f"{name}@image"], table[name]
        sa, sb = (
            statistics.fmean(mean_scores(a).values()),
            statistics.fmean(mean_scores(b).values()),
        )
        differs = sum(
            1 for route in ROUTES
            if "m" in a[route] and "m" in b[route] and abs(a[route]["m"]["km"] - b[route]["m"]["km"]) > 0.005
        )  # fmt: skip
        out.append(
            f"| {name} | {sa:.1f} | {sb:.1f} | {sb - sa:+.1f} | {mean_of(f'{name}@image', 'km'):.2f} | "
            f"{mean_of(name, 'km'):.2f} | {mean_of(f'{name}@image', 'traffic_pct'):.0f} | "
            f"{mean_of(name, 'traffic_pct'):.0f} | {differs}/{len(ROUTES)} |"
        )

    out.append("\n## Failures\n")
    failures = [
        f"- {route} / {lab}: {r['failed']}"
        for lab in wanted
        for route, r in table[lab].items()
        if "failed" in r
    ]
    out.extend(failures or ["none"])

    out.append(
        "\n## Per route (km, min, climb m, traffic %, forbidden %, paved/compacted/loose/cobbles/unknown %)\n"
    )
    for route in ROUTES:
        ref = scored["refs"][route]
        out.append(
            f"### {route}  (shortest cycling {ref['km']:.2f} km, least climb {ref['ascent_m']:.0f} m)\n"
        )
        out.append(
            "| profile | km | min | climb | traffic | forbidden | paved | cmp | loose | cobbl | unk | mean score |"
        )
        out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for lab in order:
            r = table[lab][route]
            if "failed" in r:
                out.append(f"| {lab} | {r['failed']} | | | | | | | | | | 0 |")
                continue
            m = r["m"]
            out.append(
                f"| {lab} | {m['km']:.2f} | {m['min']:.0f} | {m['ascent_m']:.0f} | {m['traffic_pct']:.0f} | "
                f"{m['forbidden_pct']:.0f} | {m['paved_pct']:.0f} | {m['compacted_pct']:.0f} | "
                f"{m['loose_pct']:.0f} | {m['cobbles_pct']:.0f} | {m['unknown_pct']:.0f} | "
                f"{statistics.fmean(r['scores'].values()):.0f} |"
            )
        out.append("")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="route everything, resumable")
    run_p.add_argument("--out", type=Path, required=True)
    run_p.add_argument("--url", default="http://127.0.0.1:17778")
    prep_p = sub.add_parser("prepare", help="write the all-tags copy of every profile")
    for name in ("upstream", "image", "ours", "out"):
        prep_p.add_argument(f"--{name}", type=Path, required=True)
    prep_p.add_argument("--community", type=Path, help="directory written by 'fetch'")
    fetch_p = sub.add_parser("fetch", help="download the pinned third-party profiles")
    fetch_p.add_argument("--out", type=Path, required=True)
    same_p = sub.add_parser("same-routes", help="compare the routes of two raw files")
    same_p.add_argument("--a", type=Path, required=True)
    same_p.add_argument("--b", type=Path, required=True)
    score_p = sub.add_parser("score", help="score a raw result file and print markdown")
    score_p.add_argument("--raw", type=Path, required=True)
    score_p.add_argument(
        "--groups",
        default="cycling,ours",
        help="profile groups to rank (cycling, ours, community); the default is the first report",
    )
    options = parser.parse_args()
    if options.command == "run":
        run(options)
    elif options.command == "prepare":
        prepare(options)
    elif options.command == "fetch":
        fetch(options)
    elif options.command == "same-routes":
        same_routes(options)
    else:
        print(render(json.loads(options.raw.read_text()), set(options.groups.split(","))))


if __name__ == "__main__":
    main()
