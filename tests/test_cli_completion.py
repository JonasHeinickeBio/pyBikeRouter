"""``bike-router completion``: generated scripts, and the bash function under a real bash."""

from __future__ import annotations

import io
import shutil
import subprocess

import pytest

from bike_routing_agent.cli import main
from bike_routing_agent.cli.commands.completion import command_tree
from bike_routing_agent.cli.main import GROUP_MODULES, build_parser


def script(shell: str) -> str:
    out, err = io.StringIO(), io.StringIO()
    assert main(["completion", shell], stdout=out, stderr=err) == 0 and err.getvalue() == ""
    return out.getvalue()


def test_the_tree_has_every_group_command_and_option():
    root = command_tree(build_parser())
    assert set(root.children) == set(GROUP_MODULES)
    plan = root.children["route"].children["plan"]
    assert {"--origin", "--format", "--trace", "--quiet", "-q"} <= set(plan.options)
    assert plan.choices["--format"] == ["json", "text"]
    assert plan.choices["--engine"] == ["ors", "brouter", "valhalla"]
    assert root.children["route"].help  # fish shows it next to the word


def test_completion_is_a_group_of_the_three_shells():
    root = command_tree(build_parser())
    assert list(root.children["completion"].children) == ["bash", "zsh", "fish"]


def test_a_group_without_a_shell_and_an_unknown_one_are_usage_errors():
    out, err = io.StringIO(), io.StringIO()
    assert main(["completion"], stdout=out, stderr=err) == 2
    assert "bash" in out.getvalue()


def test_the_scripts_name_the_program_and_what_it_completes():
    bash = script("bash")
    assert "complete -o default -F _bike_router bike-router" in bash
    assert "'route plan --format') echo 'json text' ;;" in bash
    zsh = script("zsh")
    assert "bashcompinit" in zsh and "complete -o default -F _bike_router bike-router" in zsh
    fish = script("fish")
    assert "complete -c bike-router -f -n '__fish_use_subcommand' -a route" in fish
    assert (
        "__fish_seen_subcommand_from route; and __fish_seen_subcommand_from plan' -l format" in fish
    )
    assert "-x -a 'json text'" in fish


def test_a_help_text_with_a_quote_cannot_break_the_script():
    from bike_routing_agent.cli.commands.completion import _quote

    assert _quote("it's") == "'it'\\''s'"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (["ro"], {"route"}),
        (["route", ""], {"plan", "show", "export", "picks"}),
        (["route", "plan", "--for"], {"--format"}),
        (["route", "plan", "--format", ""], {"json", "text"}),
        (["route", "plan", "--origin", "Berlin", "--eng"], {"--engine"}),  # a value is not a word
        (["route", "plan", "--engine", ""], {"ors", "brouter", "valhalla"}),
        (["history", "sh"], {"show"}),
        (["completion", ""], {"bash", "zsh", "fish"}),
    ],
)
def test_the_bash_function_completes_in_a_real_bash(words, expected, tmp_path):
    completion = tmp_path / "bike-router.bash"
    completion.write_text(script("bash"), encoding="utf-8")
    quoted = " ".join(repr(w) for w in words)
    program = f"""source {completion}
COMP_WORDS=(bike-router {quoted})
COMP_CWORD=$(( ${{#COMP_WORDS[@]}} - 1 ))
COMPREPLY=()
_bike_router
printf '%s\\n' "${{COMPREPLY[@]}}"
"""
    result = subprocess.run(["bash", "-c", program], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.split()) == expected
