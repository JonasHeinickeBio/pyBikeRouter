"""Traffic and surface of a route, as short facts (chat replies, CLI output).

Both come straight from ``RouteMetrics`` and nothing is added:

- *traffic* is ``traffic_shares``: length shares in four bands (busy roads / some traffic / quiet
  streets / paths and tracks) estimated from road class, cycle infrastructure, speed limit and
  access -- an estimate from what is mapped, not a count of cars; without it the older
  ``main_road_share`` (main roads without a bike lane) is used; ``None`` when the engine gave no
  tags;
- *surface* is ``engine_surface_shares`` (BRouter's OSM tags: paved / unpaved / cobbles /
  unknown); without it the Overpass enrichment (``surface_coverage``) is used. "Unknown" is
  reported as unknown and never counted as paved.

The web form's ``frontend/facts.js`` words the same facts the same way.
"""

from __future__ import annotations

from typing import Any

SURFACE_LABELS = {
    "paved": "paved",
    "unpaved": "unpaved",
    "cobbles": "cobbles",
    "unknown": "unknown",
}


def _pct(share: float) -> str:
    return f"{round(share * 100)} %"


def surface_shares(metrics: dict[str, Any]) -> dict[str, float]:
    """``{surface: share}`` (largest first), from the engine's tags or else the enrichment."""
    shares = dict(metrics.get("engine_surface_shares") or {})
    if not shares:
        shares = dict(metrics.get("surface_coverage") or {})
        unknown = metrics.get("unknown_surface_fraction")
        if shares and unknown:
            shares["unknown"] = unknown
    return dict(sorted(((k, v) for k, v in shares.items() if v and v > 0), key=lambda kv: -kv[1]))


def surface_text(metrics: dict[str, Any]) -> str | None:
    """ "71 % paved, 24 % unpaved, 5 % unknown"; ``None`` when there is no surface data."""
    shares = surface_shares(metrics)
    if not shares:
        return None
    return ", ".join(f"{_pct(v)} {SURFACE_LABELS.get(k, k)}" for k, v in shares.items())


TRAFFIC_LABELS = {
    "busy": "busy roads",
    "some": "some traffic",
    "quiet": "quiet streets",
    "off_road": "paths and tracks",
}
_MIN_SHOWN = 0.005  # a band under half a percent would read as "0 %"


def traffic_text(metrics: dict[str, Any]) -> str | None:
    """ "5 % busy roads, 12 % some traffic, 30 % quiet streets, 53 % paths and tracks" (an
    estimate); without the finer estimate "19 % on main roads without a bike lane"; ``None``
    when the engine gave no tags."""
    shares = metrics.get("traffic_shares") or {}
    if shares:
        shown = [
            f"{_pct(shares[band])} {label}"
            for band, label in TRAFFIC_LABELS.items()
            if shares.get(band, 0) >= _MIN_SHOWN
        ]
        if shown:
            return ", ".join(shown)
    share = metrics.get("main_road_share")
    if share is None:
        return None
    return f"{_pct(share)} on main roads without a bike lane"


def busy_share(metrics: dict[str, Any]) -> float | None:
    """The share of busy roads: the finer estimate when there is one, else the main-road share."""
    shares = metrics.get("traffic_shares") or {}
    if shares:
        return float(shares.get("busy", 0.0))
    share = metrics.get("main_road_share")
    return None if share is None else float(share)


def facts_line(metrics: dict[str, Any]) -> str:
    """One sentence for a route's reply: surface and traffic, or that there is no data."""
    surface, traffic = surface_text(metrics), traffic_text(metrics)
    estimated = bool(metrics.get("traffic_shares"))
    parts = [
        f"Surface: {surface}." if surface else "Surface: the engine gave no surface data.",
        (f"Traffic{' (estimate)' if estimated else ''}: {traffic}.")
        if traffic
        else "Traffic: the engine gave no road data.",
    ]
    return " ".join(parts)


def facts_short(metrics: dict[str, Any]) -> str:
    """For one line per candidate: "5 % busy roads, 71 % paved"; empty without data."""
    bits: list[str] = []
    share = busy_share(metrics)
    if share is not None:
        label = "busy roads" if metrics.get("traffic_shares") else "main roads"
        bits.append(f"{_pct(share)} {label}")
    paved = surface_shares(metrics).get("paved")
    if paved is not None:
        bits.append(f"{_pct(paved)} paved")
    return ", ".join(bits)
