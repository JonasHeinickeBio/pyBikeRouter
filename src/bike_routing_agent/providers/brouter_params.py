"""Settings of the BRouter profiles that a request may change.

BRouter takes a profile variable per request as ``profile:<name>=<number>``. It is picky in
ways that fail quietly or loudly: a name the profile does not have is ignored (a typo does
nothing), a boolean must be ``1``/``0`` (``true`` is HTTP 500), and a variable the profile
computes itself is HTTP 500 too. So a request never sends a name straight through: it is
looked up here, per profile, clamped to a range that is safe to route with, and anything that
could not be applied comes back as a warning instead.

The parameter space itself is defined once, in ``profile_settings.linkml.yaml`` (LinkML), and
the pydantic models in ``profile_settings_model.py`` are generated from it
(``scripts/gen_profile_settings.sh``). Everything below is read from those models: which
settings a profile has, whether one is on/off or a number, its range and its meaning. Only
the mapping from BRouter profile name to model class is written by hand. Only routing
preferences and the rider's physics are in the schema; turn instructions, debugging,
via-point correction and ``allow_motorways`` are left out on purpose. Effects measured in
docs/providers.md and docs/profile-experiment.md.
"""

from __future__ import annotations

import difflib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from annotated_types import Ge, Le
from pydantic import BaseModel, ValidationError

from bike_routing_agent.providers import profile_settings_model as models

# BRouter profile -> the model of its settings. Profiles not listed (mtb, the vm-forum
# recumbent and velomobil profiles) have none that this service sets.
PROFILE_MODELS: dict[str, type[BaseModel]] = {
    "custom_touring-v1": models.TouringV1,
    "custom_commuter-v1": models.CommuterV1,
    "custom_gravel-v1": models.GravelV1,
    "custom_gravel-v2": models.GravelV2,
    "trekking": models.Trekking,
    "fastbike": models.Fastbike,
    "fastbike-verylowtraffic": models.FastbikeVerylowtraffic,
}

# Markers that exist in the profile files but are not in the schema: not routing preferences.
NOT_OFFERED = frozenset(
    {
        "add_beeline", "allow_motorways", "considerTurnRestrictions", "correctMisplacedViaPoints",
        "correctMisplacedViaPointsDistance", "processUnusedTags", "turnInstructionCatchingRange",
        "turnInstructionMode", "turnInstructionRoundabouts",
    }
)  # fmt: skip

# Which schema class declares a setting -> what to call the group in a listing.
_GROUPS = {
    "RiderPhysics": "rider",
    "ClimbingCosts": "climbing",
    "CycleRoutePreferences": "cycle routes",
}


@dataclass(frozen=True)
class Setting:
    name: str
    kind: Literal["flag", "number"]
    low: float = 0.0
    high: float = 1.0
    meaning: str = ""
    group: str = "preferences"

    @property
    def affects(self) -> Literal["route", "time"]:
        """Whether the setting changes the route or only the time estimate."""
        return "time" if self.group == "rider" else "route"

    @property
    def range_text(self) -> str:
        return "on/off" if self.kind == "flag" else f"{_plain(self.low)}..{_plain(self.high)}"

    def as_dict(self) -> dict[str, Any]:
        found: dict[str, Any] = {
            "name": self.name,
            "type": "boolean" if self.kind == "flag" else "number",
            "group": self.group,
            "affects": self.affects,
            "meaning": self.meaning,
        }
        if self.kind == "number":
            found["minimum"], found["maximum"] = _plain(self.low), _plain(self.high)
        return found


def _plain(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(float(value), 6)


def _setting(name: str, field: Any) -> Setting:
    if field.annotation in (bool, bool | None):
        kind: Literal["flag", "number"] = "flag"
    else:
        kind = "number"
    low: Any = next((m.ge for m in field.metadata if isinstance(m, Ge)), 0.0)
    high: Any = next((m.le for m in field.metadata if isinstance(m, Le)), 1.0)
    declared = ((field.json_schema_extra or {}).get("linkml_meta") or {}).get("domain_of") or []
    group = next((_GROUPS[d] for d in declared if d in _GROUPS), "preferences")
    return Setting(name, kind, float(low), float(high), field.description or "", group)


def _build() -> dict[str, dict[str, Setting]]:
    return {
        profile: {name: _setting(name, field) for name, field in model.model_fields.items()}
        for profile, model in PROFILE_MODELS.items()
    }


# profile -> setting name -> Setting, all read from the generated models
PROFILE_SPECS: dict[str, dict[str, Setting]] = _build()
PROFILE_SETTINGS: dict[str, tuple[str, ...]] = {p: tuple(s) for p, s in PROFILE_SPECS.items()}
# every setting once; a name has the same type and range wherever it appears
SETTINGS: dict[str, Setting] = {
    name: setting for specs in PROFILE_SPECS.values() for name, setting in specs.items()
}


def settings_for(profile: str) -> list[Setting]:
    """The settings ``profile`` has, in the schema's order (empty for a profile without any)."""
    return list(PROFILE_SPECS.get(profile, {}).values())


def describe(profile: str) -> dict[str, Any]:
    """The parameter space of ``profile`` as plain data (API, CLI, MCP)."""
    model = PROFILE_MODELS.get(profile)
    return {
        "profile": profile,
        "settings": [s.as_dict() for s in settings_for(profile)],
        "json_schema": model.model_json_schema() if model is not None else None,
    }


def validate(profile: str, requested: Mapping[str, bool | float]) -> dict[str, bool | float]:
    """``requested`` checked strictly against the profile's model (an unknown name or an
    out-of-range value raises ``ValidationError``); returns the checked settings. For input that
    should fail early, like a command line; a request is handled leniently by :func:`resolve`."""
    model = PROFILE_MODELS.get(profile)
    if model is None:
        raise ValueError(f"{profile} has no per-request settings")
    return model.model_validate(dict(requested)).model_dump(exclude_none=True)


def problems(error: ValidationError, profile: str) -> list[str]:
    """A strict validation error as short sentences, with a did-you-mean for unknown names."""
    offered = PROFILE_SETTINGS.get(profile, ())
    found = []
    for item in error.errors():
        name = str(item["loc"][0]) if item["loc"] else "?"
        if item["type"] == "extra_forbidden":
            found.append(_not_a_setting(profile, offered, name).removesuffix("; not applied"))
        else:
            found.append(f"{name}: {item['msg']}")
    return found


@dataclass(frozen=True)
class Resolved:
    """What to send to BRouter, and what could not be applied."""

    values: dict[str, int | float]
    warnings: list[str]


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
    specs = PROFILE_SPECS.get(profile)
    for name, value in requested.items():
        if specs is None:
            warnings.append(
                f"profile_params '{name}' was not applied: {profile} has no per-request settings"
            )
            continue
        setting = specs.get(name)
        if setting is None:
            warnings.append(_not_a_setting(profile, PROFILE_SETTINGS[profile], name))
            continue
        if setting.kind == "flag":
            if isinstance(value, bool) or value in (0, 1):
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
