"""When a ride starts, read from a message: relative days, weekdays, clock times, day parts."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bike_routing_agent.chat.rules import parse_one_line
from bike_routing_agent.chat.when import parse_departure

BERLIN = ZoneInfo("Europe/Berlin")
WED_NOON = datetime(2026, 10, 7, 12, 0, tzinfo=BERLIN)  # a Wednesday


def at(day: int, hour: int, minute: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=BERLIN)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("tomorrow at 8", at(8, 8)),
        ("tomorrow at 8:30", at(8, 8, 30)),
        ("tomorrow morning", at(8, 8)),
        ("tomorrow afternoon", at(8, 14)),
        ("tomorrow evening", at(8, 18)),
        ("tomorrow", at(8, 9)),  # a date without a time
        ("today at 5pm", at(7, 17)),
        ("today at 17:00", at(7, 17)),
        ("tonight", at(7, 20)),
        ("the day after tomorrow at 10", at(9, 10)),
        ("on Saturday morning", at(10, 8)),
        ("on saturday", at(10, 9)),
        ("this weekend", at(10, 9)),
        ("next friday 7pm", at(16, 19)),  # Friday the 9th has not "next" in it: the one after
        ("on Friday at 9", at(9, 9)),
        ("on Wednesday at 9", at(14, 9)),  # today's 9 has passed: next week's
        ("in 3 days", at(10, 9)),
        ("on 2026-10-15 at 08:30", at(15, 8, 30)),
        ("leaving at 9:30", at(8, 9, 30)),  # 9:30 has passed today (it is noon): tomorrow
        ("starting at 3pm", at(7, 15)),  # still ahead today
        ("at 8 o'clock", at(8, 8)),
        ("8am", at(8, 8)),
        ("at noon", at(7, 12) if False else at(8, 12)),  # noon is now: passed, so tomorrow
    ],
)
def test_departures(text, expected):
    departure, rest = parse_departure(f"Goslar to Hannover {text}", WED_NOON)
    assert departure == expected, (text, departure)
    assert rest == "Goslar to Hannover", rest  # the words are taken out


@pytest.mark.parametrize(
    "text",
    [
        "Goslar to Hannover",
        "Goslar to Hannover at 80 km/h",
        "Goslar to Hannover 40 km",
        "Goslar to Hannover with 300 m climbing",
        "loop of 2 hours from Goslar",
        "on 2026-02-31",
        "Sunday River to Bethel",
        "Wednesday Street to Friday Lane",
    ],
)
def test_text_without_a_time_is_left_alone(text):
    assert parse_departure(text, WED_NOON) == (None, text)


def test_a_one_line_request_carries_the_departure_with_the_users_zone():
    line = parse_one_line("Goslar to Hannover tomorrow at 8", now=WED_NOON)
    assert line.departure == at(8, 8) and line.departure.utcoffset().total_seconds() == 7200
    assert (line.origin, line.destination) == ("Goslar", "Hannover")
    assert parse_one_line("Goslar to Hannover tomorrow at 8").departure is None  # no clock given
