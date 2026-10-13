"""Connecting a language model: the settings each choice means, the settings-file editor, and the
two small OpenRouter checks (no key needed for the list)."""

from __future__ import annotations

import httpx
import pytest
import respx

from bike_routing_agent import llm_setup
from bike_routing_agent.config import Settings


def test_every_choice_makes_settings_the_parser_accepts(monkeypatch):
    for name in list(llm_setup._KEYS):
        monkeypatch.delenv(name, raising=False)
    for choice, model, key in (
        ("openrouter", "google/gemma:free", "sk-or-x"),
        ("ollama", "llama3.2:3b", None),
    ):
        values = llm_setup.settings_for(choice, model, key)
        for name, value in values.items():
            monkeypatch.setenv(name, value)
        cfg = Settings(_env_file=None)
        assert cfg.llm_parser_enabled is True and cfg.llm_provider == "openai"
        assert cfg.llm_model == model and cfg.llm_base_url.startswith(("https://", "http://"))
    off = llm_setup.settings_for("off")
    assert off["LLM_PARSER_ENABLED"] == "false" and off["LLM_MODEL"] == ""


def test_the_settings_say_where_the_model_runs():
    assert llm_setup.settings_for("openrouter", "m:free", "k")["LLM_BASE_URL"] == (
        "https://openrouter.ai/api/v1"
    )
    ollama = llm_setup.settings_for("ollama", "llama3.2:3b")
    assert ollama["LLM_BASE_URL"] == "http://ollama:11434/v1" and ollama["LLM_API_KEY"] == ""
    assert int(ollama["LLM_TIMEOUT_S"]) >= 120  # a home processor is slow


@pytest.mark.parametrize(
    "args",
    [
        ("openrouter", None, "k"),
        ("openrouter", "m", None),
        ("ollama", None, None),
        ("nonsense", "m", "k"),
    ],
)
def test_a_choice_without_what_it_needs_is_refused(args):
    with pytest.raises(ValueError):
        llm_setup.settings_for(*args)


def test_the_settings_file_keeps_comments_and_order_and_replaces_or_appends(tmp_path):
    path = tmp_path / "settings.env"
    path.write_text(
        "# my settings\nPORT=8000\n\n# LLM_MODEL=old-example\nLLM_PARSER_ENABLED=false\n"
        "APP_BIND=127.0.0.1\n"
    )
    llm_setup.update_env_file(
        path,
        {
            "LLM_PARSER_ENABLED": "true",
            "LLM_MODEL": "llama3.2:3b",  # a commented example becomes the live line, in place
            "LLM_BASE_URL": "http://ollama:11434/v1",  # new: appended
            "LLM_API_KEY": "",  # nothing to say: not written
        },
    )
    assert path.read_text().splitlines() == [
        "# my settings",
        "PORT=8000",
        "",
        "LLM_MODEL=llama3.2:3b",
        "LLM_PARSER_ENABLED=true",
        "APP_BIND=127.0.0.1",
        "LLM_BASE_URL=http://ollama:11434/v1",
    ]
    assert path.stat().st_mode & 0o777 == 0o600  # it may hold a key


def test_switching_off_comments_the_live_keys_out(tmp_path):
    path = tmp_path / "settings.env"
    path.write_text("LLM_PARSER_ENABLED=true\nLLM_MODEL=m\nLLM_API_KEY=sk-secret\nPORT=8000\n")
    llm_setup.update_env_file(path, llm_setup.settings_for("off"))
    text = path.read_text()
    assert "sk-secret" not in text  # the key does not stay behind
    assert "LLM_PARSER_ENABLED=false" in text and "PORT=8000" in text
    assert llm_setup.read_env_file(path) == {"LLM_PARSER_ENABLED": "false", "PORT": "8000"}


def test_a_missing_file_is_created(tmp_path):
    path = tmp_path / "new.env"
    llm_setup.update_env_file(path, {"LLM_MODEL": "x"})
    assert path.read_text() == "LLM_MODEL=x\n"


MODELS = {
    "data": [
        {"id": "a/old:free", "created": 100, "architecture": {"output_modalities": ["text"]}},
        {"id": "b/new:free", "created": 300, "architecture": {"output_modalities": ["text"]}},
        {"id": "c/paid", "created": 400, "architecture": {"output_modalities": ["text"]}},
        {"id": "d/image:free", "created": 500, "architecture": {"output_modalities": ["image"]}},
        {"id": "e/mid:free", "created": 200},
    ]
}


@respx.mock
async def test_the_free_models_are_listed_newest_first_and_only_text_ones():
    respx.get(llm_setup.OPENROUTER_MODELS_URL).mock(return_value=httpx.Response(200, json=MODELS))
    async with httpx.AsyncClient() as client:
        assert await llm_setup.list_free_models(client) == [
            "b/new:free",
            "e/mid:free",
            "a/old:free",
        ]
        assert await llm_setup.list_free_models(client, limit=1) == ["b/new:free"]


@respx.mock
async def test_an_unreachable_or_odd_list_is_an_empty_list():
    respx.get(llm_setup.OPENROUTER_MODELS_URL).mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as client:
        assert await llm_setup.list_free_models(client) == []
    respx.get(llm_setup.OPENROUTER_MODELS_URL).mock(return_value=httpx.Response(200, text="<html>"))
    async with httpx.AsyncClient() as client:
        assert await llm_setup.list_free_models(client) == []


@respx.mock
@pytest.mark.parametrize(
    ("status", "expected"), [(200, True), (401, False), (403, False), (500, None)]
)
async def test_the_key_check_tells_accepted_rejected_and_unknown(status, expected):
    route = respx.get(llm_setup.OPENROUTER_KEY_URL).mock(return_value=httpx.Response(status))
    async with httpx.AsyncClient() as client:
        assert await llm_setup.key_works(client, "sk-or-abc") is expected
    assert route.calls[0].request.headers["authorization"] == "Bearer sk-or-abc"


@respx.mock
async def test_no_internet_is_unknown_not_rejected():
    respx.get(llm_setup.OPENROUTER_KEY_URL).mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as client:
        assert await llm_setup.key_works(client, "k") is None
