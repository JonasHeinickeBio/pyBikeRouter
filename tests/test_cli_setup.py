"""``bike-router setup``: pick an area, see the sizes, download the map tiles."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
import respx

from bike_routing_agent.cli import main
from bike_routing_agent.cli.commands import setup as setup_cmd
from bike_routing_agent.cli.main import build_parser
from bike_routing_agent.models import Coordinate, GeocodeCandidate

SOURCE = "https://brouter.de/brouter/segments4/"


def parse(*argv: str):
    return build_parser().parse_args(["setup", *argv])


def run(*argv: str, ask=None, geocoder=None, directory: Path | None = None):
    out, err = io.StringIO(), io.StringIO()
    args = list(argv)
    if directory is not None and "--dir" not in args:
        args += ["--dir", str(directory)]
    code = setup_cmd.run(
        parse(*args), out, err, ask=ask, geocoder_factory=geocoder, poll_interval_s=0.01
    )
    return code, out.getvalue(), err.getvalue()


def mock_tiles(*names: str, size: int = 4000) -> None:
    for name in names:
        respx.head(f"{SOURCE}{name}.rd5").mock(
            return_value=httpx.Response(200, headers={"content-length": str(size)})
        )
        respx.get(f"{SOURCE}{name}.rd5").mock(return_value=httpx.Response(200, content=b"t" * size))


def answers(*texts: str):
    queue = list(texts)
    asked: list[str] = []

    def ask(question: str) -> str:
        asked.append(question)
        return queue.pop(0)

    ask.asked = asked  # type: ignore[attr-defined]
    return ask


def town(label: str, lon: float, lat: float):
    class Geocoder:
        name = "fake"

        async def geocode(self, query: str, *, limit: int = 5):
            if "nowhere" in query:
                return []
            return [
                GeocodeCandidate(
                    label=label, coordinate=Coordinate(lon=lon, lat=lat), confidence=0.9, source="x"
                )
            ]

    return lambda: Geocoder()


def test_the_ready_made_areas_are_listed():
    out = io.StringIO()
    assert setup_cmd.run(parse("regions"), out, io.StringIO()) == 0
    assert "germany" in out.getvalue() and "E10_N50" in out.getvalue()


@respx.mock
def test_a_ready_made_area_downloads_its_tiles_after_showing_the_sizes(tmp_path):
    mock_tiles("E0_N50", "E5_N50")
    code, out, _ = run("start", "--region", "benelux", "--yes", directory=tmp_path)
    assert code == 0, out
    assert (tmp_path / "E0_N50.rd5").is_file() and (tmp_path / "E5_N50.rd5").is_file()
    assert "to download" in out and "All set" in out
    assert not list(tmp_path.glob("*.part"))


@respx.mock
def test_the_menu_asks_one_question_and_confirms_before_downloading(tmp_path):
    mock_tiles("E10_N50", "E5_N50")
    # the menu numbers follow the preset order; "around a town" and tile names come after them
    around = str(len(setup_cmd.REGIONS) + 1)
    # town, "right place" -> yes, download -> yes, then the language-model question: leave it off
    ask = answers(around, "Braunschweig", "", "", "")
    code, out, _ = run(
        "start",
        ask=ask,
        geocoder=town("Braunschweig, Niedersachsen", 10.5267, 52.2689),
        directory=tmp_path,
    )
    assert code == 0, out
    assert "Welcome to pyBikeRouter" in out and "Found: Braunschweig, Niedersachsen" in out
    assert "E5_N50, E10_N50" in out
    assert any("Download it now?" in q for q in ask.asked)  # type: ignore[attr-defined]
    assert {p.name for p in tmp_path.glob("*.rd5")} == {"E5_N50.rd5", "E10_N50.rd5"}


@respx.mock
def test_saying_no_downloads_nothing(tmp_path):
    mock_tiles("E10_N50")
    ask = answers("n")
    code, out, _ = run("start", "--tiles", "E10_N50", ask=ask, directory=tmp_path)
    assert code == 1 and "Not downloaded" in out and not list(tmp_path.iterdir())


@respx.mock
def test_tiles_that_are_already_there_are_not_downloaded_again(tmp_path):
    mock_tiles("E10_N50")
    (tmp_path / "E10_N50.rd5").write_bytes(b"x")
    code, out, _ = run("start", "--tiles", "E10_N50", "--yes", directory=tmp_path)
    assert code == 0 and "Everything is already here" in out
    assert (tmp_path / "E10_N50.rd5").read_bytes() == b"x"


@respx.mock
def test_more_tiles_than_one_download_job_allows_are_fetched_in_turns(tmp_path):
    names = ["E0_N40", "E5_N40", "E0_N45", "E5_N45", "E0_N50", "E5_N50"]
    mock_tiles(*names)
    code, out, _ = run("start", "--tiles", ",".join(names), "--yes", directory=tmp_path)
    assert code == 0 and {p.stem for p in tmp_path.glob("*.rd5")} == set(names)


def test_skip_downloads_nothing_and_needs_no_folder(tmp_path):
    code, out, _ = run("start", "--skip")
    assert code == 0 and "choose map data in the web page" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["start", "--region", "germany", "--skip"],
        ["start", "--tiles", "E10_N50", "--around", "Goslar"],
        ["start", "--tiles", "Germany"],  # not a tile name
        ["start", "--around", "nowhere at all"],
    ],
)
def test_bad_choices_are_usage_errors_in_plain_words(tmp_path, argv):
    code, _, err = run(*argv, "--yes", geocoder=town("x", 1, 1), directory=tmp_path)
    assert code == 2 and err.startswith("error:")


def test_without_a_terminal_and_without_a_choice_it_says_what_to_pass(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code, _, err = run("start", directory=tmp_path)
    assert code == 2 and "--region" in err and "--around" in err


def test_a_missing_folder_is_explained(tmp_path):
    code, _, err = run("start", "--tiles", "E10_N50", "--yes", directory=tmp_path / "nope")
    assert code == 1 and "where to put the map data" in err


def test_the_command_is_registered_in_the_cli():
    out, err = io.StringIO(), io.StringIO()
    assert main(["setup", "regions"], stdout=out, stderr=err) == 0
    assert "great_britain" in out.getvalue()


# ------------------------------------------------------------------ a language model

MODELS_URL = "https://openrouter.ai/api/v1/models"
KEY_URL = "https://openrouter.ai/api/v1/auth/key"
OLD_SETTINGS = "PORT=8000\nAPP_BIND=127.0.0.1\n"


def llm_run(*argv: str, ask=None, secret=None, settings: Path | None = None):
    out, err = io.StringIO(), io.StringIO()
    args = ["start", "--skip", *argv]
    if settings is not None:
        args += ["--settings-file", str(settings)]
    code = setup_cmd.run(parse(*args), out, err, ask=ask, secret=secret)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def settings_file(tmp_path) -> Path:
    path = tmp_path / "settings.env"
    path.write_text(OLD_SETTINGS)
    return path


def test_pressing_enter_leaves_the_language_model_off(settings_file):
    code, out, _ = llm_run(ask=answers(""), settings=settings_file)
    assert code == 0 and "Left the language model off" in out
    assert "LLM_PARSER_ENABLED=false" in settings_file.read_text()
    assert "PORT=8000" in settings_file.read_text()


@respx.mock
def test_openrouter_asks_for_the_key_without_showing_it_and_saves_the_choice(settings_file):
    respx.get(MODELS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"id": "google/gemma:free", "created": 5},
                           {"id": "meta/llama:free", "created": 9}]},
        )
    )  # fmt: skip
    respx.get(KEY_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
    typed: list[str] = []
    ask = answers("2", "2")  # OpenRouter, then the second model of the list
    code, out, err = llm_run(
        ask=ask, secret=lambda q: typed.append(q) or "sk-or-v1-secretsecret", settings=settings_file
    )
    assert code == 0, err
    assert "openrouter.ai/keys" in out and typed and "not shown" in typed[0]
    assert "sk-or-v1-secretsecret" not in out + err  # never printed
    text = settings_file.read_text()
    assert "LLM_PARSER_ENABLED=true" in text and "LLM_PROVIDER=openai" in text
    assert "LLM_MODEL=google/gemma:free" in text  # 2 = the older one in newest-first order
    assert "LLM_BASE_URL=https://openrouter.ai/api/v1" in text
    assert "LLM_API_KEY=sk-or-v1-secretsecret" in text
    assert settings_file.stat().st_mode & 0o777 == 0o600


@respx.mock
def test_a_key_openrouter_refuses_is_not_saved(settings_file):
    respx.get(MODELS_URL).mock(return_value=httpx.Response(200, json={"data": []}))
    respx.get(KEY_URL).mock(return_value=httpx.Response(401))
    code, _, err = llm_run(ask=answers("2", ""), secret=lambda q: "wrong", settings=settings_file)
    assert code == 1 and "did not accept that key" in err
    assert settings_file.read_text() == OLD_SETTINGS


@respx.mock
def test_without_internet_the_key_is_saved_with_a_note_and_a_fallback_model(settings_file):
    respx.get(MODELS_URL).mock(side_effect=httpx.ConnectError("down"))
    respx.get(KEY_URL).mock(side_effect=httpx.ConnectError("down"))
    code, out, _ = llm_run(ask=answers("2", ""), secret=lambda q: "k-123", settings=settings_file)
    assert code == 0 and "Could not reach OpenRouter" in out
    assert "LLM_MODEL=google/gemma-4-26b-a4b-it:free" in settings_file.read_text()


def test_an_empty_key_means_leave_it_off(settings_file):
    code, out, _ = llm_run(ask=answers("2"), secret=lambda q: "  ", settings=settings_file)
    assert code == 0 and "No key given" in out
    assert "LLM_PARSER_ENABLED=false" in settings_file.read_text()


def test_ollama_needs_no_account_and_saves_a_local_model(settings_file):
    code, out, _ = llm_run(ask=answers("3", "2"), settings=settings_file)  # the 7b model
    assert code == 0 and "downloads it the first time" in out
    text = settings_file.read_text()
    assert "LLM_MODEL=qwen2.5:7b" in text and "LLM_BASE_URL=http://ollama:11434/v1" in text
    assert "LLM_API_KEY" not in text.replace("# LLM_API_KEY=", "")


def test_flags_answer_every_question_for_scripts(settings_file, monkeypatch):
    code, out, _ = llm_run("--llm", "ollama", "--llm-model", "llama3.2:3b", settings=settings_file)
    assert code == 0 and "LLM_MODEL=llama3.2:3b" in settings_file.read_text()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    code, _, err = llm_run("--llm", "openrouter", "--llm-model", "m:free", settings=settings_file)
    assert code == 2 and "OPENROUTER_API_KEY" in err  # a key is never taken from the command line


@respx.mock
def test_a_key_in_the_environment_is_used_without_asking(settings_file, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env-key")
    respx.get(KEY_URL).mock(return_value=httpx.Response(200))
    code, out, err = llm_run("--llm", "openrouter", "--llm-model", "m:free", settings=settings_file)
    assert code == 0 and "sk-env-key" not in out + err
    assert "LLM_API_KEY=sk-env-key" in settings_file.read_text()


def test_without_a_settings_file_the_lines_are_shown_and_the_key_is_not(monkeypatch):
    monkeypatch.delenv("PBR_SETTINGS_FILE", raising=False)
    code, out, _ = llm_run("--llm", "ollama", "--llm-model", "llama3.2:3b")
    assert code == 0 and "LLM_PROVIDER=openai" in out and "LLM_MODEL=llama3.2:3b" in out


def test_without_a_terminal_and_without_a_flag_nothing_is_asked(settings_file, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code, out, _ = llm_run(settings=settings_file)
    assert code == 0 and settings_file.read_text() == OLD_SETTINGS


def test_the_settings_file_comes_from_the_environment_in_the_kit(settings_file, monkeypatch):
    monkeypatch.setenv("PBR_SETTINGS_FILE", str(settings_file))
    code, _, _ = llm_run("--llm", "off")
    assert code == 0 and "LLM_PARSER_ENABLED=false" in settings_file.read_text()


def test_rejecting_the_town_and_typing_nothing_is_a_clear_error_not_a_blank_search(tmp_path):
    ask = answers("n", "   ")  # "that is not the right place", then nothing typed
    springfield = town("Springfield, Illinois", -89.6, 39.8)
    code, _, err = run(
        "start", "--around", "Springfield", ask=ask, geocoder=springfield, directory=tmp_path
    )
    assert code == 2 and "no town given" in err
