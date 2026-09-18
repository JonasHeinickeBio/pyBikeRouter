"""Unit tests for the parse_request node (graph-level paths are covered in
tests/graph/test_graph.py; these target the node in isolation)."""

from bike_routing_agent.nodes.parse import build_parse_node


def test_structured_place_strings_are_reshaped_into_queries():
    node = build_parse_node()

    update = node(
        {
            "raw_input": {
                "origin": "Braunschweig",
                "destination": "Wolfenbuettel",
                "constraints": {"bike_type": "gravel"},
            }
        }
    )

    assert update["origin_input"] == {"query": "Braunschweig"}
    assert update["destination_input"] == {"query": "Wolfenbuettel"}
    assert update["constraints"] == {"bike_type": "gravel"}
    assert update["status"] == "in_progress"


def test_coordinate_dicts_bypass_query_shape():
    node = build_parse_node()

    update = node(
        {
            "raw_input": {
                "origin": {"lon": 10.5, "lat": 52.3},
                "destination": {"lon": 10.6, "lat": 52.4},
            }
        }
    )

    assert update["origin_input"] == {"coordinate": {"lon": 10.5, "lat": 52.3}}
    assert update["destination_input"] == {"coordinate": {"lon": 10.6, "lat": 52.4}}


def test_via_entries_are_reshaped_and_unsupported_shapes_dropped():
    node = build_parse_node()

    update = node(
        {
            "raw_input": {
                "origin": "A",
                "destination": "B",
                "via": ["Stop One", {"lon": 1.0, "lat": 2.0}, 42],
            }
        }
    )

    assert update["via_inputs"] == [
        {"query": "Stop One"},
        {"coordinate": {"lon": 1.0, "lat": 2.0}},
    ]


def test_missing_constraints_default_to_empty_dict():
    node = build_parse_node()

    update = node({"raw_input": {"origin": "A", "destination": "B"}})

    assert update["constraints"] == {}


def test_free_text_without_llm_parser_is_invalid():
    node = build_parse_node()

    update = node({"raw_input": {"text": "ride from Braunschweig to Wolfenbuettel"}})

    assert update["status"] == "invalid"
    assert update["errors"][0]["code"] == "nl_parsing_unavailable"


def test_free_text_is_routed_through_injected_llm_parser():
    calls: list[str] = []

    def fake_parser(text: str) -> dict:
        calls.append(text)
        return {
            "origin": "Braunschweig",
            "destination": "Wolfenbuettel",
            "constraints": {"target_distance_km": 30},
        }

    node = build_parse_node(llm_parser=fake_parser)

    update = node({"raw_input": {"text": "ride from Braunschweig to Wolfenbuettel"}})

    assert calls == ["ride from Braunschweig to Wolfenbuettel"]
    assert update["origin_input"] == {"query": "Braunschweig"}
    assert update["constraints"] == {"target_distance_km": 30}
    assert update["status"] == "in_progress"


def test_structured_input_takes_precedence_over_text_when_both_present():
    node = build_parse_node(llm_parser=lambda text: {"origin": "never", "destination": "used"})

    update = node({"raw_input": {"text": "ignored", "origin": "A", "destination": "B"}})

    assert update["origin_input"] == {"query": "A"}
