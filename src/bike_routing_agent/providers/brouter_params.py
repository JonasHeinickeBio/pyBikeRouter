"""Settings of the BRouter profiles that a request may change.

BRouter takes a profile variable per request as ``profile:<name>=<number>``. It is picky in
ways that fail quietly or loudly: a name the profile does not have is ignored (a typo does
nothing), a boolean must be ``1``/``0`` (``true`` is HTTP 500), and a variable the profile
computes itself is HTTP 500 too. So a request never sends a name straight through: it is
looked up here, per profile, clamped to a range that is safe to route with, and anything that
could not be applied comes back as a warning instead.

Only routing preferences and the rider's physics are listed. Turn-instruction, debugging and
via-point settings are left out on purpose, and so is ``allow_motorways`` of ``fastbike``.
The sets below were read from the ``# %name% | ...`` markers of the profiles
(docker/brouter/profiles for ours, the BRouter image for the stock ones); a test keeps ours
in step with the files. Effects measured in docs/profile-experiment.md.
"""

from __future__ import annotations

import difflib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Setting:
    kind: Literal["flag", "number"]
    low: float = 0.0
    high: float = 1.0
    meaning: str = ""


def _flag(meaning: str) -> Setting:
    return Setting("flag", 0, 1, meaning)


def _number(low: float, high: float, meaning: str) -> Setting:
    return Setting("number", low, high, meaning)


SETTINGS: dict[str, Setting] = {
    # what the route avoids or prefers
    "consider_traffic": _flag("avoid roads with estimated heavy traffic"),
    "consider_traffic_estimate": _flag("avoid roads with estimated heavy traffic"),
    "consider_noise": _flag("prefer quiet surroundings"),
    "avoid_noise": _flag("prefer quiet surroundings"),
    "consider_town": _flag("bypass towns"),
    "avoid_towns": _flag("bypass towns"),
    "consider_forest": _flag("prefer forests and parks"),
    "prefer_forests": _flag("prefer forests and parks"),
    "consider_river": _flag("prefer rivers and lakes"),
    "prefer_rivers": _flag("prefer rivers and lakes"),
    "avoid_unsafe": _flag("avoid standard highways"),
    "prefer_cycle_routes": _flag("prefer signed cycle routes"),
    "stick_to_cycleroutes": _flag("follow signed cycle routes closely"),
    "ignore_cycleroutes": _flag("ignore signed cycle routes"),
    "use_proposed_cycleroutes": _flag("treat proposed cycle routes like existing ones"),
    "allow_steps": _flag("allow steps"),
    "allow_ferries": _flag("allow ferries"),
    "prefer_unpaved_paths": _flag("prefer unpaved paths"),
    "assume_wet_conditions": _flag("assume wet ground"),
    "avoid_steep_inclines": _flag("avoid steep inclines"),
    "consider_elevation": _flag("count climbing as a cost"),
    "unpavedPenalty": _number(1, 10, "extra cost factor on unpaved ways (1 = none)"),
    # how much climbing costs; very high values only add detours when a climb cannot be avoided
    "uphillcost": _number(0, 200, "cost per metre climbed per km"),
    "downhillcost": _number(0, 200, "cost per metre descended per km"),
    "uphillcutoff": _number(0, 10, "gradient (%) below which climbing is free"),
    "downhillcutoff": _number(0, 10, "gradient (%) below which descending is free"),
    # the rider: these change the time estimate, not the route
    "totalMass": _number(20, 300, "bike + rider in kg"),
    "maxSpeed": _number(5, 80, "top speed in km/h"),
    "bikerPower": _number(20, 1000, "average power in W"),
    "S_C_x": _number(0.05, 1.5, "drag area (m^2, with half the air density)"),
    "C_r": _number(0.001, 0.05, "rolling resistance coefficient"),
}

_PHYSICS = ("totalMass", "maxSpeed", "bikerPower", "S_C_x", "C_r")
_CLIMBING = ("uphillcost", "downhillcost", "uphillcutoff", "downhillcutoff", "consider_elevation")
_TREKKING = (
    "consider_traffic", "consider_noise", "consider_town", "consider_forest", "consider_river",
    "allow_steps", "allow_ferries", *_CLIMBING, *_PHYSICS,
)  # fmt: skip
_CYCLE_ROUTES = ("avoid_unsafe", "stick_to_cycleroutes", "ignore_cycleroutes")
_GRAVEL = (
    "consider_traffic_estimate", "avoid_noise", "avoid_towns", "prefer_forests", "prefer_rivers",
    "prefer_cycle_routes", "use_proposed_cycleroutes", "prefer_unpaved_paths",
    "assume_wet_conditions", "avoid_steep_inclines", "consider_elevation", *_PHYSICS,
)  # fmt: skip

# BRouter profile -> the settings a request may change. Profiles not listed (mtb, the
# vm-forum recumbent and velomobil profiles) have none that this service sets.
PROFILE_SETTINGS: dict[str, tuple[str, ...]] = {
    "custom_touring-v1": (*_TREKKING, *_CYCLE_ROUTES, "use_proposed_cycleroutes", "unpavedPenalty"),
    "custom_commuter-v1": (*_TREKKING, *_CYCLE_ROUTES),
    "custom_gravel-v1": _GRAVEL,
    "custom_gravel-v2": _GRAVEL,
    "trekking": (*_TREKKING, *_CYCLE_ROUTES),
    "fastbike": _TREKKING,
    "fastbike-verylowtraffic": _TREKKING,
}

# Markers that exist in the profile files but are not offered: not routing preferences.
NOT_OFFERED = frozenset(
    {
        "add_beeline", "allow_motorways", "considerTurnRestrictions", "correctMisplacedViaPoints",
        "correctMisplacedViaPointsDistance", "processUnusedTags", "turnInstructionCatchingRange",
        "turnInstructionMode", "turnInstructionRoundabouts",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Resolved:
    """What to send to BRouter, and what could not be applied."""

    values: dict[str, int | float]
    warnings: list[str]


def _plain(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(float(value), 6)


def _not_a_setting(profile: str, offered: tuple[str, ...], name: str) -> str:
    """Why ``name`` was not applied: another profile's setting, a likely typo, or unknown."""
    head = f"profile_params '{name}' is not a setting of {profile}"
    if name in SETTINGS:  # it exists, for a different profile
        return f"{head}; not applied"
    close = difflib.get_close_matches(name, offered, n=2, cutoff=0.7)
    if close:
        return f"{head} (did you mean {' or '.join(repr(c) for c in close)}?); not applied"
    return f"{head} (settings: {', '.join(offered)}); not applied"


def resolve(profile: str, requested: Mapping[str, bool | float]) -> Resolved:
    """Settings for ``profile`` from a request's ``profile_params``, with warnings for the rest."""
    values: dict[str, int | float] = {}
    warnings: list[str] = []
    offered = PROFILE_SETTINGS.get(profile)
    for name, value in requested.items():
        if offered is None:
            warnings.append(
                f"profile_params '{name}' was not applied: {profile} has no per-request settings"
            )
            continue
        if name not in offered:
            warnings.append(_not_a_setting(profile, offered, name))
            continue
        setting = SETTINGS[name]
        if setting.kind == "flag":
            if isinstance(value, bool):
                values[name] = int(value)
            elif value in (0, 1):
                values[name] = int(value)
            else:
                warnings.append(
                    f"profile_params '{name}' is on/off: use true or false; not applied"
                )
            continue
        if isinstance(value, bool) or not math.isfinite(value):
            warnings.append(f"profile_params '{name}' needs a number; not applied")
            continue
        clamped = min(max(float(value), setting.low), setting.high)
        if clamped != float(value):
            warnings.append(
                f"profile_params '{name}'={_plain(value)} is outside {_plain(setting.low)}.."
                f"{_plain(setting.high)} for {profile}; used {_plain(clamped)}"
            )
        values[name] = _plain(clamped)
    return Resolved(values, warnings)
