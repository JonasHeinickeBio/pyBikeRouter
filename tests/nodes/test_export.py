"""Unit tests for the explain_and_export node."""

from xml.etree import ElementTree as ET

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
