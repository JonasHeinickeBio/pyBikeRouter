"""Optimisation goals said in words: "less traffic but as much asphalt as possible".

Rules, not guesses. A message is read as an optimisation request only when it is clearly one:
it says "optimise", or names two goals, or names one with a superlative ("quietest", "as much
asphalt as possible", "the most direct"). A single mild wish ("quieter") stays an ordinary
change to the route.

Goals keep the order they were said in and get falling weights (1.0, 0.7, 0.5, 0.4): the
first is what matters most, "but ..." after it is what to get as well.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# (metric, pattern, is_superlative_marker)
_GOALS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (metric, re.compile(pattern, re.I))
    for metric, pattern in (
        (
            "traffic",
            r"\b(?:less|low|little|least|minimal|minimum|no|fewer)[\s-]+"
            r"(?:traffic|cars|car traffic)\b"
            r"|\btraffic[\s-]free\b|\bcar[\s-]free\b|\bquiet(?:est|er)?(?:\s+(?:roads?|streets?|"
            r"routes?|ways?|paths?))?\b|\bcalm(?:er|est)?\b"
            r"|\bavoid(?:ing)?\s+(?:the\s+)?(?:traffic|cars|busy roads|main roads)\b"
            r"|\b(?:no|without|fewest?|away from)\s+(?:the\s+)?busy\s+(?:roads?|streets?)\b"
            r"|\b(?:kein|wenig|ohne)\s+(?:auto)?verkehr\b|\bruhig\w*\b",
        ),
        (
            "paved",
            r"\b(?:as much|as many|most|more|mostly|maximum|max)\s+(?:of\s+)?(?:the\s+)?"
            r"(?:asphalt\w*|tarmac|paved|smooth\w*)\b|\basphalt\w*\b|\btarmac\b|\bpaved\b"
            r"|\bsmooth(?:est)?\b",
        ),
        (
            "pois",
            r"\b(?:as many|as much|most|more|many|lots of|plenty of|maximum|max)\s+(?:of\s+)?"
            r"(?:the\s+)?(?:(?:famous|well-known|known|interesting|great|nice|best)\s+)?"
            r"(?:sights?|castles?|attractions?|landmarks?|museums?|viewpoints?|monuments?|"
            r"churches|places of interest|points of interest|pois?)\b"
            r"(?:\s+(?:as possible|along the (?:way|route|track)|on the way|en route))?"
            r"|\bsights?\s+along the (?:way|route|track)\b",
        ),
        ("unpaved", r"\boff[\s-]?road\b|\bunpaved\b|\bmore gravel\b|\btrails?\b|\bdirt\b"),
        (
            "cobbles",
            r"\b(?:no|fewer|avoid(?:ing)?|without)\s+cobble(?:s|stones?)?\b|\bcobble[\s-]free\b",
        ),
        ("distance", r"\bshortest\b|\bmost direct\b|\bdirectest\b"),
        ("duration", r"\bfastest\b|\bquickest\b|\bas fast as possible\b"),
        (
            "ascent",
            r"\bflattest\b|\bleast (?:climbing|hilly|elevation)\b|\bas flat as possible\b"
            r"|\bfewest hills\b|\bhill[\s-]?free\b"
            r"|\bflat\b(?!\s+(?:tyres?|tires?|tubes?|batter\w*))"
            r"|\b(?:less|little|low|fewer|minimal|no)\s+(?:climbing|climbs?|hills?|hilly|elevation"
            r"|ascent|steep(?:ness)?)\b"
            r"|\bnot\s+(?:too\s+)?(?:hilly|steep)\b"
            r"|\bavoid(?:ing)?\s+(?:the\s+)?(?:hills?|climbs?|steep\w*)\b"
            r"|\b(?:wenig|kaum|ohne)\s+(?:steigung(?:en)?|anstiege?|berge?)\b|\bflach\b",
        ),
    )
)
_EXPLICIT = re.compile(r"\boptimi[sz](?:e|ed|ing|ation)\b|\bbest (?:route|way|trade-?off)\b", re.I)
_SUPERLATIVE = re.compile(
    r"\bas\s+\w+(?:\s+\w+)?\s+as\s+possible\b|\b(?:quietest|smoothest|shortest|fastest|quickest|"
    r"flattest|most direct|most paved|least|fewest)\b|\bmaximum\b|\bmax\b",
    re.I,
)
_WEIGHTS = (1.0, 0.7, 0.5, 0.4)
# What joins goals together and drops out once they are taken from the line.
_GLUE = re.compile(
    r"\b(?:but|and|while|with|plus|also|yet|however|as much as possible|as possible|"
    r"optimi[sz]e(?:d)?(?:\s+(?:for|the route for|it for))?|the best route for|best route for)\b",
    re.I,
)


_ARTICLES = ("the", "a", "an", "of", "my", "some")
_STOP_WORDS = ("past", "along", "via", "passing", "visiting", "including", "through")


def _is_stop_request(text: str, match: re.Match[str]) -> bool:
    """ "past the most famous sights" asks for stops (the poi_stops feature); "as many sights as
    possible" and "the most sights along the way" ask to optimise."""
    said = match.group(0).lower()
    if said.startswith(("as many", "as much")) or "as possible" in said:
        return False
    words = [w for w in re.findall(r"[a-z]+", text[: match.start()].lower()) if w not in _ARTICLES]
    return bool(words) and words[-1] in _STOP_WORDS


@dataclass
class Goals:
    spec: dict[str, Any]  # an OptimizeSpec as a dict
    rest: str  # the text without the goal phrases
    explicit: bool = False
    metrics: list[str] = field(default_factory=list)


def detect(text: str) -> Goals | None:
    """The optimisation goals in ``text`` (and the text without them), or ``None`` when it is
    not clearly an optimisation request."""
    found: list[tuple[int, str, re.Match[str]]] = []
    for metric, pattern in _GOALS:
        match = pattern.search(text)
        if match and metric == "pois" and _is_stop_request(text, match):
            continue  # "past the most famous sights": stops on the route, not a goal
        if match:
            found.append((match.start(), metric, match))
    if not found:
        return None
    explicit = bool(_EXPLICIT.search(text))
    wants_sights = any(m == "pois" for _, m, _ in found)  # its pattern only matches strong forms
    if not (explicit or len(found) >= 2 or wants_sights or _SUPERLATIVE.search(text)):
        return None
    found.sort(key=lambda item: item[0])
    metrics = [m for _, m, _ in found]
    if "paved" in metrics and "unpaved" in metrics:
        return None  # contradictory: leave it to the person to say what they mean
    goals = [
        {"metric": metric, "weight": _WEIGHTS[min(i, len(_WEIGHTS) - 1)]}
        for i, metric in enumerate(metrics[:4])
    ]
    rest = text
    for _, _, match in sorted(found, key=lambda item: -item[2].start()):
        rest = rest[: match.start()] + " " + rest[match.end() :]
    rest = _GLUE.sub(" ", rest)
    rest = " ".join(rest.split())
    # An article left behind by a removed phrase ("with the quietest route" -> "the").
    rest = re.sub(
        r"(?:^|\s+)\b(?:the|a|an|my|some|past|along|via|with|for|and|but|on)\b(?=\s*(?:,|;|$))",
        "",
        rest,
        flags=re.I,
    )
    # ... or a bare noun for the route itself ("a quiet and smooth route").
    rest = re.sub(r"[\s,]*\b(?:a|an|the|my)?\s*(?:route|way|ride|trip)\s*$", "", rest, flags=re.I)
    rest = re.sub(r"\s*,\s*,+", ",", rest).strip(" ,;.")
    return Goals(spec={"goals": goals}, rest=rest, explicit=explicit, metrics=metrics[:4])
