"""scripts/check_profiles.py: which label a row carries (no BRouter needed)."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_profiles.py"


@pytest.fixture
def script(monkeypatch):
    spec = importlib.util.spec_from_file_location("check_profiles", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_profiles"] = module
    spec.loader.exec_module(module)
    metrics = {
        "km": 1.0, "min": 2.0, "ascent_m": 3.0, "traffic_pct": 4, "paved_pct": 5,
        "compacted_pct": 6, "loose_pct": 7, "cobbles_pct": 8, "unknown_pct": 9,
        "track_path_pct": 10,
    }  # fmt: skip
    monkeypatch.setattr(module, "route_metrics", lambda *a, **k: dict(metrics))
    monkeypatch.setattr(module, "routes", lambda: {"r1": ({}, {})})
    return module


def run(script, monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["check_profiles.py", *args])
    script.main()
    return capsys.readouterr().out.splitlines()


def test_by_default_every_bike_type_is_labelled_with_its_profile(script, monkeypatch, capsys):
    lines = run(script, monkeypatch, capsys)
    rows = [line for line in lines if line.startswith(tuple(script.BIKE_TYPES))]
    assert [r.split()[0] for r in rows] == script.BIKE_TYPES
    assert "custom_gravel-v2" in next(r for r in rows if r.startswith("gravel"))


def test_an_empty_profile_flag_behaves_like_no_flag(script, monkeypatch, capsys):
    # nargs="*": `--profile` with no values is [] and falls back to the bike types;
    # the labels must follow the same rule (the bike type, not the profile name).
    plain = run(script, monkeypatch, capsys)
    empty = run(script, monkeypatch, capsys, "--profile")
    assert plain == empty


def test_explicit_profiles_are_labelled_by_name_once(script, monkeypatch, capsys):
    lines = run(script, monkeypatch, capsys, "--profile", "custom_gravel-v1", "custom_gravel-v2")
    rows = [line for line in lines if line.startswith("custom_gravel")]
    assert [r.split()[0] for r in rows] == ["custom_gravel-v1", "custom_gravel-v2"]
    assert all(r.count("custom_gravel") == 1 for r in rows)
