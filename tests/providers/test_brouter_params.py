"""Per-request BRouter profile settings: what is accepted, clamped and reported."""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from bike_routing_agent.models import MAX_PROFILE_PARAMS, RouteConstraints
from bike_routing_agent.providers.brouter_params import (
    NOT_OFFERED,
    PROFILE_SETTINGS,
    SETTINGS,
    resolve,
)

PROFILES = Path(__file__).resolve().parents[2] / "docker" / "brouter" / "profiles"
OURS = [name for name in PROFILE_SETTINGS if name.startswith("custom_")]


def test_numbers_and_flags_are_sent_as_the_numbers_brouter_wants():
    got = resolve(
        "custom_touring-v1",
        {"uphillcost": 120.0, "consider_noise": True, "allow_steps": False, "C_r": 0.0075},
    )
    assert got.values == {"uphillcost": 120, "consider_noise": 1, "allow_steps": 0, "C_r": 0.0075}
    assert all(isinstance(v, int) for k, v in got.values.items() if k != "C_r")  # not "120.0"
    assert got.warnings == []


def test_a_flag_given_as_zero_or_one_is_accepted_but_other_numbers_are_not():
    got = resolve("custom_touring-v1", {"consider_noise": 1.0, "allow_steps": 0.5})
    assert got.values == {"consider_noise": 1}
    assert got.warnings == [
        "profile_params 'allow_steps' is on/off: use true or false; not applied"
    ]


def test_a_number_setting_given_a_boolean_is_not_applied():
    got = resolve("custom_touring-v1", {"uphillcost": True})
    assert got.values == {}
    assert got.warnings == ["profile_params 'uphillcost' needs a number; not applied"]


def test_a_typo_gets_a_did_you_mean():
    got = resolve("custom_touring-v1", {"uphilcost": 120, "consider_noise": True})
    assert got.values == {"consider_noise": 1}
    assert got.warnings == [
        "profile_params 'uphilcost' is not a setting of custom_touring-v1 "
        "(did you mean 'uphillcost'?); not applied"
    ]


def test_a_name_that_is_nothing_like_a_setting_lists_the_settings():
    (warning,) = resolve("custom_gravel-v2", {"zzz": 1}).warnings
    assert (
        "'zzz' is not a setting of custom_gravel-v2 (settings: consider_traffic_estimate,"
        in warning
    )
    assert "prefer_unpaved_paths" in warning and warning.endswith("not applied")


def test_a_setting_of_another_profile_is_named_without_the_list():
    # uphillcost belongs to the trekking family; the gravel profile has no such variable
    got = resolve("custom_gravel-v2", {"uphillcost": 120})
    assert got.values == {}
    assert got.warnings == [
        "profile_params 'uphillcost' is not a setting of custom_gravel-v2; not applied"
    ]


@pytest.mark.parametrize("profile", ["mtb", "vm-forum-liegerad-schnell", "no_such_profile"])
def test_a_profile_without_settings_says_so(profile):
    got = resolve(profile, {"uphillcost": 120})
    assert got.values == {}
    assert got.warnings == [
        f"profile_params 'uphillcost' was not applied: {profile} has no per-request settings"
    ]


@pytest.mark.parametrize(
    ("value", "used"),
    [(5000, 200), (-3, 0), (200, 200), (0, 0), (60.5, 60.5)],
)
def test_a_value_outside_the_range_is_clamped_and_reported(value, used):
    got = resolve("custom_commuter-v1", {"uphillcost": value})
    assert got.values == {"uphillcost": used}
    if used == value:
        assert got.warnings == []
    else:
        assert got.warnings == [
            f"profile_params 'uphillcost'={value} is outside 0..200 for custom_commuter-v1; "
            f"used {used}"
        ]


def test_a_value_that_is_not_a_number_is_not_applied():
    got = resolve("custom_touring-v1", {"bikerPower": math.nan, "totalMass": math.inf})
    assert got.values == {}
    assert len(got.warnings) == 2


def test_nothing_requested_means_nothing_sent():
    assert resolve("custom_touring-v1", {}).values == {}
    assert resolve("custom_touring-v1", {}).warnings == []


# --- the registry against the profile files


@pytest.mark.parametrize("profile", OURS)
def test_every_registered_setting_is_documented_in_the_profile_file(profile):
    text = (PROFILES / f"{profile.removeprefix('custom_')}.brf").read_text()
    markers = set(re.findall(r"#\s*%(\w+)%", text))
    assert set(PROFILE_SETTINGS[profile]) <= markers, "a setting the profile does not have"


@pytest.mark.parametrize("profile", OURS)
def test_a_new_setting_in_a_profile_file_is_offered_or_left_out_on_purpose(profile):
    text = (PROFILES / f"{profile.removeprefix('custom_')}.brf").read_text()
    markers = set(re.findall(r"#\s*%(\w+)%", text))
    missing = markers - set(PROFILE_SETTINGS[profile]) - NOT_OFFERED
    assert not missing, (
        f"{profile} documents {sorted(missing)}: add to brouter_params or NOT_OFFERED"
    )


def test_every_setting_is_used_and_every_range_makes_sense():
    used = {name for names in PROFILE_SETTINGS.values() for name in names}
    assert used == set(SETTINGS)
    for name, setting in SETTINGS.items():
        assert setting.low <= setting.high, name
        assert setting.meaning, name
        if setting.kind == "flag":
            assert (setting.low, setting.high) == (0, 1), name


def test_no_profile_offers_a_setting_that_was_left_out_on_purpose():
    for names in PROFILE_SETTINGS.values():
        assert not set(names) & NOT_OFFERED


# --- the request field


def test_profile_params_default_to_nothing_and_keep_booleans_and_numbers():
    assert RouteConstraints().profile_params == {}
    got = RouteConstraints.model_validate_json(
        '{"profile_params": {"consider_noise": true, "uphillcost": 120, "C_r": 0.0075}}'
    ).profile_params
    assert got["consider_noise"] is True
    assert got["uphillcost"] == 120 and got["C_r"] == 0.0075


@pytest.mark.parametrize(
    "bad",
    [
        {"a b": 1},
        {"x&profile=other": 1},
        {"": 1},
        {"1abc": 1},
        {"x" * 41: 1},
        {"x": float("nan")},
        {"x": float("inf")},
        {"x": "abc"},
        {f"p{i}": 1 for i in range(MAX_PROFILE_PARAMS + 1)},
    ],
)
def test_profile_params_that_could_not_be_a_setting_are_rejected(bad):
    with pytest.raises(ValidationError):
        RouteConstraints(profile_params=bad)
