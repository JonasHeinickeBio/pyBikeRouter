"""The pure presentation helpers in frontend/weather.js, run under node.

They need no DOM, so a small node script exercises them directly; the tests are
skipped where node is not installed (CI's frontend job has it).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_js(expression: str):
    script = (
        f"const W = require({json.dumps(str(FRONTEND / 'weather.js'))});\n"
        f"process.stdout.write(JSON.stringify({expression}));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


SAMPLE = {
    "fraction": 0,
    "lon": 10,
    "lat": 52,
    "time": "2026-10-07T12:00:00Z",
    "weather": {
        "condition": "rain",
        "is_day": True,
        "temperature_c": 9.6,
        "wind_speed_kmh": 24.4,
        "wind_from_deg": 270,
        "precipitation_probability": 70,
    },
}


def weather(**summary):
    return {
        "provider": "open-meteo",
        "attribution": "Weather data by Open-Meteo.com (CC BY 4.0)",
        "departure": "2026-10-07T12:00:00Z",
        "duration_source": "provider",
        "samples": [SAMPLE],
        "advisories": ["Wind gusts up to 62 km/h are forecast."],
        "summary": {
            "temperature_min_c": 9,
            "temperature_max_c": 14,
            "precipitation_probability_max": 70,
            "wind_speed_max_kmh": 31,
            "wind_gust_max_kmh": 62,
            "headwind_mean_kmh": 11,
            "headwind_share": 0.4,
            "tailwind_share": 0.2,
            "worst_condition": "rain",
            **summary,
        },
    }


def test_compass_names_and_wraparound():
    names = run_js("[0, 22, 45, 90, 180, 270, 337, 359, 360, -90].map(W.compass)")
    assert names == ["N", "NNE", "NE", "E", "S", "W", "NNW", "N", "N", "W"]


def test_circular_mean_does_not_average_350_and_10_to_180():
    assert run_js("W.circularMean([350, 10])") == pytest.approx(0, abs=1e-6) or run_js(
        "W.circularMean([350, 10])"
    ) == pytest.approx(360, abs=1e-6)
    assert run_js("W.circularMean([90, 90])") == pytest.approx(90)
    assert run_js("W.circularMean([0, 180])") is None  # opposing winds cancel
    assert run_js("W.circularMean([])") is None
    assert run_js("W.circularMean([null, 'x', 45])") == pytest.approx(45)


def test_wind_arrow_points_where_the_wind_blows_to():
    svg = run_js("W.windArrow(0, 20)")  # a north wind blows toward the south
    assert "rotate(180deg)" in svg and 'aria-label="wind from N"' in svg
    assert "rotate(90deg)" in run_js("W.windArrow(270)")  # a west wind blows east
    assert run_js("W.windArrow(null)") == ""


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ({"temperature_min_c": 9.4, "temperature_max_c": 13.6}, "9–14 °C"),
        ({"temperature_min_c": 12, "temperature_max_c": 12.2}, "12 °C"),
        ({"temperature_min_c": None, "temperature_max_c": 5}, None),
        ({}, None),
    ],
)
def test_temperature_range(summary, expected):
    assert run_js(f"W.temperatureRange({json.dumps(summary)})") == expected


@pytest.mark.parametrize(
    ("headwind", "kind"),
    [(12, "head"), (-9, "tail"), (1.5, "calm"), (-2.9, "calm"), (None, None)],
)
def test_wind_versus_route(headwind, kind):
    result = run_js(f"W.windVsRoute({json.dumps(headwind)})")
    assert (result["kind"] if result else None) == kind


def test_night_and_unknown_conditions():
    assert run_js("W.conditionInfo('clear', false).icon") != run_js(
        "W.conditionInfo('clear', true).icon"
    )
    assert run_js("W.conditionInfo('rain', true).label") == "Rain"
    assert run_js("W.conditionInfo('???').label") == "No conditions reported"


def test_the_card_states_forecast_facts_and_credits_the_source():
    html = run_js(f"W.weatherCardHtml({json.dumps(weather())}, 'ok')")
    assert "9–14 °C" in html and "up to 70%" in html
    assert "31 km/h from W, gusts 62" in html
    assert "11 km/h headwind on average" in html
    assert "Wind gusts up to 62 km/h are forecast." in html
    assert "Weather data by Open-Meteo.com (CC BY 4.0)" in html
    assert re.search(r'<a href="https://open-meteo\.com/"', html)
    assert 'class="windbar"' in html and "headwind on 40% of the route, tailwind on 20%" in html
    assert 'aria-label="Forecast along the route"' in html
    assert "safe" not in html.lower()


def test_estimated_durations_are_called_out():
    html = run_js(f"W.weatherCardHtml({json.dumps(weather())}, 'ok')")
    assert "estimated" not in html
    estimated = weather()
    estimated["duration_source"] = "estimated"
    assert "estimated from the distance" in run_js(
        f"W.weatherCardHtml({json.dumps(estimated)}, 'ok')"
    )


def test_html_in_provider_text_cannot_inject_markup():
    hostile = weather()
    hostile["advisories"] = ['<img src=x onerror="alert(1)">']
    hostile["attribution"] = "<script>alert(1)</script>"
    html = run_js(f"W.weatherCardHtml({json.dumps(hostile)}, 'ok')")
    assert "<img" not in html and "<script>" not in html and "&lt;img" in html


def test_missing_weather_explains_itself_only_when_something_went_wrong():
    assert run_js("W.weatherCardHtml(null, null)") == ""  # weather switched off: say nothing
    assert "could not be loaded" in run_js("W.weatherCardHtml(null, 'unavailable')")
    assert "does not cover this ride" in run_js("W.weatherCardHtml(null, 'not_covered')")
    assert run_js("W.weatherCardHtml(null, 'skipped')") == ""


def test_a_forecast_without_values_does_not_print_nan_or_undefined():
    empty = weather(
        temperature_min_c=None,
        temperature_max_c=None,
        precipitation_probability_max=None,
        wind_speed_max_kmh=None,
        wind_gust_max_kmh=None,
        headwind_mean_kmh=None,
        headwind_share=None,
        tailwind_share=None,
        worst_condition="unknown",
    )
    empty["samples"] = []
    html = run_js(f"W.weatherCardHtml({json.dumps(empty)}, 'ok')")
    assert "NaN" not in html and "undefined" not in html and "null" not in html


@pytest.mark.parametrize(
    ("headwind", "kind", "text"),
    [(12.4, "head", "▲ 12"), (-8.6, "tail", "▼ 9"), (0.5, "calm", "≈ 0")],
)
def test_the_candidate_table_wind_cell(headwind, kind, text):
    cell = run_js(f"W.windCell({{summary: {{headwind_mean_kmh: {headwind}}}}})")
    assert (cell["kind"], cell["text"]) == (kind, text)
    assert run_js("W.windCell(null)")["kind"] == "none"
    assert run_js("W.windCell({summary: {}})")["kind"] == "none"


def test_sample_tooltip_summarises_the_point():
    tip = run_js(f"W.sampleTooltip({json.dumps(SAMPLE)})")
    assert "Rain" in tip and "10 °C" in tip and "wind 24 km/h from W" in tip and "70% rain" in tip


def test_provider_credit_links_known_providers_and_plain_text_otherwise():
    assert 'href="https://open-meteo.com/"' in run_js(
        "W.providerCredit({provider: 'open-meteo', attribution: 'Weather data by Open-Meteo.com'})"
    )
    assert "<a" not in run_js("W.providerCredit({provider: 'other', attribution: 'By <b>x</b>'})")


def test_feels_like_only_when_it_differs_from_the_air():
    cold = {
        "temperature_min_c": 6,
        "temperature_max_c": 8,
        "apparent_temperature_min_c": 2.2,
        "apparent_temperature_max_c": 4.4,
    }
    assert run_js(f"W.feelsLike({json.dumps(cold)})") == "2–4 °C"
    same = {**cold, "apparent_temperature_min_c": 5.5, "apparent_temperature_max_c": 8.5}
    assert run_js(f"W.feelsLike({json.dumps(same)})") is None
    assert run_js("W.feelsLike({temperature_min_c: 5})") is None


def test_wet_stretch_text_says_where_and_when():
    stretch = {
        "from_km": 11.6,
        "to_km": 30.2,
        "from_time": "2026-10-07T12:00:00Z",
        "to_time": "2026-10-07T13:00:00Z",
        "whole_route": False,
    }
    text = run_js(f"W.wetStretchText({json.dumps(stretch)})")
    assert text.startswith("km 12–30 (") and "–" in text.split("(")[1]
    assert run_js(f"W.wetStretchText({json.dumps({**stretch, 'whole_route': True})})").startswith(
        "along the whole route"
    )
    unknown = {**stretch, "from_km": None, "to_km": None}
    assert run_js(f"W.wetStretchText({json.dumps(unknown)})").startswith("part of the route")
    assert run_js("W.wetStretchText(null)") is None


def test_the_card_lists_the_new_facts():
    weather = {
        "provider": "open-meteo",
        "attribution": "x",
        "departure": "2026-10-07T12:00:00Z",
        "duration_source": "provider",
        "samples": [],
        "advisories": [],
        "daylight": {"sunrise": "2026-10-07T05:29:00Z", "sunset": "2026-10-07T16:46:00Z"},
        "summary": {
            "worst_condition": "rain",
            "temperature_min_c": 6,
            "temperature_max_c": 8,
            "apparent_temperature_min_c": 1,
            "apparent_temperature_max_c": 3,
            "wet_stretch": {
                "from_km": 5,
                "to_km": 20,
                "from_time": "2026-10-07T12:00:00Z",
                "to_time": "2026-10-07T13:00:00Z",
                "whole_route": False,
            },
        },
    }
    html = run_js(f"W.weatherCardHtml({json.dumps(weather)}, 'ok')")
    assert "Feels like" in html and "Precipitation" in html and "Sunrise / sunset" in html
    assert run_js("W.daylightText(null)") is None
