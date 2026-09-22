"""enrich_candidates node (issue #3).

Looks up OSM surface/access/road-class data along each candidate's geometry
and writes the result into the candidate's metrics (`surface_coverage`,
`unknown_surface_fraction`) plus a provenance record of how trustworthy the
data is. Runs between routing and scoring so the scorer sees real surface
evidence instead of the provider-neutral empty default.

Failure policy mirrors the route node: enrichment problems are structured
`errors` entries and a `failed` provenance marker, never a lost or penalised
candidate. A candidate whose enrichment failed keeps `unknown_surface_
fraction=None` -- "we do not know" must not degrade into a fabricated 1.0
the scorer could misread, and equally must not cost the candidate points.
The node therefore never emits warnings (the scorer penalises those) and
never returns a terminal status.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from bike_routing_agent.enrichment.base import SurfaceEnricher, SurfaceSummary
from bike_routing_agent.errors import ProviderError
from bike_routing_agent.models import Coordinate, RouteCandidate
from bike_routing_agent.state import RouteAgentState

NodeFn = Callable[[RouteAgentState], Awaitable[dict[str, Any]]]

PROVENANCE_KEY = "surface_enrichment"


def build_enrich_node(*, surface_enricher: SurfaceEnricher | None) -> NodeFn:
    async def enrich_candidates(state: RouteAgentState) -> dict[str, Any]:
        candidates = state.get("candidates", [])
        if surface_enricher is None or not candidates:
            return {}

        new_errors: list[dict[str, Any]] = []
        # Identical geometries from different providers share one lookup
        # (Overpass is rate-limited; the enricher may also cache internally).
        summaries: dict[str, SurfaceSummary] = {}
        failures: dict[str, dict[str, Any]] = {}
        updated: list[dict[str, Any]] = []

        for candidate in candidates:
            lookup_key, geometry_error = _geometry_key(candidate)
            summary: SurfaceSummary | None = None
            failure: dict[str, Any] | None = None

            if lookup_key is None:
                failure = geometry_error.to_dict() if geometry_error else None
            elif lookup_key in summaries:
                summary = summaries[lookup_key]
            elif lookup_key in failures:
                failure = failures[lookup_key]
            else:
                try:
                    summary = await surface_enricher.surface_profile(
                        _geometry_coordinates(candidate)
                    )
                except Exception as exc:  # noqa: BLE001 - structured below
                    error = (
                        exc
                        if isinstance(exc, ProviderError)
                        else ProviderError(
                            f"unexpected {type(exc).__name__}: {exc}",
                            provider=surface_enricher.name,
                        )
                    )
                    failure = error.to_dict()
                    failures[lookup_key] = failure
                else:
                    summaries[lookup_key] = summary

            if failure is not None and failure not in new_errors:
                new_errors.append(failure)

            updated.append(_apply_summary(candidate, summary, surface_enricher.name))

        if not new_errors:
            return {"candidates": updated}
        return {
            "candidates": updated,
            "errors": [*state.get("errors", []), *new_errors],
        }

    return enrich_candidates


def _geometry_coordinates(candidate: dict[str, Any]) -> list[Coordinate]:
    raw = candidate.get("geometry_geojson", {}).get("coordinates")
    if not isinstance(raw, list):
        raise ProviderError(
            "candidate geometry is missing coordinates", provider="enrichment"
        )
    return [Coordinate(lon=float(pair[0]), lat=float(pair[1])) for pair in raw]


def _geometry_key(
    candidate: dict[str, Any],
) -> tuple[str | None, ProviderError | None]:
    """Hashable identity for a candidate's geometry, or the parse error."""
    try:
        coordinates = _geometry_coordinates(candidate)
    except (ProviderError, TypeError, ValueError, IndexError, KeyError) as exc:
        error = (
            exc
            if isinstance(exc, ProviderError)
            else ProviderError(
                f"unreadable candidate geometry: {exc}", provider="enrichment"
            )
        )
        return None, error
    if len(coordinates) < 2:
        return None, ProviderError(
            "candidate geometry has fewer than two points", provider="enrichment"
        )
    return json.dumps([c.model_dump(mode="json") for c in coordinates]), None


def _apply_summary(
    candidate: dict[str, Any], summary: SurfaceSummary | None, enricher_name: str
) -> dict[str, Any]:
    """Rebuild the candidate dict with enrichment applied (or marked failed).

    Round-trips through ``RouteCandidate`` so the scoring node's
    re-validation cannot be surprised by the write.
    """
    validated = RouteCandidate.model_validate(candidate)
    provenance = dict(validated.provenance)
    if summary is None:
        provenance[PROVENANCE_KEY] = {"status": "failed", "source": enricher_name}
    else:
        provenance[PROVENANCE_KEY] = {
            "status": "ok",
            "source": enricher_name,
            "total_m": summary.total_m,
            "unknown_fraction": summary.unknown_fraction,
            "inferred_fraction": summary.inferred_fraction,
            "conflict_fraction": summary.conflict_fraction,
            "highway_fractions": summary.highway_fractions,
            "access_fractions": summary.access_fractions,
        }
    updated = validated.model_copy(
        update={
            "metrics": validated.metrics.model_copy(
                update={
                    "surface_coverage": dict(summary.coverage) if summary else {},
                    "unknown_surface_fraction": (
                        summary.unknown_fraction if summary else None
                    ),
                }
            ),
            "provenance": provenance,
        }
    )
    return updated.model_dump(mode="json")
