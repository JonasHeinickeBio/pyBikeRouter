"""Plain-text rendering for the CLI (``--format text``): short, readable, facts only.

Everything here takes the same dictionaries the JSON output has, so a saved plan file can be
shown again later (``route show``) exactly as it was printed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any


def km(meters: float | None) -> str:
    return "-" if meters is None else f"{meters / 1000:.1f} km"


def minutes(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    total = round(seconds / 60)
    hours, rest = divmod(total, 60)
    return f"{hours} h {rest:02d} min" if hours else f"{rest} min"


def metres(value: float | None) -> str:
    return "-" if value is None else f"{round(value)} m"


def megabytes(size: int | None) -> str:
    return "size unknown" if not size else f"{size / 1e6:.0f} MB"


def table(rows: Sequence[Sequence[str]], headers: Sequence[str]) -> str:
    """A left-aligned text table."""
    widths = [max(len(str(c)) for c in col) for col in zip(headers, *rows, strict=False)]

    def line(cells: Iterable[str]) -> str:
        return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths, strict=False)).rstrip()

    return "\n".join([line(headers), line("-" * w for w in widths), *(line(r) for r in rows)])


def _candidate_line(candidate: dict[str, Any], selected: bool) -> str:
    m = candidate.get("metrics") or {}
    score = candidate.get("score")
    return (
        f"  {candidate.get('rank') or '-'}. {candidate.get('provider')}/"
        f"{candidate.get('provider_profile')}  {km(m.get('distance_m'))}  "
        f"{minutes(m.get('duration_s'))}  up {metres(m.get('ascent_m'))}"
        + (f"  score {score:.2f}" if isinstance(score, int | float) else "")
        + ("  <- selected" if selected else "")
    )


def _weather_lines(weather: dict[str, Any] | None) -> list[str]:
    summary = (weather or {}).get("summary") or {}
    if not summary:
        return []
    parts: list[str] = []
    low, high = summary.get("temperature_min_c"), summary.get("temperature_max_c")
    if low is not None and high is not None:
        parts.append(f"{low:.0f}..{high:.0f} C")
    if summary.get("wind_speed_mean_kmh") is not None:
        parts.append(f"wind {summary['wind_speed_mean_kmh']:.0f} km/h")
    if summary.get("headwind_mean_kmh") is not None:
        parts.append(f"headwind {summary['headwind_mean_kmh']:+.0f} km/h")
    if summary.get("precipitation_probability_max") is not None:
        parts.append(f"rain chance up to {summary['precipitation_probability_max']:.0f} %")
    stretch = summary.get("wet_stretch")
    if stretch and stretch.get("from_km") is not None and stretch.get("to_km") is not None:
        parts.append(f"wet km {stretch['from_km']:.0f}-{stretch['to_km']:.0f}")
    if summary.get("after_dark"):
        parts.append("part of the ride after dark")
    return [f"Weather: {', '.join(parts)}"] if parts else []


def clarification_text(groups: list[dict[str, Any]]) -> list[str]:
    lines = ["The places are ambiguous; pick one and run again with its lat,lon:"]
    for group in groups:
        lines.append(f'  "{group.get("field")}":')
        for c in group.get("candidates") or []:
            coord = c.get("coordinate") or {}
            lines.append(
                f"    {coord.get('lat'):.5f},{coord.get('lon'):.5f}  {c.get('label')}"
                if coord
                else f"    {c.get('label')}"
            )
        if not group.get("candidates"):
            lines.append(f"    {group.get('hint') or 'no matches'}")
    return lines


def plan_text(payload: dict[str, Any]) -> str:
    """A readable summary of a plan payload (the dictionary ``route plan`` prints as JSON)."""
    status = payload.get("status")
    lines: list[str] = []
    if status != "ready":
        lines.append(f"No route: {status}.")
        if status == "awaiting_clarification":
            lines.extend(clarification_text(payload.get("clarification") or []))
    else:
        route = payload.get("route") or {}
        m = route.get("metrics") or {}
        lines.append(
            f"Route ready: {route.get('provider')}/{route.get('provider_profile')}  "
            f"{km(m.get('distance_m'))}  {minutes(m.get('duration_s'))}  "
            f"up {metres(m.get('ascent_m'))} / down {metres(m.get('descent_m'))}"
        )
        if payload.get("explanation"):
            lines.append(str(payload["explanation"]))
        candidates = payload.get("candidates") or []
        if len(candidates) > 1:
            lines.append("")
            lines.append("Alternatives:")
            for c in candidates:
                lines.append(_candidate_line(c, c.get("rank") == route.get("rank")))
                lines.extend(f"       + {p}" for p in c.get("pros") or [])
                lines.extend(f"       - {p}" for p in c.get("cons") or [])
        weather = _weather_lines(route.get("weather"))
        if weather:
            lines.extend(["", *weather])
        stops = payload.get("poi_stops")
        if stops:
            lines.extend(["", "Stops (the best-known sights on the way):"])
            lines.extend(
                f"  {s.get('name') or s.get('category')}  (described in {s.get('fame')} languages)"
                for s in stops
            )
        if route.get("warnings"):
            lines.extend(["", "Warnings:", *(f"  {w}" for w in route["warnings"])])
        files = payload.get("artifacts") or {}
        if files:
            lines.extend(["", "Files:", *(f"  {k}: {v}" for k, v in files.items())])
    if payload.get("poi_stops_status") not in (None, "ok"):
        lines.append(f"Sight stops: {payload['poi_stops_status']}")
    errors = payload.get("errors") or []
    if errors:
        lines.extend(["", "Notes:"])
        lines.extend(f"  [{e.get('code')}] {e.get('message')}" for e in errors)
    if payload.get("plan_id"):
        lines.extend(["", f"Recorded as {payload['plan_id']}"])
    return "\n".join(lines)


def poi_text(
    pois: list[dict[str, Any]], *, truncated: bool = False, fame_status: str = "ok"
) -> str:
    if not pois:
        return "No points of interest of these kinds here."
    rows = [
        [
            str(p.get("fame") if p.get("fame") is not None else "-"),
            str(p.get("category")),
            str(p.get("name") or "(unnamed)"),
            "-"
            if p.get("distance_from_route_m") is None
            else f"{p['distance_from_route_m']:.0f} m",
            "-" if p.get("along_route_km") is None else f"{p['along_route_km']:.1f} km",
            f"{p.get('lat'):.5f},{p.get('lon'):.5f}",
            str(p.get("id")),
        ]
        for p in pois
    ]
    text = table(rows, ["FAME", "KIND", "NAME", "OFF ROUTE", "ALONG", "LAT,LON", "OSM"])
    notes = []
    if fame_status in ("unavailable", "partial"):
        notes.append(f"fame lookup: {fame_status} (unranked where unknown)")
    if truncated:
        notes.append("the best-known per kind are shown; narrow the kinds or the buffer for more")
    return text + ("\n" + "\n".join(notes) if notes else "")


def poi_info_text(info: dict[str, Any]) -> str:
    lines = [str(info.get("title") or "(no title)")]
    if info.get("description"):
        lines.append(str(info["description"]))
    if info.get("extract"):
        lines.extend(["", str(info["extract"])])
    if info.get("thumbnail_url"):
        lines.extend(["", f"Image: {info['thumbnail_url']}"])
    links = info.get("links") or []
    if links:
        lines.extend(
            ["", "Read more:", *(f"  {link.get('label')}: {link.get('url')}" for link in links)]
        )
    if info.get("attribution"):
        lines.extend(["", *(str(a) for a in info["attribution"])])
    return "\n".join(lines)


def status_text(report: dict[str, Any], caps: dict[str, Any]) -> str:
    lines = [f"Ready: {'yes' if report.get('ready') else 'NO'}  ({report.get('status')})", ""]
    rows = [
        [
            str(c.get("name")),
            str(c.get("kind")),
            str(c.get("status")) + ("" if c.get("required") else " (optional)"),
            "-" if c.get("latency_ms") is None else f"{c['latency_ms']} ms",
            str(c.get("detail") or ""),
        ]
        for c in report.get("components") or []
    ]
    if rows:
        lines.append(table(rows, ["COMPONENT", "KIND", "STATUS", "LATENCY", "DETAIL"]))
    lines.extend(["", "Features:"])
    for key, value in caps.items():
        shown = ", ".join(value) if isinstance(value, list) else ("on" if value else "off")
        lines.append(f"  {key}: {shown}")
    return "\n".join(lines)
