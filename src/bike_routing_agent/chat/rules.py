"""Understanding a chat message without a language model: rules, not guesses.

A person can ask for a route in several ways and the chat accepts them all
(docs/chat.md): a one-line request ("from Braunschweig to Wolfenbüttel by gravel bike",
"40 km loop from Goslar"), free text for the optional LLM parser, or a step-by-step dialogue.
This module is the deterministic part: it reads the one-line forms, recognises what a follow-up
message wants (change the last route, show alternatives, explain, export ...) and turns a change
request into edits of the previous request.

It never invents anything: places stay the words the user typed (a later step looks them up),
and a message it does not understand is reported as such.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Literal

BIKE_WORDS: dict[str, str] = {
    "road": "road",
    "racing": "road",
    "racer": "road",
    "gravel": "gravel",
    "touring": "touring",
    "trekking": "touring",
    "mountain": "mountain",
    "mtb": "mountain",
    "city": "city",
    "ebike": "ebike",
    "e-bike": "ebike",
    "electric": "ebike",
    "commuter": "commuter",
    "commuting": "commuter",
    "recumbent": "recumbent",
}
_BIKE_ALT = "|".join(sorted((re.escape(w) for w in BIKE_WORDS), key=len, reverse=True))

SIGHT_WORDS = (
    "sights|sight|castles|castle|attractions|attraction|viewpoints|viewpoint|museums|museum|"
    "landmarks|landmark|churches|waterfalls|places of interest|points of interest"
)
_NUMBER_WORDS = {"one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5}

Intent = Literal[
    "plan",  # a one-line request this module could read
    "plan_text",  # worth giving to the free-text (LLM) parser
    "refine",  # change the last route
    "alternatives",  # list or pick one of the alternatives of the last plan
    "sights",  # which sights are along the last route
    "explain",  # a question about the last route (weather, climb, why ...)
    "export",  # files of the last route
    "reset",
    "help",
    "guided",  # start (or answer) the step-by-step dialogue
]

_COORDINATE = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*[,;]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


def place_value(text: str) -> Any:
    """``"lat, lon"`` as a coordinate; any other text as the place words typed."""
    cleaned = text.strip().strip(".,;!?\"'")
    match = _COORDINATE.match(cleaned)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return {"lon": lon, "lat": lat}
    return cleaned


@dataclass
class OneLine:
    """What a one-line route request said."""

    origin: str | None = None
    destination: str | None = None
    via: list[str] = field(default_factory=list)
    loop: bool = False
    distance_km: float | None = None
    bike_type: str | None = None
    sight_stops: int | None = None

    def complete(self) -> bool:
        if self.loop:
            return bool(self.origin) and self.distance_km is not None
        return bool(self.origin) and bool(self.destination)


def _km(text: str) -> float | None:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:km|kilomet(?:er|re)s?)\b", text, re.I)
    return float(match.group(1).replace(",", ".")) if match else None


def _bike(text: str) -> str | None:
    match = re.search(
        rf"\b({_BIKE_ALT})\b(?:\s+(?:bike|bicycle|tyres?|tires?|route|ride))?", text, re.I
    )
    return BIKE_WORDS[match.group(1).lower()] if match else None


def _sight_count(text: str) -> int | None:
    """``past 2 castles`` / ``along the most famous sights`` -> stops (None = not asked)."""
    pattern = (
        rf"\b(?:past|along|via|with|including|passing|visiting)\s+(?:the\s+)?"
        rf"(?:(\d+|one|two|three|four|five|a|an)\s+)?(?:of\s+the\s+)?"
        rf"(?:most\s+|best\s+)?(?:famous\s+|known\s+|well-known\s+|popular\s+)?"
        rf"(?:{SIGHT_WORDS})\b"
    )
    match = re.search(pattern, text, re.I)
    if not match:
        return None
    raw = (match.group(1) or "").lower()
    if raw.isdigit():
        return max(1, min(int(raw), 5))
    return _NUMBER_WORDS.get(raw, 2)


_TRAILING = re.compile(
    rf"""\s+(?:
        (?:by|on|with|using|for)\s+(?:a|an|my|the)?\s*(?:{_BIKE_ALT})\b.*
      | (?:past|along|passing|visiting|including)\s+(?:the\s+)?(?:\d+|one|two|three|four|five)?\s*
        (?:of\s+the\s+)?(?:most\s+|best\s+)?(?:famous\s+|known\s+|popular\s+)?(?:{SIGHT_WORDS})\b.*
      | via\s+.*
      | (?:about|around|approximately)?\s*\d+(?:[.,]\d+)?\s*km\b.*
    )$""",
    re.I | re.X,
)


def _strip_clauses(place: str) -> str:
    previous = None
    while previous != place:
        previous = place
        place = _TRAILING.sub("", place).strip().strip(".,;!?")
    return place


_PAIR = r"-?\d{1,3}\.\d+\s*,\s*-?\d{1,3}\.\d+"


def _split_places(text: str) -> list[str]:
    """Places separated by commas / "and" -- keeping a ``lat, lon`` pair in one piece."""
    pairs: list[str] = []

    def hold(match: re.Match[str]) -> str:
        pairs.append(match.group(0))
        return f"\0{len(pairs) - 1}\0"

    held = re.sub(_PAIR, hold, text)
    parts = [p.strip().strip(".,;!?") for p in re.split(r"\s*(?:,|\band\b|;)\s*", held)]
    return [re.sub(r"\0(\d+)\0", lambda m: pairs[int(m.group(1))], p) for p in parts if p]


def _vias(text: str) -> list[str]:
    out: list[str] = []
    for match in re.finditer(
        r"\bvia\s+(.+?)(?=\s+(?:by|on|with|using|for|past|along)\b|$)", text, re.I
    ):
        for part in _split_places(match.group(1)):
            if part.lower() not in SIGHT_WORDS.split("|"):
                out.append(part)
    return out


def parse_one_line(text: str) -> OneLine:
    """The route request in a single line, when it has one (``complete()`` says whether it is
    enough to plan)."""
    t = " ".join(text.split())
    out = OneLine(
        distance_km=_km(t),
        bike_type=_bike(t),
        sight_stops=_sight_count(t),
        via=_vias(t),
    )
    if re.search(
        r"\b(?:loop|round[\s-]?trip|circuit|circular|there and back|out and back)\b", t, re.I
    ):
        out.loop = True
        match = re.search(
            r"\b(?:from|around|near|starting\s+(?:at|in|from)|out of|at|in)\s+(.+)$", t, re.I
        )
        if match:
            out.origin = _strip_clauses(match.group(1)) or None
        return out
    match = re.search(
        r"^(?:(?:please\s+)?(?:plan|find|route|show|give|get)(?:\s+me)?(?:\s+a)?(?:\s+route)?\s+)?"
        r"(?:from\s+)?(?P<o>.+?)\s+(?:to|->|→|until|towards?)\s+(?P<d>.+)$",
        t,
        re.I,
    )
    if match:
        origin = _strip_clauses(match.group("o"))
        # A bike or distance clause may sit between the places: "gravel from A to B".
        origin = re.sub(
            rf"^(?:a\s+|an\s+)?(?:{_BIKE_ALT})(?=\s+(?:bike|route|ride|from)\b|\s*$)"
            r"(?:\s+bike)?(?:\s+(?:route|ride))?(?:\s+from)?\s*",
            "",
            origin,
            flags=re.I,
        )
        out.origin = origin.strip() or None
        out.destination = _strip_clauses(match.group("d")) or None
    return out


# --------------------------------------------------------------------- follow-up intents

_PATTERNS: list[tuple[Intent, re.Pattern[str]]] = [
    (
        "reset",
        re.compile(
            r"^\s*(?:new(?:\s+(?:route|chat|search))?|start over|reset|clear|"
            r"forget (?:it|that))\s*[.!]?\s*$",
            re.I,
        ),
    ),
    ("help", re.compile(r"^\s*(?:help|\?|what can you do|how does this work|commands)\b", re.I)),
    (
        "export",
        re.compile(
            r"\b(?:gpx|geojson|download|export|save|files?|garmin|wahoo|navigation file)\b", re.I
        ),
    ),
    (
        "alternatives",
        re.compile(
            r"\b(?:alternatives?|other (?:routes?|options?|versions?)|options|compare|comparison|"
            r"(?:show|use|take|give|pick|choose|switch to)\b.*"
            r"\b(?:version|one|route|option|alternative)|"
            r"the (?:first|second|third|fourth|fifth|1st|2nd|3rd) (?:one|route|option|alternative)|"
            r"\b(?:the|that)\s+(?:\w+\s+)?(?:version|variant|one|option|alternative)\b|"
            r"\b\w+\s+(?:version|variant)\b|"
            r"(?:option|alternative|route) #?\d)\b",
            re.I,
        ),
    ),
    (
        "sights",
        re.compile(
            rf"\b(?:what|which|any|show|list|are there|anything|things to see|worth)\b"
            rf".*\b(?:{SIGHT_WORDS})\b|"
            rf"\b(?:{SIGHT_WORDS})\b.*"
            rf"\b(?:on the way|along|near|nearby|on this route|on my route)\b",
            re.I,
        ),
    ),
    (
        "explain",
        re.compile(
            r"\b(?:weather|rain|raining|wind|windy|headwind|temperature|cold|warm|hot|sunset|sunrise|"
            r"dark|daylight|how (?:far|long|steep|hilly|much)|climb|climbing|ascent|elevation|"
            r"surface|paved|why|explain|pros|cons|how fast|duration)\b",
            re.I,
        ),
    ),
]

_REFINE = re.compile(
    r"\b(?:shorter|longer|more km|fewer km|flatter|less (?:climb|climbing|hilly|elevation)|"
    r"less hills?|no hills?|hillier|more climb|avoid|no ferr|allow|without|instead|"
    r"change|make it|switch|"
    r"use (?:the )?(?:road|gravel|touring|mountain|city|e-?bike|commuter|recumbent)|"
    r"(?:use|try|using) (?:ors|openrouteservice|brouter|valhalla)|"
    r"other direction|opposite direction|counter-?clockwise|clockwise|paved|unpaved|gravel only|"
    r"add (?:a )?(?:stop|via)|stop at|via|past|along|more traffic|less traffic|quieter|"
    rf"(?:{_BIKE_ALT}) (?:bike|instead)|on (?:a|my) (?:{_BIKE_ALT})|by (?:{_BIKE_ALT}))\b",
    re.I,
)


def classify(text: str, *, has_plan: bool) -> Intent | None:
    """What a follow-up (or a first) message wants, or ``None`` when no rule applies."""
    for intent, pattern in _PATTERNS:
        if intent in ("reset", "help"):
            if pattern.search(text):
                return intent
            continue
        if has_plan and pattern.search(text):
            return intent
    if has_plan and _REFINE.search(text):
        return "refine"
    return None


# ----------------------------------------------------------------------------- refinements


@dataclass
class Refinement:
    """Edits to the previous request, and how to say what changed."""

    constraints: dict[str, Any] = field(default_factory=dict)
    add_via: list[str] = field(default_factory=list)
    drop_via: bool = False
    poi_stops: int | None = None  # 0 = drop the sight stops
    routing_engines: list[str] | None = None
    notes: list[str] = field(default_factory=list)
    unknown: bool = False

    def empty(self) -> bool:
        return not (
            self.constraints
            or self.add_via
            or self.drop_via
            or self.poi_stops is not None
            or self.routing_engines
        )


def refine(
    text: str,
    previous: dict[str, Any],
    *,
    last_ascent_m: float | None = None,
    last_distance_m: float | None = None,
) -> Refinement:
    """Turn "shorter", "avoid ferries", "on a road bike" ... into edits of ``previous``."""
    out = Refinement()
    t = text.lower()
    constraints = previous.get("constraints") or {}
    target = constraints.get("target_distance_km")
    maximum = constraints.get("max_distance_km")

    bike = _bike(text)
    if bike and bike != constraints.get("bike_type"):
        out.constraints["bike_type"] = bike
        out.notes.append(f"bike: {bike}")

    km = _km(text)
    if km is not None and re.search(
        r"\b(?:max(?:imum)?|at most|no more than|under|within|up to)\b", t
    ):
        out.constraints["max_distance_km"] = km
        out.notes.append(f"at most {km:g} km")
    elif km is not None and (constraints.get("return_to_origin") or target is not None):
        out.constraints["target_distance_km"] = km
        out.notes.append(f"about {km:g} km")
    elif re.search(r"\b(?:shorter|fewer km|less distance|cut it)\b", t):
        base = target or maximum
        if base:
            key = "target_distance_km" if target else "max_distance_km"
            out.constraints[key] = round(base * 0.75, 1)
            out.notes.append(f"about 25 % shorter ({round(base * 0.75, 1):g} km)")
        elif last_distance_m:
            cap = round(last_distance_m / 1000 * 0.75, 1)
            out.constraints["max_distance_km"] = cap
            out.notes.append(f"at most {cap:g} km (25 % shorter than the current route)")
        else:
            out.notes.append("no distance was set, so 'shorter' needs one: say e.g. 'max 20 km'.")
            out.unknown = True
    elif re.search(r"\b(?:longer|more km|more distance|extend)\b", t):
        base = target or maximum
        if base:
            out.constraints["target_distance_km" if target else "max_distance_km"] = round(
                base * 1.25, 1
            )
            out.notes.append(f"about 25 % longer ({round(base * 1.25, 1):g} km)")
        else:
            out.notes.append("no distance was set, so 'longer' needs one: say e.g. '40 km'.")
            out.unknown = True

    if re.search(
        r"\b(?:flatter|less (?:climb|climbing|hilly|elevation)|less hills?|no hills?|"
        r"fewer hills?)\b",
        t,
    ):
        if last_ascent_m:
            cap = max(10.0, round(last_ascent_m * 0.7))
            out.constraints["max_ascent_m"] = cap
            out.notes.append(f"at most {cap:g} m of climbing (30 % less than before)")
        else:
            out.notes.append("I do not know how much it climbed, so say e.g. 'max 100 m ascent'.")
            out.unknown = True
    ascent = re.search(
        r"\b(?:max(?:imum)?|at most|under)\s*(\d+)\s*m\b.*\b(?:climb|ascent|elevation)|"
        r"\b(?:climb|ascent|elevation)\b.*\b(?:max(?:imum)?|at most|under)\s*(\d+)\s*m\b",
        t,
    )
    if ascent:
        value = float(ascent.group(1) or ascent.group(2))
        out.constraints["max_ascent_m"] = value
        out.notes.append(f"at most {value:g} m of climbing")

    if re.search(r"\b(?:avoid|no|without)\s+ferr", t):
        out.constraints["avoid_ferries"] = True
        out.notes.append("avoid ferries")
    elif re.search(r"\b(?:allow|ok with|use)\s+ferr", t):
        out.constraints["avoid_ferries"] = False
        out.notes.append("ferries allowed")
    if re.search(
        r"\b(?:avoid|no|without|less)\s+(?:busy\s+)?"
        r"(?:traffic|cars|main roads|busy roads)\b|\bquieter\b",
        t,
    ):
        out.constraints["avoid_high_traffic_roads"] = True
        out.notes.append("avoid busy roads")
    elif re.search(r"\b(?:allow|ok with|more)\s+(?:busy\s+)?(?:traffic|main roads)\b", t):
        out.constraints["avoid_high_traffic_roads"] = False
        out.notes.append("busy roads allowed")

    if re.search(r"\b(?:paved|asphalt|tarmac|smooth)\b", t) and not re.search(
        r"\b(?:avoid|no|not|unpaved)\b.*\bpaved\b", t
    ):
        out.constraints["prefer_surfaces"] = ["paved"]
        out.constraints["avoid_surfaces"] = []
        out.notes.append("prefer paved surfaces")
    elif re.search(r"\b(?:unpaved|off-?road|dirt|trails?|gravel only|more gravel)\b", t):
        out.constraints["prefer_surfaces"] = ["gravel"]
        out.notes.append("prefer gravel / unpaved surfaces")
    if re.search(r"\b(?:avoid|no|without)\s+(?:gravel|unpaved|dirt|sand)\b", t):
        out.constraints["avoid_surfaces"] = ["gravel", "sand"]
        out.constraints.setdefault("prefer_surfaces", ["paved"])
        out.notes.append("avoid gravel and sand")

    if re.search(
        r"\b(?:other|opposite|reverse|reversed)\s+direction\b|\bcounter-?clockwise\b|\bclockwise\b",
        t,
    ):
        counter = bool(re.search(r"counter-?clockwise", t)) or (
            "other direction" in t and constraints.get("loop_direction", "clockwise") == "clockwise"
        )
        out.constraints["loop_direction"] = "counterclockwise" if counter else "clockwise"
        out.notes.append(f"loop {out.constraints['loop_direction']}")

    stops = _sight_count(text)
    if stops is not None:
        out.poi_stops = stops
        out.notes.append(f"past {stops} well-known sight{'s' if stops != 1 else ''}")
    elif re.search(
        r"\b(?:no|without|drop|skip|remove)\s+(?:the\s+)?(?:sights|stops|castles|attractions)\b", t
    ):
        out.poi_stops = 0
        out.notes.append("no sight stops")

    removing = re.search(
        r"\b(?:remove|drop|delete|clear|no|without)\s+(?:the\s+|all\s+)?(?:via|stops?|waypoints?)"
        r"(?:\s+points?)?\b",
        t,
    )
    if removing and out.poi_stops is None:
        out.drop_via = True
        out.notes.append("no via points")
    else:
        for match in re.finditer(
            rf"\b(?:add (?:a )?(?:stop|via)(?: point)?(?: at| in)?|stop at|stopping at|via)\s+"
            rf"({_PAIR}|.+?)(?=\s+(?:and|by|on|with|using|past)\b|[.;!?]|$)",
            text,
            re.I,
        ):
            for place in _split_places(match.group(1)):
                if place.lower() not in (*SIGHT_WORDS.split("|"), "point", "points"):
                    out.add_via.append(place)
                    out.notes.append(f"via {place}")

    engine = re.search(
        r"\b(?:use|with|via|using|try)\s+(ors|openrouteservice|brouter|valhalla)\b", t
    )
    if engine:
        name = {"openrouteservice": "ors"}.get(engine.group(1), engine.group(1))
        out.routing_engines = [name]
        out.notes.append(f"routing engine: {name}")
        if out.add_via and out.add_via[-1].lower() in (
            "ors",
            "openrouteservice",
            "brouter",
            "valhalla",
        ):
            out.add_via.pop()

    if out.empty() and not out.unknown:
        out.unknown = True
    return out


def apply_refinement(previous: dict[str, Any], change: Refinement) -> dict[str, Any]:
    """The new request: ``previous`` with the edits applied (``previous`` is not changed)."""
    request: dict[str, Any] = copy.deepcopy(previous)
    constraints = dict(request.get("constraints") or {})
    constraints.update(change.constraints)
    request["constraints"] = constraints
    if change.drop_via:
        request["via"] = []
    if change.add_via:
        request["via"] = [*(request.get("via") or []), *(place_value(p) for p in change.add_via)]
    if change.poi_stops is not None:
        if change.poi_stops == 0:
            request.pop("poi_stops", None)
        else:
            request["poi_stops"] = {**(request.get("poi_stops") or {}), "count": change.poi_stops}
    if change.routing_engines:
        request["routing_engines"] = change.routing_engines
    return request


# ------------------------------------------------------------------------ choosing results

_ORDINALS = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
}


def pick_candidate(text: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The alternative a message names: by rank ("second", "#2"), profile ("the mtb one") or by
    what it is best at ("fastest", "shortest", "flattest")."""
    t = text.lower()
    for word, rank in _ORDINALS.items():
        if re.search(rf"\b{word}\b", t):
            return next((c for c in candidates if c.get("rank") == rank), None)
    number = re.search(r"(?:#|\boption\s+|\balternative\s+|\broute\s+)(\d)\b", t)
    if number:
        return next((c for c in candidates if c.get("rank") == int(number.group(1))), None)

    def metric(name: str) -> Any:
        return lambda c: (c.get("metrics") or {}).get(name)

    ranked = {
        "fastest": ("duration_s", min),
        "quickest": ("duration_s", min),
        "shortest": ("distance_m", min),
        "flattest": ("ascent_m", min),
        "least climbing": ("ascent_m", min),
        "longest": ("distance_m", max),
    }
    for phrase, (key, chooser) in ranked.items():
        if phrase in t:
            usable = [c for c in candidates if metric(key)(c) is not None]
            return chooser(usable, key=metric(key)) if usable else None
    for c in candidates:
        profile = str(c.get("provider_profile") or "").lower().replace("custom_", "").split("-")[0]
        names = {profile, str(c.get("provider") or "").lower()}
        if profile == "mtb":
            names.add("mountain")
        if any(n and re.search(rf"\b{re.escape(n)}\b", t) for n in names):
            return c
    return None
