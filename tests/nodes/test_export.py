"""Unit tests for the explain_and_export node."""

from xml.etree import ElementTree as ET

import pytest

from bike_routing_agent.nodes.export import build_export_node

GPX_NS = "{http://www.topografix.com/GPX/1/1}"


def selected_candidate(**overrides):
    candidate = {
        "provider": "ors",
        "provider_profile": "cycling-regular",
        "geometry_geojson": {
            "type": "LineString",
            "coordinates": [[10.5, 52.3, 70.0], [10.6, 52.4, 85.0]],
        },
        "metrics": {
            "distance_m": 21_400,
            "duration_s": 3600,
            "ascent_m": 180,
            "descent_m": 175,
        },
        "score": 0.9,
        "score_breakdown": {"distance_fit": 1.0, "elevation_fit": 0.91, "warning_penalty": 0.0},
        "warnings": ["steep section"],
        "provenance": {"provider": "ors"},
    }
    candidate.update(overrides)
    return candidate


def test_missing_selection_is_structured_no_route(tmp_path):
    node = build_export_node(export_dir=tmp_path)

    update = node({"selected_candidate": None})

    assert update["status"] == "no_route"
    assert update["errors"][0]["code"] == "no_candidates"
    assert list(tmp_path.iterdir()) == []


def test_artifacts_are_written_and_named_consistently(tmp_path):
    node = build_export_node(export_dir=tmp_path)

    update = node({"selected_candidate": selected_candidate()})

    assert update["status"] == "ready"
    geojson_name = update["artifacts"]["geojson_file"]
    gpx_name = update["artifacts"]["gpx_file"]
    assert geojson_name.endswith(".geojson")
    assert gpx_name.endswith(".gpx")
    assert (tmp_path / geojson_name).is_file()
    assert (tmp_path / gpx_name).is_file()
    # The GPX <name> must match the artifact id so downloads stay traceable.
    root = ET.fromstring((tmp_path / gpx_name).read_text().split("\n", 1)[1])
    name = root.find(f"{GPX_NS}trk/{GPX_NS}name")
    assert name is not None and name.text == gpx_name.removesuffix(".gpx")


def test_explanation_uses_only_candidate_facts_and_hedges_safety(tmp_path):
    node = build_export_node(export_dir=tmp_path)

    update = node({"selected_candidate": selected_candidate()})

    explanation = update["explanation"]
    assert "21.4 km" in explanation
    assert "ors" in explanation and "cycling-regular" in explanation
    assert "180 m of ascent" in explanation
    assert "steep section" in explanation
    assert "not a guarantee of safety" in explanation


def test_explanation_surfaces_uncertainty_notes(tmp_path):
    candidate = selected_candidate()
    candidate["metrics"]["ascent_m"] = None
    candidate["metrics"]["unknown_surface_fraction"] = 0.4

    node = build_export_node(export_dir=tmp_path)
    update = node({"selected_candidate": candidate})

    explanation = update["explanation"]
    assert "elevation data was not available" in explanation
    assert "40%" in explanation
    # With no ascent the sentence about metres of ascent must be absent.
    assert "m of ascent" not in explanation


def test_export_dir_is_created_on_demand(tmp_path):
    nested = tmp_path / "deep" / "exports"
    node = build_export_node(export_dir=nested)

    update = node({"selected_candidate": selected_candidate()})

    assert (nested / update["artifacts"]["geojson_file"]).is_file()


# ---------------------------------------------------------------- loops (issue #5)


def test_explanation_describes_a_synthesized_loop(tmp_path):
    node = build_export_node(export_dir=tmp_path)

    update = node(
        {
            "selected_candidate": selected_candidate(),
            "constraints": {"return_to_origin": True, "target_distance_km": 15},
            "loop_plan": {
                "vias": [],
                "radius_m": 2887.0,
                "reach_m": 5000.0,
                "direction": "clockwise",
                "sides": 3,
            },
        }
    )

    explanation = update["explanation"]
    assert "loop back to the start" in explanation
    assert "waypoints were synthesized" in explanation
    assert "5.0 km out" in explanation


def test_explanation_notes_caller_drawn_loop_waypoints(tmp_path):
    node = build_export_node(export_dir=tmp_path)

    update = node(
        {
            "selected_candidate": selected_candidate(),
            "constraints": {"return_to_origin": True, "target_distance_km": 15},
            "loop_plan": None,
        }
    )

    assert "waypoints you supplied" in update["explanation"]


def test_non_loop_explanation_carries_no_loop_language(tmp_path):
    node = build_export_node(export_dir=tmp_path)

    update = node({"selected_candidate": selected_candidate(), "constraints": {}})

    assert "loop" not in update["explanation"].lower()


class MemoryStore:
    def __init__(self):
        self.data: dict[str, str] = {}

    def put(self, name, content):
        self.data[name] = content

    def get(self, name):
        return self.data[name].encode() if name in self.data else None


def test_export_writes_through_any_artifact_store_and_reports_the_route_id():
    store = MemoryStore()
    node = build_export_node(artifact_store=store)

    update = node({"selected_candidate": selected_candidate()})

    route_id = update["route_id"]
    assert update["artifacts"] == {
        "geojson_file": f"{route_id}.geojson",
        "gpx_file": f"{route_id}.gpx",
    }
    assert set(store.data) == {f"{route_id}.geojson", f"{route_id}.gpx"}


def test_export_requires_exactly_one_destination(tmp_path):
    with pytest.raises(ValueError, match="exactly one"):
        build_export_node()
    with pytest.raises(ValueError, match="exactly one"):
        build_export_node(export_dir=tmp_path, artifact_store=MemoryStore())


# ------------------------------------------------------- weather in the explanation


def _weather(**summary):
    from datetime import UTC, datetime

    from bike_routing_agent.weather.models import RouteWeather, WeatherSummary

    stamp = datetime(2026, 10, 7, 14, 0, tzinfo=UTC)
    return RouteWeather(
        provider="open-meteo",
        attribution="Weather data by Open-Meteo.com (CC BY 4.0)",
        departure=stamp,
        arrival=stamp,
        duration_source="provider",
        retrieved_at=stamp,
        samples=[],
        summary=WeatherSummary(**summary),
        advisories=["Wind gusts up to 62 km/h are forecast."],
    )


def test_explanation_states_the_forecast_as_facts_with_its_source():
    from bike_routing_agent.nodes.export import _weather_sentences

    sentences = _weather_sentences(
        _weather(
            temperature_min_c=9.4,
            temperature_max_c=13.6,
            precipitation_probability_max=40,
            wind_speed_max_kmh=31,
            wind_gust_max_kmh=62,
            headwind_mean_kmh=11,
        )
    )
    assert sentences[0] == (
        "Forecast for a 07 Oct 14:00 UTC departure (open-meteo): 9 to 14 °C, up to 40% chance "
        "of precipitation, wind up to 31 km/h (gusts 62), about 11 km/h of headwind on average."
    )
    assert sentences[1] == "Forecast notes: Wind gusts up to 62 km/h are forecast."
    assert "safe" not in " ".join(sentences).lower()


def test_a_merged_forecast_names_every_source_in_the_explanation():
    from bike_routing_agent.nodes.export import _weather_sentences

    weather = _weather(temperature_min_c=9.0, temperature_max_c=12.0).model_copy(
        update={"provider": "dwd", "sources": ["dwd", "open-meteo"]}
    )
    assert "(dwd + open-meteo)" in _weather_sentences(weather)[0]


def test_explanation_leaves_out_a_negligible_wind_direction_and_empty_forecasts():
    from bike_routing_agent.nodes.export import _weather_sentences

    mild = _weather_sentences(
        _weather(temperature_min_c=12, temperature_max_c=12, headwind_mean_kmh=2)
    )
    assert "12 °C" in mild[0] and "headwind" not in mild[0] and "tailwind" not in mild[0]
    assert "tailwind" in _weather_sentences(_weather(headwind_mean_kmh=-9, temperature_min_c=1))[0]
    assert _weather_sentences(_weather()) == []
