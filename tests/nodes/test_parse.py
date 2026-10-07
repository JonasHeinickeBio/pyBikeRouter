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


# ----------------------------------------------- free text (issue #30)

import pytest  # noqa: E402

from bike_routing_agent.llm.parser import LLMParseError  # noqa: E402


def text_state(**raw):
    return {"raw_input": {"text": "a ride", **raw}}


def good_parse(text, **kwargs):
    return {
        "origin": "Braunschweig",
        "destination": "Wolfenbüttel",
        "via": ["Riddagshausen"],
        "constraints": {"bike_type": "gravel"},
        "departure_time": "2026-10-08T08:00:00+02:00",
        "notes": ["scenic is not expressible"],
        "provenance": {"parser": "llm", "model": "m", "prompt_version": "1"},
    }


def test_a_parsed_request_flows_on_with_places_as_queries_and_an_interpretation():
    update = build_parse_node(llm_parser=good_parse)(text_state())

    assert update["status"] == "in_progress"
    assert update["origin_input"] == {"query": "Braunschweig"}
    assert update["destination_input"] == {"query": "Wolfenbüttel"}
    assert update["via_inputs"] == [{"query": "Riddagshausen"}]
    assert update["constraints"] == {"bike_type": "gravel"}
    assert update["departure_time"] == "2026-10-08T08:00:00+02:00"
    assert update["interpretation"] == {
        "request": {
            "origin": "Braunschweig",
            "destination": "Wolfenbüttel",
            "via": ["Riddagshausen"],
            "constraints": {"bike_type": "gravel"},
        },
        "departure_time": "2026-10-08T08:00:00+02:00",
        "notes": ["scenic is not expressible"],
        "parser": {"parser": "llm", "model": "m", "prompt_version": "1"},
    }


def test_structured_requests_carry_no_interpretation():
    node = build_parse_node(llm_parser=good_parse)
    update = node({"raw_input": {"origin": "A", "destination": "B"}})
    assert "interpretation" not in update


def test_the_timezone_reaches_a_parser_only_when_given():
    seen = []

    def parser(text, **kwargs):
        seen.append(kwargs)
        return good_parse(text)

    node = build_parse_node(llm_parser=parser)
    node(text_state(timezone="Europe/Berlin"))
    node(text_state())
    assert seen == [{"timezone": "Europe/Berlin"}, {}]


def test_plain_one_argument_parsers_keep_working():
    node = build_parse_node(llm_parser=lambda text: good_parse(text))
    assert node(text_state())["status"] == "in_progress"


def test_max_alternatives_comes_from_the_request_never_from_the_parser():
    def greedy(text):
        return {**good_parse(text), "max_alternatives": 5}

    node = build_parse_node(llm_parser=greedy)
    assert node(text_state())["max_alternatives"] is None
    assert node(text_state(max_alternatives=2))["max_alternatives"] == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"origin": {"lon": 10.5, "lat": 52.2}, "destination": "B"},  # coordinates, not words
        {"origin": "A", "destination": {"lon": 10.5, "lat": 52.2}},
        {"origin": "A", "destination": "B", "via": [{"lon": 1, "lat": 2}]},
        {"origin": "", "destination": "B"},
        {"origin": "A", "destination": "B", "constraints": ["not", "a", "dict"]},
        {"origin": "A", "destination": "B", "via": "Hamburg"},
        ["a", "list"],
    ],
)
def test_the_graph_boundary_refuses_coordinates_and_malformed_parser_output(bad):
    update = build_parse_node(llm_parser=lambda text: bad)(text_state())
    assert update["status"] == "invalid"
    assert update["errors"][0]["code"] == "llm_parser_invalid_output"


def test_parser_errors_become_structured_invalid_results():
    def declined(text):
        raise LLMParseError("llm_parser_declined", "the language model declined this request")

    update = build_parse_node(llm_parser=declined)(text_state())
    assert update == {
        "status": "invalid",
        "errors": [
            {"code": "llm_parser_declined", "message": "the language model declined this request"}
        ],
    }


def test_an_unexpected_parser_crash_is_contained_without_leaking_its_message():
    def crash(text):
        raise RuntimeError("secret internal detail")

    update = build_parse_node(llm_parser=crash)(text_state())
    assert update["status"] == "invalid" and update["errors"][0]["code"] == "llm_parser_error"
    assert "secret" not in update["errors"][0]["message"]
