"""Enrichment protocols and result shape.

An enricher decorates routing candidates with map-metadata that routing
engines do not return (OSM surface/access/road-class tags along the route
geometry, issue #3). Like routing providers, enrichers sit behind a Protocol
so the graph node never depends on a concrete backend, and like providers
they translate transport failures into the structured `errors.py` hierarchy.

The production-scale design (PostGIS spatial joins) replaces only the
enricher implementation -- the node, `SurfaceSummary` and quality policy are
shared. See docs/enrichment.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from bike_routing_agent.models import Coordinate


@dataclass(frozen=True)
class SurfaceSummary:
    """Length-weighted surface data quality along one route.

    - ``coverage``: fraction of the total route length per *known* surface
      category (unknown is deliberately absent -- it is tracked by
      ``unknown_fraction`` and must never look favorable).
    - ``unknown_fraction``: share of route length with no usable surface
      evidence (missing tags, conflicting tags, or geometry that could not
      be matched to an OSM way).
    - ``inferred_fraction``: share of the route length whose category comes
      from fallback evidence (``tracktype`` / ``paved`` / ``native``) rather
      than an explicit ``surface`` tag.
    - ``conflict_fraction``: share whose tags contradicted each other and
      was therefore treated as unknown.
    - ``highway_fractions``: length-weighted share per ``highway`` tag value
      (road-class visibility; scoring on it is a separate follow-up).

    All fractions are relative to the route's geometric length and sum with
    ``unknown_fraction`` to 1.
    """

    total_m: float
    coverage: dict[str, float] = field(default_factory=dict)
    unknown_fraction: float = 0.0
    inferred_fraction: float = 0.0
    conflict_fraction: float = 0.0
    highway_fractions: dict[str, float] = field(default_factory=dict)


class SurfaceEnricher(Protocol):
    """Fetches surface/access/road-class data along a route geometry."""

    name: str

    async def surface_profile(
        self, coordinates: Sequence[Coordinate]
    ) -> SurfaceSummary: ...
