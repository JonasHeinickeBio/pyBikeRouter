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
    ask = answers(around, "Braunschweig", "", "")  # town, "right place" -> yes, download -> yes
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


def test_rejecting_the_town_and_typing_nothing_is_a_clear_error_not_a_blank_search(tmp_path):
    ask = answers("n", "   ")  # "that is not the right place", then nothing typed
    springfield = town("Springfield, Illinois", -89.6, 39.8)
    code, _, err = run(
        "start", "--around", "Springfield", ask=ask, geocoder=springfield, directory=tmp_path
    )
    assert code == 2 and "no town given" in err
