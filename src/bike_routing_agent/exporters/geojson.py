"""GeoJSON export for a selected route candidate."""

from __future__ import annotations

import json
from typing import Any

from bike_routing_agent.models import RouteCandidate

_VALID_GEOMETRY_TYPES = {"LineString", "MultiLineString"}


def to_geojson_feature(candidate: RouteCandidate) -> dict[str, Any]:
    geometry_type = candidate.geometry_geojson.get("type")
    if geometry_type not in _VALID_GEOMETRY_TYPES:
        raise ValueError(f"unsupported geometry type for export: {geometry_type!r}")

    return {
        "type": "Feature",
        "geometry": candidate.geometry_geojson,
        "properties": {
            "provider": candidate.provider,
            "provider_profile": candidate.provider_profile,
            "distance_m": candidate.metrics.distance_m,
            "duration_s": candidate.metrics.duration_s,
            "ascent_m": candidate.metrics.ascent_m,
            "descent_m": candidate.metrics.descent_m,
            "score": candidate.score,
            "warnings": candidate.warnings,
        },
    }


def to_geojson_str(candidate: RouteCandidate, *, indent: int | None = 2) -> str:
    return json.dumps(to_geojson_feature(candidate), indent=indent)
