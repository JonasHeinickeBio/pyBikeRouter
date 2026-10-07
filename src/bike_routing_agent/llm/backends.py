"""Model transports for the request parser.

The parser (``parser.py``) owns the prompt, validation and repair logic; a
backend only sends one conversation to a model and hands back the text:

* ``AnthropicBackend`` -- the Anthropic Messages API with structured outputs
  (the JSON schema is enforced by the API).
* ``OpenAICompatBackend`` -- any OpenAI-compatible ``/chat/completions`` server
  (Helmholtz Blablador, vLLM, Ollama, ...). These do not all support schema-
  constrained output, so the schema travels in the system prompt and the reply
  is validated by the parser exactly like any other.

Both raise ``LLMParseError`` with the stable codes the API documents.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from bike_routing_agent.llm.errors import LLMParseError
from bike_routing_agent.llm.schema import OUTPUT_SCHEMA

logger = logging.getLogger(__name__)

Messages = list[dict[str, Any]]


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMBackend(Protocol):
    def complete(self, system: str, messages: Messages) -> Completion: ...


def _is_timeout(exc: Exception) -> bool:
    return "timeout" in type(exc).__name__.lower() or isinstance(exc, TimeoutError)


class AnthropicBackend:
    def __init__(self, *, client: Any, model: str, max_output_tokens: int) -> None:
        self._client = client
        self._model = model
        self._max_output_tokens = max_output_tokens

    @staticmethod
    def build_client(api_key: str | None, timeout_s: float) -> Any:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise RuntimeError(
                "LLM_PROVIDER=anthropic needs the Anthropic SDK; install the llm extra: "
                "pip install 'bike-routing-agent[llm]'"
            ) from exc
        # With no key given the SDK resolves credentials itself (environment,
        # auth token, profile). Retries stay at the SDK default for transient errors.
        kwargs: dict[str, Any] = {"timeout": timeout_s}
        if api_key:
            kwargs["api_key"] = api_key
        return anthropic.Anthropic(**kwargs)

    def complete(self, system: str, messages: Messages) -> Completion:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_output_tokens,
                system=system,
                messages=messages,
                output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            )
        except Exception as exc:
            logger.warning("llm parser request failed: %s: %s", type(exc).__name__, exc)
            if _is_timeout(exc):
                raise LLMParseError("llm_parser_timeout", "the language model timed out") from exc
            raise LLMParseError(
                "llm_parser_error", "the language model could not be reached"
            ) from exc

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            raise LLMParseError("llm_parser_declined", "the language model declined this request")
        if stop == "max_tokens":
            raise LLMParseError(
                "llm_parser_invalid_output", "the language model's answer was cut off"
            )
        text = ""
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                text = str(block.text)
                break
        return Completion(text, input_tokens, output_tokens)


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def extract_json_text(content: str) -> str:
    """The JSON object in a chat reply that may carry reasoning or code fences."""
    text = _THINK.sub("", content).strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    if not text.startswith("{") and "{" in text and "}" in text:
        # Prose around the object: take the outermost braces.
        text = text[text.index("{") : text.rindex("}") + 1]
    return text


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """Honour a numeric Retry-After (capped at 10 s), else back off 1 s, 2 s, ..."""
    try:
        return min(max(float(response.headers.get("retry-after", "")), 0.0), 10.0)
    except ValueError:
        return float(min(2 ** (attempt - 1), 10))


class OpenAICompatBackend:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_s: float = 30.0,
        max_output_tokens: int = 8000,
        max_retries: int = 2,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = http_client or httpx.Client(timeout=timeout_s)
        self._max_retries = max_retries
        self._sleep = sleep

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST with a bounded retry on rate limits and 5xx (never on timeouts or 4xx)."""
        attempt = 0
        while True:
            try:
                response = self._http.post(self._url, json=body, headers=self._headers)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("response is not a JSON object")
                return data
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if attempt < self._max_retries and (code == 429 or code >= 500):
                    attempt += 1
                    wait = _retry_delay(exc.response, attempt)
                    logger.info("llm parser got HTTP %s; retrying in %.1fs", code, wait)
                    self._sleep(wait)
                    continue
                raise

    def complete(self, system: str, messages: Messages) -> Completion:
        schema_note = (
            "\n\nReply with ONLY a JSON object (no prose, no code fences) matching this "
            "JSON schema:\n" + json.dumps(OUTPUT_SCHEMA)
        )
        body = {
            "model": self._model,
            "temperature": 0,
            "max_tokens": self._max_output_tokens,
            "messages": [{"role": "system", "content": system + schema_note}, *messages],
        }
        try:
            data = self._post(body)
        except Exception as exc:
            # The message of an httpx error can echo the URL; never the key.
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            logger.warning(
                "llm parser request failed: %s%s",
                type(exc).__name__,
                f" (HTTP {status})" if status else "",
            )
            if _is_timeout(exc):
                raise LLMParseError("llm_parser_timeout", "the language model timed out") from exc
            raise LLMParseError(
                "llm_parser_error", "the language model could not be reached"
            ) from exc

        try:
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMParseError(
                "llm_parser_invalid_output", "the language model sent an unexpected response"
            ) from exc
        finish = choice.get("finish_reason")
        if finish == "content_filter":
            raise LLMParseError("llm_parser_declined", "the language model declined this request")
        if finish == "length":
            raise LLMParseError(
                "llm_parser_invalid_output", "the language model's answer was cut off"
            )
        usage = data.get("usage") or {}
        return Completion(
            extract_json_text(str(content)),
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )
