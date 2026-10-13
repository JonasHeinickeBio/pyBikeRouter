"""Connecting a language model, for people who do not know what an API is.

The planner works without one. With one it also reads free sentences ("a relaxed 50 km loop
around Goslar tomorrow morning", docs/llm-parser.md). Two easy ways, both through the
OpenAI-compatible interface the parser already speaks:

- **OpenRouter** -- an online service with some free models; needs an account and an API key.
- **Ollama** -- runs on the person's own computer (a container of the easy-install kit); free,
  no account; downloads a model of a few GB.

This module holds what the setup wizard (``bike-router setup``) needs: the settings each choice
means, a tolerant editor for ``settings.env``, and two small checks against OpenRouter. Nothing
here sends a person's text anywhere; the key is only ever written to the settings file.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Mapping
from pathlib import Path

import httpx

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS_URL = f"{OPENROUTER_BASE_URL}/models"
OPENROUTER_KEY_URL = f"{OPENROUTER_BASE_URL}/auth/key"
OPENROUTER_KEYS_PAGE = "https://openrouter.ai/keys"
# Free models rotate; this is only what is offered when the live list cannot be fetched.
OPENROUTER_FALLBACK_MODEL = "google/gemma-4-26b-a4b-it:free"

OLLAMA_BASE_URL = "http://ollama:11434/v1"  # the kit's service name, inside its network
OLLAMA_MODELS: tuple[tuple[str, str], ...] = (
    ("llama3.2:3b", "about 2 GB; reads short sentences; needs about 4 GB of free memory"),
    ("qwen2.5:7b", "about 4.7 GB; understands more; needs about 8 GB of free memory"),
)

CHOICES = ("off", "openrouter", "ollama")
_KEYS = (
    "LLM_PARSER_ENABLED",
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_TIMEOUT_S",
)


def settings_for(
    choice: str, model: str | None = None, api_key: str | None = None
) -> dict[str, str]:
    """The settings.env entries for a choice; every LLM key is set so none is left over."""
    if choice == "off":
        return {key: "" for key in _KEYS} | {"LLM_PARSER_ENABLED": "false"}
    if not model:
        raise ValueError("a model is needed")
    if choice == "openrouter":
        if not api_key:
            raise ValueError("an API key is needed for OpenRouter")
        return {
            "LLM_PARSER_ENABLED": "true",
            "LLM_PROVIDER": "openai",
            "LLM_BASE_URL": OPENROUTER_BASE_URL,
            "LLM_MODEL": model,
            "LLM_API_KEY": api_key,
            "LLM_TIMEOUT_S": "60",
        }
    if choice == "ollama":
        return {
            "LLM_PARSER_ENABLED": "true",
            "LLM_PROVIDER": "openai",
            "LLM_BASE_URL": OLLAMA_BASE_URL,
            "LLM_MODEL": model,
            "LLM_API_KEY": "",
            "LLM_TIMEOUT_S": "180",  # a model on a home computer's processor is slow
        }
    raise ValueError(f"unknown choice {choice!r}; use one of {', '.join(CHOICES)}")


def update_env_file(path: Path, updates: Mapping[str, str]) -> None:
    """Set ``KEY=value`` lines in a settings file, keeping everything else (comments, order).

    An existing ``KEY=...`` line is replaced; a commented ``# KEY=...`` example is replaced in
    place by the live line; otherwise the line is appended. An empty value comments the key out.
    The file is left readable by its owner only (it may hold an API key).
    """
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        match = re.match(r"^\s*#?\s*([A-Z][A-Z0-9_]*)=", line)
        key = match.group(1) if match else None
        if key in remaining:
            value = remaining.pop(key)
            if value == "":
                # a key left empty stays out of the way, as a comment
                out.append(line if line.lstrip().startswith("#") else f"# {key}=")
            else:
                out.append(f"{key}={value}")
        else:
            out.append(line)
    for key, value in remaining.items():
        if value != "":
            out.append(f"{key}={value}")
    path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):  # best effort (not every file system has modes)
        os.chmod(path, 0o600)


def read_env_file(path: Path) -> dict[str, str]:
    """The live ``KEY=value`` lines of a settings file."""
    found: dict[str, str] = {}
    if not path.exists():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            found[match.group(1)] = match.group(2).strip()
    return found


async def list_free_models(client: httpx.AsyncClient, limit: int = 6) -> list[str]:
    """Ids of OpenRouter's free models, newest first (no key needed); ``[]`` when unreachable."""
    try:
        response = await client.get(OPENROUTER_MODELS_URL, timeout=15.0)
        response.raise_for_status()
        data = response.json().get("data") or []
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    free = [
        m
        for m in data
        if isinstance(m, dict)
        and str(m.get("id", "")).endswith(":free")
        and "text" in ((m.get("architecture") or {}).get("output_modalities") or ["text"])
    ]
    free.sort(key=lambda m: m.get("created") or 0, reverse=True)
    return [str(m["id"]) for m in free[:limit]]


async def key_works(client: httpx.AsyncClient, api_key: str) -> bool | None:
    """Does OpenRouter accept this key? ``None`` when it could not be asked (no internet)."""
    try:
        response = await client.get(
            OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=15.0
        )
    except httpx.HTTPError:
        return None
    if response.status_code == 200:
        return True
    if response.status_code in (401, 403):
        return False
    return None
