"""Free text -> structured route request, via the Anthropic API.

Contract (docs/llm-parser.md): words in, the same structured shape the API
already takes out -- *never* coordinates, geometry or metrics. Concretely:

* the model answers in a JSON schema (structured outputs) in which a place is
  a plain string and everything unstated is null;
* the answer is re-validated here (pydantic), and a place that looks like
  coordinates must appear verbatim in what the user typed, so a coordinate the
  model "knew" cannot slip through;
* the user's text is data inside ``<user_request>`` tags, never instructions;
* at most one repair attempt feeds the validation error back; after that the
  parse fails with a structured error code instead of a guess.

``anthropic`` is the optional ``llm`` extra, imported when the client is built.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from bike_routing_agent.llm.backends import AnthropicBackend, LLMBackend
from bike_routing_agent.llm.errors import LLMParseError
from bike_routing_agent.llm.schema import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    ParsedRequest,
)

logger = logging.getLogger(__name__)

# What a coordinate pair typed by a person looks like: "52.27, 10.52".
_COORDINATE = re.compile(r"-?\d{1,3}\.\d+\s*[,;\s]\s*-?\d{1,3}\.\d+")


def _normalise(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _coordinate_problems(parsed: ParsedRequest, user_text: str) -> list[str]:
    """Places that look like coordinates the user never typed."""
    typed = _normalise(user_text)
    problems: list[str] = []
    places = [parsed.origin, *(parsed.via), *([parsed.destination] if parsed.destination else [])]
    for place in places:
        for match in _COORDINATE.findall(place):
            if _normalise(match) not in typed:
                problems.append(
                    f"place {place!r} contains coordinates that are not in the user's text; "
                    "output the place name the user wrote, never coordinates"
                )
    return problems


def _departure_problems(parsed: ParsedRequest) -> list[str]:
    if parsed.departure_time is None:
        return []
    try:
        when = datetime.fromisoformat(parsed.departure_time.replace("Z", "+00:00"))
    except ValueError:
        return [f"departure_time {parsed.departure_time!r} is not ISO-8601"]
    if when.tzinfo is None:
        return [f"departure_time {parsed.departure_time!r} needs an explicit UTC offset"]
    return []


def _context_line(timezone: str | None, now: datetime) -> str:
    zone_name = "UTC"
    local = now.astimezone(UTC)
    if timezone:
        try:
            local = now.astimezone(ZoneInfo(timezone))
            zone_name = timezone
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return f"Current time: {local.isoformat(timespec='minutes')} (time zone {zone_name})."


def _error_summary(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:6]:
        where = ".".join(str(p) for p in error["loc"]) or "reply"
        parts.append(f"{where}: {error['msg']}")
    return "; ".join(parts)


class RouteRequestParser:
    """Callable ``(text, *, timezone=None) -> dict`` for ``build_parse_node``."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        timeout_s: float = 30.0,
        max_output_tokens: int = 8000,
        max_input_chars: int = 500,
        client: Any = None,
        backend: LLMBackend | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """``backend`` picks the model transport; without one, ``client`` (or a new
        Anthropic client built from ``api_key``) is wrapped in the Anthropic backend."""
        self._model = model
        self._max_input_chars = max_input_chars
        self._now = now
        if backend is None:
            if client is None:
                client = AnthropicBackend.build_client(api_key, timeout_s)
            backend = AnthropicBackend(
                client=client, model=model, max_output_tokens=max_output_tokens
            )
        self._backend = backend

    def __call__(self, text: str, *, timezone: str | None = None) -> dict[str, Any]:
        cleaned = text.strip()
        if not cleaned:
            raise LLMParseError("llm_parser_empty_input", "the request text is empty")
        if len(cleaned) > self._max_input_chars:
            raise LLMParseError(
                "llm_parser_input_too_long",
                f"the request text is longer than {self._max_input_chars} characters",
            )
        # The text must not be able to close its own data tags.
        safe_text = re.sub(r"</?\s*user_request[^>]*>", "", cleaned, flags=re.IGNORECASE)
        context = _context_line(timezone, self._now())
        user_message = f"{context}\n\n<user_request>\n{safe_text}\n</user_request>"
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

        usage = {"input_tokens": 0, "output_tokens": 0}
        problem = ""
        for attempt in range(2):  # the first answer, then at most one repair
            reply = self._ask(messages, usage)
            parsed, problem = self._validate(reply, cleaned)
            if parsed is not None:
                return self._result(parsed, usage, repaired=attempt == 1)
            logger.info("llm parse attempt %d rejected: %s", attempt + 1, problem)
            messages = [
                *messages,
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": (
                        f"Your reply was rejected: {problem}. Reply again with corrected JSON "
                        "for the same request, following the same rules."
                    ),
                },
            ]
        raise LLMParseError(
            "llm_parser_invalid_output",
            "the language model's answer could not be turned into a valid route request",
        )

    # -- one model call ---------------------------------------------------------------

    def _ask(self, messages: list[dict[str, Any]], usage: dict[str, int]) -> str:
        completion = self._backend.complete(SYSTEM_PROMPT, messages)
        usage["input_tokens"] += completion.input_tokens
        usage["output_tokens"] += completion.output_tokens
        return completion.text

    # -- validation ---------------------------------------------------------------------

    def _validate(self, reply: str, user_text: str) -> tuple[ParsedRequest | None, str]:
        try:
            data = json.loads(reply)
        except (TypeError, ValueError):
            return None, "the reply was not valid JSON"
        if not isinstance(data, dict):
            return None, "the reply must be a JSON object"
        if isinstance(data.get("origin"), str) and not data["origin"].strip():
            raw_notes = data.get("notes")
            notes: list[Any] = raw_notes if isinstance(raw_notes, list) else []
            detail = "; ".join(str(n) for n in notes[:3])
            raise LLMParseError(
                "llm_parser_no_route_request",
                "no route request was found in the text" + (f" ({detail})" if detail else ""),
            )
        try:
            parsed = ParsedRequest.model_validate(data)
        except ValidationError as exc:
            return None, _error_summary(exc)
        problems = _coordinate_problems(parsed, user_text) + _departure_problems(parsed)
        if problems:
            return None, "; ".join(problems)
        return parsed, ""

    def _result(
        self, parsed: ParsedRequest, usage: dict[str, int], *, repaired: bool
    ) -> dict[str, Any]:
        result = parsed.to_request_dict()
        result["notes"] = parsed.notes
        result["provenance"] = {
            "parser": "llm",
            "model": self._model,
            "prompt_version": PROMPT_VERSION,
            "repaired": repaired,
            **usage,
        }
        return result
