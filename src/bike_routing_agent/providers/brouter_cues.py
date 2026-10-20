"""Turn-by-turn cues from BRouter's own turn instructions.

With ``timode`` set, BRouter adds a ``voicehints`` list to the route's properties. Each entry is
``[index, command, exit, distance_to_next_m, angle_deg]``: ``index`` is a vertex of the route's
geometry (not a row of ``messages``), ``command`` what to do there, ``exit`` the roundabout exit,
``angle_deg`` the turn (negative = left). The distance in the entry is to the *next* hint, so the
distance from the start is measured along the geometry instead.

Only the commands that tell a rider to do something are kept; "continue straight" (1) is the
absence of a cue, and "off route" / "beeline" (12, 15) and anything this table does not know are
dropped rather than guessed. The codes were read off real routes (left/right/slight/sharp, keep
left/right, a left and right u-turn, a roundabout with its exit). BRouter carries no street names,
so a cue says what to do and where, never "onto Hauptstrasse".
"""

from __future__ import annotations

import math
from typing import Any

from bike_routing_agent.models import RouteCue
from bike_routing_agent.poi.geo import cumulative_lengths_m

# BRouter's command codes -> the kind of cue.
_KINDS: dict[int, str] = {
    2: "left",
    3: "slight_left",
    4: "sharp_left",
    5: "right",
    6: "slight_right",
    7: "sharp_right",
    8: "keep_left",
    9: "keep_right",
    10: "uturn",
    11: "uturn",
    13: "roundabout",
    14: "roundabout",
}
MAX_EXIT = 12


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def cues_from(properties: dict[str, Any], coordinates: object) -> list[RouteCue]:
    """The cues of a BRouter route, in route order; empty when it has none or they are unusable."""
    hints = properties.get("voicehints")
    if not isinstance(hints, list) or not isinstance(coordinates, list) or len(coordinates) < 2:
        return []
    line: list[tuple[float, float]] = []
    for position in coordinates:
        lon = _number(position[0]) if isinstance(position, list) and len(position) >= 2 else None
        lat = _number(position[1]) if isinstance(position, list) and len(position) >= 2 else None
        if lon is None or lat is None:
            return []
        line.append((lon, lat))
    cumulative = cumulative_lengths_m(line)
    cues: list[RouteCue] = []
    for hint in hints:
        if not isinstance(hint, list) or len(hint) < 2:
            continue
        index, command = _number(hint[0]), _number(hint[1])
        if index is None or command is None or not 0 <= index < len(line):
            continue
        kind = _KINDS.get(int(command))
        if kind is None:
            continue
        exit_number = _number(hint[2]) if len(hint) > 2 else None
        angle = _number(hint[4]) if len(hint) > 4 else None
        lon, lat = line[int(index)]
        cues.append(
            RouteCue(
                kind=kind,  # type: ignore[arg-type]
                distance_m=round(cumulative[int(index)], 1),
                lon=lon,
                lat=lat,
                exit=int(exit_number)
                if kind == "roundabout" and exit_number and 1 <= exit_number <= MAX_EXIT
                else None,
                angle_deg=round(angle) if angle is not None and abs(angle) <= 180 else None,
            )
        )
    return cues
