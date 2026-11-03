"""``bike-router profile``: the settings of a routing profile, and a dry run of them."""

from __future__ import annotations

import io
import json

import pytest

from bike_routing_agent.cli import main


def cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_settings_of_a_bike_types_profile_are_listed_with_range_and_effect():
    code, out, _ = cli("profile", "settings", "--bike-type", "touring")
    assert code == 0
    assert out.startswith("custom_touring-v1 (touring): 22 settings")
    line = next(ln for ln in out.splitlines() if ln.strip().startswith("uphillcost"))
    assert "0..200" in line and "per metre climbed" in line
    power = next(ln for ln in out.splitlines() if ln.strip().startswith("bikerPower"))
    assert "[time estimate only]" in power
    assert "on/off" in next(ln for ln in out.splitlines() if "consider_noise" in ln)


def test_settings_as_json_have_the_same_facts():
    code, out, _ = cli("profile", "settings", "--profile", "custom_gravel-v2", "--format", "json")
    assert code == 0
    by_name = {s["name"]: s for s in json.loads(out)}
    assert by_name["prefer_unpaved_paths"]["type"] == "boolean"
    assert by_name["totalMass"] == {
        "name": "totalMass",
        "type": "number",
        "group": "rider",
        "affects": "time",
        "meaning": "bike and rider together, in kg",
        "minimum": 20,
        "maximum": 300,
    }
    assert "uphillcost" not in by_name


def test_without_a_target_every_profile_with_settings_is_listed():
    code, out, _ = cli("profile", "settings")
    assert code == 0
    for name in ("custom_touring-v1", "custom_gravel-v2", "trekking", "fastbike"):
        assert name in out
    code, out, _ = cli("profile", "settings", "--format", "json")
    assert code == 0 and "custom_commuter-v1" in json.loads(out)


def test_a_profile_without_settings_says_so():
    code, out, _ = cli("profile", "settings", "--bike-type", "mountain")
    assert code == 0 and "mtb (mountain): no per-request settings" in out


def test_naming_both_a_bike_type_and_a_profile_is_a_usage_error():
    code, _, err = cli("profile", "settings", "--bike-type", "touring", "--profile", "trekking")
    assert code == 2 and "not both" in err


def test_schema_is_json_schema_that_forbids_unknown_names():
    code, out, _ = cli("profile", "schema", "--bike-type", "commuter")
    assert code == 0
    schema = json.loads(out)
    assert schema["additionalProperties"] is False and "uphillcost" in schema["properties"]
    assert cli("profile", "schema")[0] == 2
    code, _, err = cli("profile", "schema", "--bike-type", "mountain")
    assert code == 2 and "no per-request settings" in err


def test_check_shows_what_brouter_would_be_sent():
    code, out, _ = cli(
        "profile", "check", "--bike-type", "touring",
        "--profile-param", "uphillcost=90", "--profile-param", "consider_noise=true",
    )  # fmt: skip
    assert code == 0
    assert "custom_touring-v1: ok" in out
    assert "sent to BRouter: profile:uphillcost=90 profile:consider_noise=1" in out


def test_check_names_every_problem_and_exits_2():
    code, out, _ = cli(
        "profile", "check", "--bike-type", "touring",
        "--profile-param", "uphilcost=120", "--profile-param", "totalMass=5000",
    )  # fmt: skip
    assert code == 2
    assert "custom_touring-v1: not valid" in out
    assert "(did you mean 'uphillcost'?)" in out and "totalMass:" in out


def test_check_as_json_for_scripts():
    code, out, _ = cli(
        "profile", "check", "--profile", "trekking", "--profile-param", "consider_noise=off",
        "--format", "json",
    )  # fmt: skip
    assert code == 0
    assert json.loads(out) == {
        "profile": "trekking",
        "valid": True,
        "problems": [],
        "sent": {"consider_noise": 0},
    }
    code, out, _ = cli("profile", "check", "--bike-type", "mountain", "--profile-param", "a=1",
                       "--format", "json")  # fmt: skip
    assert code == 2 and json.loads(out)["problems"] == ["mtb has no per-request settings"]


def test_check_reads_a_file_and_standard_input(tmp_path, monkeypatch):
    path = tmp_path / "rider.json"
    path.write_text('{"bikerPower": 250}')
    code, out, _ = cli(
        "profile", "check", "--bike-type", "touring", "--profile-params-file", str(path)
    )
    assert code == 0 and "profile:bikerPower=250" in out
    monkeypatch.setattr("sys.stdin", io.StringIO('{"maxSpeed": 30}'))
    code, out, _ = cli("profile", "check", "--bike-type", "touring", "--profile-params-file", "-")
    assert code == 0 and "profile:maxSpeed=30" in out
    code, _, err = cli("profile", "check", "--bike-type", "touring", "--profile-param", "oops")
    assert code == 2 and "NAME=VALUE" in err


@pytest.mark.parametrize("command", ["settings", "schema", "check"])
def test_the_group_is_in_the_help(command, capsys):
    assert main(["profile", "--help"]) == 0
    assert command in capsys.readouterr().out
