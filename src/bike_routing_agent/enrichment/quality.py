"""Data-quality policy for OSM surface/access tags (issue #3).

The scoring step must never see a guessed surface as a real one, so this
module is the single place that decides what the raw OSM tag soup means.
Rules, in order of evidence strength:

1. an explicit ``surface`` tag wins over everything else (category from
   ``config.SURFACE_TAXONOMY``; colon-suffixed values such as
   ``paving_stones:30`` normalise to their base value);
2. otherwise ``tracktype`` (``config.TRACKTYPE_TAXONOMY``) provides an
   *inferred* category;
3. otherwise the boolean tags ``paved=yes`` -> paved and ``native=yes``
   -> natural_soft provide an *inferred* category. ``paved=no`` /
   ``native=no`` say only what a way is *not*, so they stay unknown;
4. contradictory tags cancel out to a *conflict*, which is reported as
   unknown (never as a favorable category):
   - ``paved=yes`` together with ``native=yes``,
   - ``paved=yes`` together with a loose/natural-soft ``surface``,
   - ``paved=no`` together with a hard (paved/masonry) ``surface``,
   - ``native=yes`` together with a hard ``surface``.

Values outside the taxonomy (``surface=trail``, ``surface=unpaved``,
missing tags entirely) are unknown -- unknown stays unknown. The
taxonomy itself lives in ``config.py`` with the other data vocabularies.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from bike_routing_agent.config import SURFACE_TAXONOMY, TRACKTYPE_TAXONOMY
from bike_routing_agent.enrichment.base import SurfaceSummary

EXPLICIT = "explicit"
INFERRED = "inferred"

_TRUE_VALUES = frozenset({"yes", "true"})
_FALSE_VALUES = frozenset({"no", "false"})

# Categories used by the conflict rules: "hard" behaves like paving,
# "soft" behaves like unbound natural material. compacted sits between
# them and never conflicts with paved=yes/no (compacted gravel is a
# legitimately ambiguous OSM combination).
_HARD_CATEGORIES = frozenset({"paved", "masonry"})
_SOFT_CATEGORIES = frozenset({"loose", "natural_soft"})


@dataclass(frozen=True)
class WayClass:
    """What one OSM way says about surface quality.

    ``category`` is a taxonomy category or ``None`` (unknown/conflict);
    ``source`` records the evidence strength (``explicit`` from a
    ``surface`` tag, ``inferred`` from fallback tags, ``None`` when no
    usable evidence exists).
    """

    category: str | None
    source: str | None
    conflict: bool
    highway: str | None


def normalize_tag_value(value: str) -> str:
    """Lower-case and strip a colon suffix: ``paving_stones:30`` -> paving_stones."""
    return value.strip().lower().split(":", 1)[0]


def _tri_state(value: object) -> bool | None:
    if not isinstance(value, str):
        return None
    normalized = normalize_tag_value(value)
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return None


def _tag_str(tags: dict, key: str) -> str | None:
    value = tags.get(key)
    return value if isinstance(value, str) and value.strip() else None


def classify_way(tags: dict) -> WayClass:
    """Apply the data-quality policy to one way's tag dict."""
    highway = _tag_str(tags, "highway")

    surface_tag = _tag_str(tags, "surface")
    surface_category = (
        SURFACE_TAXONOMY.get(normalize_tag_value(surface_tag)) if surface_tag else None
    )
    tracktype_tag = _tag_str(tags, "tracktype")
    tracktype_category = (
        TRACKTYPE_TAXONOMY.get(normalize_tag_value(tracktype_tag)) if tracktype_tag else None
    )
    paved = _tri_state(tags.get("paved"))
    native = _tri_state(tags.get("native"))

    if _has_conflict(paved=paved, native=native, surface_category=surface_category):
        return WayClass(category=None, source=None, conflict=True, highway=highway)

    if surface_category is not None:
        return WayClass(
            category=surface_category, source=EXPLICIT, conflict=False, highway=highway
        )
    if tracktype_category is not None:
        return WayClass(
            category=tracktype_category, source=INFERRED, conflict=False, highway=highway
        )
    if paved is True:
        return WayClass(category="paved", source=INFERRED, conflict=False, highway=highway)
    if native is True:
        return WayClass(
            category="natural_soft", source=INFERRED, conflict=False, highway=highway
        )
    return WayClass(category=None, source=None, conflict=False, highway=highway)


def _has_conflict(
    *, paved: bool | None, native: bool | None, surface_category: str | None
) -> bool:
    if paved is True and native is True:
        return True
    if surface_category is None:
        return False
    if paved is True and surface_category in _SOFT_CATEGORIES:
        return True
    if paved is False and surface_category in _HARD_CATEGORIES:
        return True
    return native is True and surface_category in _HARD_CATEGORIES


def build_summary(
    segments: Sequence[tuple[float, WayClass | None]],
) -> SurfaceSummary:
    """Aggregate per-segment classifications into a route summary.

    Each entry is ``(length_m, way_class)`` where ``way_class`` is the
    classification of the way matched to that route segment, or ``None``
    when the segment could not be matched to any way. All fractions are
    relative to the total segment length.
    """
    total_m = sum(length for length, _ in segments)
    if total_m <= 0:
        return SurfaceSummary(total_m=0.0, unknown_fraction=1.0)

    coverage_m: dict[str, float] = {}
    highway_m: dict[str, float] = {}
    unknown_m = 0.0
    inferred_m = 0.0
    conflict_m = 0.0

    for length, way_class in segments:
        if way_class is None:  # unmatched geometry: unknown, no road class either
            unknown_m += length
            continue
        if way_class.highway:
            highway_m[way_class.highway] = highway_m.get(way_class.highway, 0.0) + length
        if way_class.conflict:
            conflict_m += length
            unknown_m += length
        elif way_class.category is not None:
            category = way_class.category
            coverage_m[category] = coverage_m.get(category, 0.0) + length
            if way_class.source == INFERRED:
                inferred_m += length
        else:
            unknown_m += length

    return SurfaceSummary(
        total_m=total_m,
        coverage={name: meters / total_m for name, meters in sorted(coverage_m.items())},
        unknown_fraction=unknown_m / total_m,
        inferred_fraction=inferred_m / total_m,
        conflict_fraction=conflict_m / total_m,
        highway_fractions={name: meters / total_m for name, meters in sorted(highway_m.items())},
    )
