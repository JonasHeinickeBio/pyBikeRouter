"""The error a failed parse raises, with a stable code the API reports."""

from __future__ import annotations


class LLMParseError(Exception):
    """A parse that failed in a way the caller should report, with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
