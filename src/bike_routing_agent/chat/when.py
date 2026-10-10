"""When a ride starts, read from a chat message ("tomorrow at 8", "on Saturday morning", "9:30").

Rules, not guesses: a phrase this module does not recognise is left in the text, and what it
did read is removed from the text so the rest can be read as places. Times are in the
caller's time zone (``now`` carries it).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_WEEKDAY = "|".join(WEEKDAYS)
# Hours the day parts stand for when no clock time is given.
DAY_PARTS = {"morning": 8, "noon": 12, "midday": 12, "afternoon": 14, "evening": 18, "tonight": 20}
DEFAULT_HOUR = 9  # a date without a time

_DAY_WORDS = re.compile(
    rf"""\b(?:
        (?P<after>day\ after\ tomorrow)
      | (?P<tomorrow>tomorrow)
      | (?P<today>today)
      | (?P<weekend>(?:this|on\ the|at\ the|over\ the)\ weekend)
      | (?P<in_days>in\ (?P<n_days>\d{{1,2}})\ days?)
      | (?:(?P<next>next|this|coming|on|for)\s+)?(?P<weekday>{_WEEKDAY})
      | (?P<iso>(?P<y>\d{{4}})-(?P<mo>\d{{2}})-(?P<d>\d{{2}}))
    )\b""",
    re.I | re.X,
)
_CLOCK_AMPM = re.compile(
    r"\b(?:(?:at|around|about|from|starting|leaving|departing|start)\s+)*"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)(?![a-z])",
    re.I,
)
_CLOCK_24H = re.compile(
    r"\b(?:(?:at|around|about|from|starting|leaving|departing|start)\s+)+"
    r"(?P<h>\d{1,2})(?!\d)(?::(?P<m>\d{2}))?(?!\s*(?:km|kilomet|m\b|min|hours?|hrs?|h\b|%|/))"
    r"(?:\s*(?:o'?clock|h\b|uhr))?",
    re.I,
)
_CLOCK_COLON = re.compile(r"(?<![\d:.])(?P<h>\d{1,2}):(?P<m>\d{2})(?![\d:])")
_OCLOCK = re.compile(r"\b(?P<h>\d{1,2})\s*o'?clock\b", re.I)
_PART = re.compile(
    r"\b(?:in\s+the\s+|this\s+|early\s+)?(?P<part>morning|afternoon|evening|noon|midday|tonight)\b",
    re.I,
)


def _next_weekday(now: datetime, target: int, *, following_week: bool) -> datetime:
    """The coming ``target`` weekday (today counts); "next Friday" is the one in the week after
    this one."""
    days = (target - now.weekday()) % 7
    if following_week and days < 7 - now.weekday():
        days += 7
    return now + timedelta(days=days)


_LEADING_PREPOSITION = re.compile(
    r"\s(?:on|at|for|around|about|from|starting|start|leaving|departing|by|the)\s*$", re.I
)


def _cut(text: str, span: tuple[int, int]) -> str:
    """Text without the span -- and without the preposition that led into it ("on Saturday")."""
    head = _LEADING_PREPOSITION.sub("", text[: span[0]])
    return (head + " " + text[span[1] :]).strip()


def parse_departure(text: str, now: datetime) -> tuple[datetime | None, str]:
    """``(departure, text without the words that gave it)``; ``None`` when no time was said."""
    date: datetime | None = None
    hour: int | None = None
    minute = 0
    rest = text

    day = _DAY_WORDS.search(rest)
    if day:
        if day.group("after"):
            date = now + timedelta(days=2)
        elif day.group("tomorrow"):
            date = now + timedelta(days=1)
        elif day.group("today"):
            date = now
        elif day.group("weekend"):
            # The coming Saturday; on a Saturday or Sunday, today.
            date = now if now.weekday() >= 5 else _next_weekday(now, 5, following_week=False)
        elif day.group("in_days"):
            date = now + timedelta(days=int(day.group("n_days")))
        elif day.group("iso"):
            try:
                date = now.replace(
                    year=int(day.group("y")), month=int(day.group("mo")), day=int(day.group("d"))
                )
            except ValueError:
                date = None  # 2026-02-31: not a date; leave the words alone
        elif day.group("weekday"):
            qualifier = (day.group("next") or "").lower()
            if qualifier in ("on", "for", "next", "this", "coming") or re.search(
                r"\b(?:morning|afternoon|evening|at|noon)\b", rest[day.end() : day.end() + 20], re.I
            ):
                date = _next_weekday(
                    now, WEEKDAYS[day.group("weekday").lower()], following_week=qualifier == "next"
                )
        if date is not None:
            rest = _cut(rest, day.span())

    clock = (
        _CLOCK_AMPM.search(rest)
        or _CLOCK_24H.search(rest)
        or _OCLOCK.search(rest)
        or _CLOCK_COLON.search(rest)
    )
    if clock:
        h, m = int(clock.group("h")), int(clock.groupdict().get("m") or 0)
        ap = (clock.groupdict().get("ap") or "").lower().replace(".", "")
        if ap:
            if not 1 <= h <= 12:
                clock = None
            else:
                h = h % 12 + (12 if ap == "pm" else 0)
        if clock and 0 <= h <= 23 and 0 <= m <= 59:
            hour, minute = h, m
            rest = _cut(rest, clock.span())
    if hour is None:
        part = _PART.search(rest)
        if part:
            hour = DAY_PARTS[part.group("part").lower()]
            rest = _cut(rest, part.span())
            if part.group("part").lower() == "tonight" and date is None:
                date = now
    elif _PART.search(rest):  # "tomorrow at 8 in the morning": the day part adds nothing
        rest = _cut(rest, _PART.search(rest).span())  # type: ignore[union-attr]

    if date is None and hour is None:
        return None, text
    explicit_date = date is not None
    departure = (date or now).replace(
        hour=DEFAULT_HOUR if hour is None else hour, minute=minute, second=0, microsecond=0
    )
    if not explicit_date and departure <= now:
        departure += timedelta(days=1)  # "at 8" at 10 o'clock means tomorrow's 8
    elif departure <= now and day is not None and (day.group("weekday") or day.group("weekend")):
        departure += timedelta(days=7)  # "Saturday morning", asked on Saturday afternoon
    return departure, rest
