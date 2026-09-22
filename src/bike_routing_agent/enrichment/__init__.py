"""OSM surface/access/road-class enrichment (issue #3).

- ``base``: the ``SurfaceEnricher`` protocol and ``SurfaceSummary`` result.
- ``quality``: the data-quality policy for conflicting/missing OSM tags.
- ``geometry``: local-plane route/way matching helpers.
- ``overpass``: prototype Overpass-backed enricher (production design is
  PostGIS, see docs/enrichment.md).
"""

from bike_routing_agent.enrichment.base import SurfaceEnricher, SurfaceSummary
from bike_routing_agent.enrichment.overpass import OverpassEnricher
from bike_routing_agent.enrichment.quality import WayClass, classify_way

__all__ = [
    "OverpassEnricher",
    "SurfaceEnricher",
    "SurfaceSummary",
    "WayClass",
    "classify_way",
]
