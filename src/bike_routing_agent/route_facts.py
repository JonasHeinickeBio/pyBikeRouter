"""Traffic and surface of a route, as short facts (chat replies, CLI output).

Both come straight from ``RouteMetrics`` and nothing is added:

- *traffic* is ``main_road_share``: the share of the route on trunk/primary/secondary/tertiary
  roads without a mapped cycle lane or track -- a proxy for exposure to motor traffic, not a
  measurement of it, and ``None`` when the engine gave no tags;
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


def traffic_text(metrics: dict[str, Any]) -> str | None:
    """ "19 % on main roads without a bike lane"; ``None`` when the engine gave no tags."""
    share = metrics.get("main_road_share")
    if share is None:
        return None
    return f"{_pct(share)} on main roads without a bike lane"


def facts_line(metrics: dict[str, Any]) -> str:
    """One sentence for a route's reply: surface and traffic, or that there is no data."""
    surface, traffic = surface_text(metrics), traffic_text(metrics)
    parts = [
        f"Surface: {surface}." if surface else "Surface: the engine gave no surface data.",
        f"Traffic: {traffic}." if traffic else "Traffic: the engine gave no road data.",
    ]
    return " ".join(parts)


def facts_short(metrics: dict[str, Any]) -> str:
    """For one line per candidate: "19 % main roads, 71 % paved"; empty without data."""
    bits: list[str] = []
    share = metrics.get("main_road_share")
    if share is not None:
        bits.append(f"{_pct(share)} main roads")
    paved = surface_shares(metrics).get("paved")
    if paved is not None:
        bits.append(f"{_pct(paved)} paved")
    return ", ".join(bits)
