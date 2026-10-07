"""The OpenAI-compatible transport, against an in-process fake server."""

import json

import httpx
import pytest

from bike_routing_agent.llm.backends import OpenAICompatBackend, extract_json_text
from bike_routing_agent.llm.errors import LLMParseError
from bike_routing_agent.llm.parser import RouteRequestParser

REPLY = {
    "origin": "Bremen",
    "destination": "Hamburg",
    "via": [],
    "constraints": {
        "bike_type": None,
        "target_distance_km": None,
        "max_distance_km": None,
        "max_ascent_m": None,
        "prefer_surfaces": [],
        "avoid_surfaces": [],
        "avoid_high_traffic_roads": None,
        "avoid_ferries": None,
        "return_to_origin": None,
        "loop_direction": None,
    },
    "departure_time": None,
    "notes": [],
}


def _backend(handler, **kwargs):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatBackend(
        base_url="https://llm.example/v1/", model="alias-large", http_client=http, **kwargs
    )


def _chat(content, finish="stop", usage=None):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 11, "completion_tokens": 7},
    }


def test_request_shape_and_token_usage():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat(json.dumps(REPLY)))

    done = _backend(handler, api_key="sk-x").complete("SYS", [{"role": "user", "content": "hi"}])
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-x"
    body = seen["body"]
    assert body["model"] == "alias-large" and body["temperature"] == 0
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"].startswith("SYS")
    assert '"additionalProperties"' in body["messages"][0]["content"]  # schema is in the prompt
    assert body["messages"][1] == {"role": "user", "content": "hi"}
    assert (done.input_tokens, done.output_tokens) == (11, 7)


def test_no_key_means_no_authorization_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_chat("{}"))

    _backend(handler).complete("s", [])
    assert seen["auth"] is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('\n\n{"a": 1}', '{"a": 1}'),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('<think>hmm {not json}</think>\n{"a": 1}', '{"a": 1}'),
        ('Sure! Here it is: {"a": 1} Hope that helps.', '{"a": 1}'),
        ("no json at all", "no json at all"),
    ],
)
def test_json_is_extracted_from_chatty_replies(raw, expected):
    assert extract_json_text(raw) == expected


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(500), "llm_parser_error"),
        (httpx.Response(200, json={"choices": []}), "llm_parser_invalid_output"),
        (httpx.Response(200, json=_chat("{}", finish="length")), "llm_parser_invalid_output"),
        (httpx.Response(200, json=_chat("", finish="content_filter")), "llm_parser_declined"),
    ],
)
def test_failures_map_to_stable_codes(response, code):
    with pytest.raises(LLMParseError) as err:
        _backend(lambda request: response).complete("s", [])
    assert err.value.code == code


def test_timeouts_and_connection_errors():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def down(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(LLMParseError) as err:
        _backend(slow).complete("s", [])
    assert err.value.code == "llm_parser_timeout"
    with pytest.raises(LLMParseError) as err:
        _backend(down).complete("s", [])
    assert err.value.code == "llm_parser_error"


def test_full_parse_through_the_openai_backend_with_a_repair():
    replies = iter(["not json", f"```json\n{json.dumps(REPLY)}\n```"])

    def handler(request):
        return httpx.Response(200, json=_chat(next(replies)))

    parser = RouteRequestParser(model="alias-large", backend=_backend(handler))
    result = parser("Bremen to Hamburg")
    assert (result["origin"], result["destination"]) == ("Bremen", "Hamburg")
    assert result["provenance"]["repaired"] is True
    assert result["provenance"]["input_tokens"] == 22


def test_transient_errors_are_retried_once_then_reported():
    waits = []
    calls = iter([429, 200])

    def handler(request):
        if next(calls) == 429:
            return httpx.Response(429, headers={"retry-after": "3"})
        return httpx.Response(200, json=_chat(json.dumps(REPLY)))

    backend = _backend(handler, sleep=waits.append)
    assert backend.complete("s", []).text.startswith("{")
    assert waits == [3.0]

    waits.clear()
    with pytest.raises(LLMParseError) as err:
        _backend(lambda request: httpx.Response(503), sleep=waits.append).complete("s", [])
    assert err.value.code == "llm_parser_error"
    assert waits == [1.0, 2.0]  # two retries, then give up


def test_client_errors_and_timeouts_are_not_retried():
    waits = []
    attempts = []

    def forbidden(request):
        attempts.append(1)
        return httpx.Response(401)

    with pytest.raises(LLMParseError):
        _backend(forbidden, sleep=waits.append).complete("s", [])
    assert len(attempts) == 1 and waits == []
