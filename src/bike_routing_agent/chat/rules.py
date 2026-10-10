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
from datetime import datetime
from typing import Any, Literal

from bike_routing_agent.chat.when import parse_departure

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
# Compound forms of a bike word, spelt as one word (hyphens removed).
BARE_BIKE_WORDS = {
    "gravelbike": "gravel",
    "rennrad": "road",
    "trekkingrad": "touring",
    "mountainbike": "mountain",
    "ebike": "ebike",
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
    # Other constraints the line named (ferries, surfaces, climb limit, direction ...).
    constraints: dict[str, Any] = field(default_factory=dict)
    departure: datetime | None = None
    engines: list[str] | None = None
    # Said but not applicable ("flat": no number to hold it to) -- reported, never dropped quietly.
    ignored: list[str] = field(default_factory=list)
    # Numbers the chat had to assume ("2 hours at 18 km/h = 36 km") -- reported as well.
    assumed: list[str] = field(default_factory=list)
    explicit_separator: bool = False  # the line had an "A to B" shape

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


def _sight_match(text: str) -> re.Match[str] | None:
    """``past 2 castles`` / ``along the most famous sights`` / ``, 2 sights``."""
    qualifiers = (
        r"(?:of\s+the\s+)?(?:most\s+|best\s+)?(?:famous\s+|known\s+|well-known\s+|popular\s+)?"
    )
    counted = re.compile(
        rf"\b(\d+|one|two|three|four|five)\s+{qualifiers}(?:{SIGHT_WORDS})\b", re.I
    )
    after_preposition = re.compile(
        rf"\b(?:past|along|via|with|including|passing|visiting)\s+(?:the\s+)?"
        rf"(?:(\d+|one|two|three|four|five|a|an)\s+)?{qualifiers}(?:{SIGHT_WORDS})\b",
        re.I,
    )
    return after_preposition.search(text) or counted.search(text)


def _sight_count(text: str) -> int | None:
    """Stops asked for (None = not asked)."""
    match = _sight_match(text)
    if not match:
        return None
    raw = (match.group(1) or "").lower()
    if raw.isdigit():
        return max(1, min(int(raw), 5))
    return _NUMBER_WORDS.get(raw, 2)


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


# Where a list of via places ends: the next clause, or the "to <destination>" of the line.
_VIA_END = r"(?=\s+(?:by|on|with|using|for|past|along|to|until|nach|bis)\b|\s*(?:->|→)|$)"
_VIA_CLAUSE = re.compile(rf"\b(?:via|through|über)\s+(.+?){_VIA_END}", re.I)


def _vias(text: str) -> list[str]:
    out: list[str] = []
    for match in _VIA_CLAUSE.finditer(text):
        for part in _split_places(match.group(1)):
            if part.lower() not in SIGHT_WORDS.split("|"):
                out.append(part)
    return out


# ------------------------------------------------------------------ the one-line request

_MAX_WORDS = r"max(?:imum)?|at most|no more than|not more than|under|below|within|up to|less than"
_NUM = r"\d+(?:[.,]\d+)?"
_SEP = re.compile(r"\s+(?:to|until|towards?|nach|bis|->|→|–|—|-)\s+|\s*(?:->|→)\s*", re.I)
_FROM = re.compile(r"\b(?:from|von|ab|starting\s+(?:at|in|from)|start(?:ing)?\s+in)\s+", re.I)
_LOOP_WORDS = re.compile(
    r"\b(?:loop|round[\s-]?trip|circuit|circular(?:\s+(?:route|ride|tour))?|there and back|"
    r"out and back|and back|then back|and return|and then back|rundtour|rundfahrt|"
    r"und zur[üu]ck)\b",
    re.I,
)
_ENGINE = re.compile(
    r"\b(?:using|use|with|try)\s+(?:ors|openrouteservice|brouter|valhalla)\b", re.I
)
_ASCENT = re.compile(
    rf"(?:(?:{_MAX_WORDS}|with)\s+)?(?P<n>{_NUM})\s*(?:m|meters?|metres?)\s+(?:of\s+)?"
    r"(?:climb\w*|ascent|elevation(?:\s+gain)?|height\s+gain|hills?)\b"
    rf"|\b(?:climb\w*|ascent|elevation)\s+(?:{_MAX_WORDS})\s+(?P<n2>{_NUM})\s*(?:m|meters?|metres?)\b",
    re.I,
)
_SPEED = re.compile(rf"(?:(?:at|with|around|about)\s+)?{_NUM}\s*km\s*/\s*h\b", re.I)
_DISTANCE = re.compile(
    rf"(?:(?P<q>{_MAX_WORDS}|about|around|approx(?:imately)?|ca\.?)\s+)?"
    rf"(?P<n>{_NUM})\s*(?:km|kilomet(?:er|re)s?)\b"
    r"(?:\s+(?:loop|round[\s-]?trip|ride|route|tour|trip|circuit))?"
    r"(?:\s+(?P<post>max(?:imum)?|at most|or less|or fewer|tops))?",
    re.I,
)
_DURATION = re.compile(
    rf"(?:(?P<q>{_MAX_WORDS}|about|around|approx(?:imately)?)\s+)?"
    rf"(?:(?P<h>{_NUM})\s*(?:hours?|hrs?|h)\b(?:\s*(?:and\s+)?(?P<m>\d{{1,2}})\s*min\w*)?"
    r"|(?P<min>\d{1,3})\s*(?:minutes?|mins?)\b|(?P<half>half an hour)|(?P<one>an hour))"
    r"(?:\s+(?:loop|round[\s-]?trip|ride|route|tour|trip|circuit))?",
    re.I,
)
# Clauses removed from the line once their meaning is taken (refine() reads the meaning).
_FLAG_ITEM = (
    r"(?:ferr\w+|(?:busy\s+)?(?:traffic|cars|main\s+roads|busy\s+roads|highways?|motorways?)|"
    r"gravel|unpaved|dirt|sand|cobbles?|cobblestones?)"
)
_FLAG_CLAUSES = re.compile(
    rf"\b(?:avoid(?:ing)?|no|without|skip(?:ping)?|allow(?:ing)?)\s+{_FLAG_ITEM}\b"
    rf"(?:\s*(?:,|and|or|&)\s*(?:(?:avoid|no|without)\s+)?{_FLAG_ITEM}\b)*"
    r"|\bquiet(?:er)?(?:\s+(?:roads|streets|routes|ways))?(?:\s+only)?\b"
    r"|\b(?:paved|asphalt|tarmac)(?:\s+(?:only|roads|surfaces))?\b|\b(?:unpaved|off-?road)\b"
    r"|\b(?:in\s+the\s+)?(?:other|opposite)\s+direction\b|\b(?:counter-?)?clockwise\b",
    re.I,
)
# Wishes a number is needed for; said, but nothing here can hold a route to them.
_WISHES = {
    r"\b(?:flat(?:test)?|not\s+(?:too\s+)?hilly|few(?:er)?\s+hills?)\b": (
        "flat -- I need a number to hold the route to, e.g. 'max 300 m climb'"
    ),
    r"\b(?:hilly|hillier|hills|mountainous)\b": "hilly -- I cannot ask for more climbing",
    r"\b(?:scenic|beautiful|pretty|nice view\w*)\b": "scenic -- I cannot rate scenery",
}
_RIDE_WORD = r"(?:\d+\s*km\s+)?(?:ride|route|tour|trip|loop|round|circuit|journey|way)\b"
_FILLER = re.compile(
    r"\b(?:please|thanks|thank you|for me)\b|"
    r"\b(?:on|for|as|in)\s+(?:a|an|the|my)\s+(?:ride|route|tour|trip|way|journey)\b|"
    rf"\b(?:nice|good|short|long|quick|easy|relaxed|relaxing|pleasant|fun)\s+"
    rf"(?={_RIDE_WORD}|around\b|near\b|from\b|in\b|at\b|$)",
    re.I,
)
# Words that open a sentence before the places ("can you plan me a ride ...").
_LEAD = re.compile(
    r"^(?:(?:hi|hello|hey|please|can|could|would|you|i|want|wanna|would|like|'d|need|to|"
    r"let's|lets|help|me|plan|planning|find|show|give|get|make|create|build|route|ride|cycle|"
    r"cycling|bike|biking|tour|trip|journey|path|a|an|my|some|one)\b[\s,]*)+",
    re.I,
)
# Words that make a "place" no place: a clause that got into it.
_NOT_A_PLACE = re.compile(
    r"\b(?:avoid\w*|without|max|maximum|min|km|hours?|tomorrow|today|bike|route|please|"
    r"ferr\w+|climb\w*|ascent|paved|unpaved|flat|hilly|quiet|using|loop|"
    # a sentence, not a place:
    r"i|we|you|me|my|would|love|want|wanna|need|can|could|ride|rides|cycle|cycling|plan|go|"
    r"take|trip|tour|what|which|how|why|where|when|who|is|are|was|were|be|been|do|does|did|"
    r"will|should|weather|rain|wind|temperature|expected)\b|\d\s*(?:m|km|h)\b",
    re.I,
)
# Average speeds (km/h) used only to turn "a 2 hour loop" into a distance -- and said so.
SPEEDS_KMH = {"road": 25, "gravel": 20, "mountain": 15, "touring": 17, "city": 16, "ebike": 22}
DEFAULT_SPEED_KMH = 18


def _cut(text: str, match: re.Match[str]) -> str:
    return (text[: match.start()] + " " + text[match.end() :]).strip()


def _tidy(text: str) -> str:
    """Remove the punctuation and joining words clause removal leaves at either end."""
    text = " ".join(text.split())
    previous = None
    while previous != text:
        previous = text
        text = re.sub(
            r"(?:^|\s)(?:and|with|by|on|for|at|using|in|then|also|only)?[\s,;.:]*$",
            "",
            text,
            flags=re.I,
        )
        text = re.sub(r"^[\s,;.:]+|[\s,;]+$", "", text)
    return text


def _plausible(place: str | None) -> str | None:
    if not place:
        return None
    place = place.strip(" ,;.!?")
    words = place.split()
    if not place or len(words) > 6 or len(place) > 60 or _NOT_A_PLACE.search(place):
        return None
    return place


def _bike_match(text: str) -> re.Match[str] | None:
    """A bike type said as a bike type: not "Mountain View" or "City Park" ("city" alone)."""
    for pattern in (
        rf"\b(?:by|on|with|using|for|my)\s+(?:(?:my|a|an|the)\s+)?(?P<w>{_BIKE_ALT})\b"
        r"(?:\s+(?:bike|bicycle|tyres?|tires?|route|ride|tour))?",
        rf"\b(?:a\s+|an\s+|the\s+)?(?P<w>{_BIKE_ALT})\s+(?:bike|bicycle|route|ride|tour|trip|loop)\b",
        r"\b(?P<w>mtb|e-?bike|gravel|gravel-?bike|rennrad|trekking-?rad|mountain-?bike)\b",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            return match
    return None


def parse_one_line(text: str, *, now: datetime | None = None) -> OneLine:
    """The route request in a single line, when it has one (``complete()`` says whether it is
    enough to plan).

    The line is read by taking its clauses out one kind at a time (when, how far, which bike,
    sights, via places, avoid/prefer words ...); what is left is the places. ``now`` (with the
    caller's time zone) lets "tomorrow at 8" become a departure time.
    """
    t = " ".join(text.split())
    out = OneLine()

    if now is not None:
        out.departure, t = parse_departure(t, now)

    # Meaning first: the avoid/prefer words are read by the same rules as a follow-up.
    meaning = refine(t, {})
    for key, value in meaning.constraints.items():
        if key not in ("bike_type", "target_distance_km", "max_distance_km", "max_ascent_m"):
            out.constraints[key] = value
    out.engines = meaning.routing_engines

    engine = _ENGINE.search(t)
    while engine:
        t = _cut(t, engine)
        engine = _ENGINE.search(t)
    stated_speed = _SPEED.search(t)
    if stated_speed:
        out.ignored.append(f"'{stated_speed.group(0).strip()}' -- speed is not a setting")
        t = _cut(t, stated_speed)

    climb = _ASCENT.search(t)
    if climb:
        out.constraints["max_ascent_m"] = float(
            (climb.group("n") or climb.group("n2")).replace(",", ".")
        )
        t = _cut(t, climb)

    loop_word = _LOOP_WORDS.search(t)
    duration = _DURATION.search(t)
    hours = None
    if duration:
        if duration.group("h"):
            hours = float(duration.group("h").replace(",", "."))
            hours += int(duration.group("m") or 0) / 60
        elif duration.group("min"):
            hours = int(duration.group("min")) / 60
        else:
            hours = 0.5 if duration.group("half") else 1.0
        t = _cut(t, duration)

    distance = _DISTANCE.search(t)
    if distance:
        km = float(distance.group("n").replace(",", "."))
        qualifier = (distance.group("q") or "").lower()
        out.distance_km = km
        if (re.fullmatch(_MAX_WORDS, qualifier) or distance.group("post")) and not loop_word:
            out.constraints["max_distance_km"] = km
            out.distance_km = None
        t = _cut(t, distance)

    sights = _sight_match(t)
    if sights:
        out.sight_stops = _sight_count(t)
        t = _cut(t, sights)

    bike = _bike_match(t)
    if bike:
        word = bike.group("w").lower().replace("-", "")
        out.bike_type = BIKE_WORDS.get(word) or BARE_BIKE_WORDS.get(word)
        t = _cut(t, bike)

    for pattern, note in _WISHES.items():
        if re.search(pattern, t, re.I) and not (
            "flat" in note and out.constraints.get("max_ascent_m")
        ):
            out.ignored.append(note)
    for pattern in _WISHES:
        t = re.sub(pattern, " ", t, flags=re.I)
    t = _FLAG_CLAUSES.sub(" ", t)
    t = _FILLER.sub(" ", t)

    out.via = []
    via_match = _VIA_CLAUSE.search(t)
    while via_match:
        for part in _split_places(via_match.group(1)):
            if part.lower() not in SIGHT_WORDS.split("|"):
                out.via.append(part)
        t = _cut(t, via_match)
        via_match = _VIA_CLAUSE.search(t)

    returning = False
    if loop_word:
        without_loop_words = _tidy(_LOOP_WORDS.sub(" ", t))
        returning = bool(_SEP.search(without_loop_words))  # "A to B and back": a return trip
        t = without_loop_words
        out.loop = not returning
    if loop_word and not returning:
        if out.distance_km is None and hours is not None:
            speed = SPEEDS_KMH.get(out.bike_type or "", DEFAULT_SPEED_KMH)
            out.distance_km = round(hours * speed, 1)
            out.assumed.append(
                f"{hours:g} h taken as {out.distance_km:g} km at {speed} km/h (my assumption)"
            )
        origin = _FROM.split(t, maxsplit=1)[-1] if _FROM.search(t) else t
        origin = re.sub(
            r"^(?:around|near|out of|at|in|of)\s+", "", _LEAD.sub("", origin), flags=re.I
        )
        out.origin = _plausible(_tidy(origin))
        return out

    if hours is not None:
        out.ignored.append(f"{hours:g} h -- a trip between two places takes as long as it takes")
    if out.distance_km is not None:
        out.ignored.append(
            f"{out.distance_km:g} km -- a trip between two places is as long as the way there"
        )
        out.distance_km = None

    t = _tidy(t)
    if _FROM.search(t):
        t = _FROM.split(t, maxsplit=1)[-1]
    else:
        stripped = _LEAD.sub("", t, count=1)
        t = stripped if _SEP.search(stripped) and not _SEP.match(" " + stripped) else t
    parts = [p for p in (_tidy(x) for x in _SEP.split(t)) if p]
    if len(parts) >= 2:
        out.explicit_separator = True
        out.origin = _plausible(parts[0])
        aside = re.split(
            rf"\s*,\s*(?=(?:the\s+)?(?:most\s+|best\s+)?(?:famous\s+|known\s+)?(?:{SIGHT_WORDS})\b)",
            parts[-1],
            maxsplit=1,
        )
        if len(aside) > 1:
            out.ignored.append(
                f"'{aside[1].strip(' .,')}' -- say 'past 2 sights' to route past them"
            )
        destination = re.split(
            r"\s+(along|across|over|entlang|with|including|for|by|during|while)\s+",
            aside[0],
            maxsplit=1,
        )
        if len(destination) > 2:
            rest = f"{destination[1]} {destination[2]}".strip(" .,")
            out.ignored.append(f"'{rest}' -- not something I can plan with")
        out.destination = _plausible(_tidy(destination[0]))
        legs = [_plausible(x) for x in parts[1:-1]]
        out.via = [v for v in legs if v] + out.via
    elif len(parts) == 1:
        out.origin = _plausible(_LEAD.sub("", parts[0], count=1))
    if out.origin and out.destination and out.origin.lower() == out.destination.lower():
        out.loop, out.destination = True, None  # "Goslar to Goslar" is a loop; its length is asked
    elif returning and out.origin and out.destination:
        # Out and back: the far place becomes the last stop before the way home.
        out.via = [*out.via, out.destination]
        out.destination = out.origin
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
            r"^\s*(?:(?:show|use|take|pick|choose|give)(?:\s+me)?\s+)?(?:the\s+)?"
            r"(?:fastest|quickest|shortest|flattest|best|first|second|third)"
            r"(?:\s+(?:one|route|version|option|alternative))?\s*[.!]?\s*$|"
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

_REFINE_WHEN = re.compile(
    r"^\s*(?:at\s+\d|tomorrow|today|tonight|on\s+\w+day|next\s+\w+day|"
    r"this\s+(?:morning|afternoon|evening|weekend))\b|"
    r"\b(?:leave|leaving|start|starting|depart|departing|go|ride)\b.*"
    r"\b(?:tomorrow|today|tonight|at\s+\d|\d\s*(?:am|pm)|on\s+\w+day|next\s+\w+day|"
    r"this\s+(?:morning|afternoon|evening|weekend))\b",
    re.I,
)
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
    if has_plan and (_REFINE.search(text) or _REFINE_WHEN.search(text)):
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
    departure: datetime | None = None
    notes: list[str] = field(default_factory=list)
    unknown: bool = False

    def empty(self) -> bool:
        return not (
            self.constraints
            or self.add_via
            or self.drop_via
            or self.poi_stops is not None
            or self.routing_engines
            or self.departure
        )


def refine(
    text: str,
    previous: dict[str, Any],
    *,
    last_ascent_m: float | None = None,
    last_distance_m: float | None = None,
    now: datetime | None = None,
) -> Refinement:
    """Turn "shorter", "avoid ferries", "on a road bike" ... into edits of ``previous``."""
    out = Refinement()
    if now is not None:
        out.departure, text = parse_departure(text, now)
        if out.departure:
            out.notes.append("leaving " + out.departure.strftime("%a %d %b, %H:%M"))
    t = text.lower()
    t = re.sub(r"\bavoiding\b", "avoid", t)
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

    if re.search(r"\b(?:avoid(?:ing)?|no|without)\s+ferr", t):
        out.constraints["avoid_ferries"] = True
        out.notes.append("avoid ferries")
    elif re.search(r"\b(?:allow|ok with|use)\s+ferr", t):
        out.constraints["avoid_ferries"] = False
        out.notes.append("ferries allowed")
    if re.search(
        r"\b(?:avoid|no|without|less)\s+(?:busy\s+)?"
        r"(?:traffic|cars|main roads|busy roads|highways?|motorways?)\b|\bquieter\b|"
        r"\b(?:avoid|no|without|less)\b[^.;]*?\b(?:and|or|,)\s+(?:busy\s+)?"
        r"(?:traffic|cars|main roads|busy roads|highways?|motorways?)\b",
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
    elif re.search(
        r"\b(?:unpaved|off-?road|dirt|trails?|gravel only|more gravel)\b", t
    ) and not re.search(r"\b(?:avoid(?:ing)?|no|without)\s+(?:gravel|unpaved|dirt|sand)\b", t):
        out.constraints["prefer_surfaces"] = ["gravel"]
        out.notes.append("prefer gravel / unpaved surfaces")
    if re.search(r"\b(?:avoid(?:ing)?|no|without)\s+(?:gravel|unpaved|dirt|sand)\b", t):
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
    if change.departure:
        request["departure_time"] = change.departure.isoformat()
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
