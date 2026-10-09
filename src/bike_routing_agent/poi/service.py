"""The POI service: find, de-duplicate, resolve fame, rank.

Ties the Overpass fetcher and the Wikimedia resolver together. Everything the
Wikimedia side adds is best effort -- if it is down the POIs are still found,
just without a fame ranking, and the result says so (``fame_status``).

Ranking never invents fame: a POI without a Wikidata item is *unranked by
fame* (sorted after the ranked ones, by distance), not "obscure".
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from bike_routing_agent.errors import ProviderError
from bike_routing_agent.poi.categories import SERVICE_MAX_OFFSET_M, PoiCategory, resolve_categories
from bike_routing_agent.poi.fetcher import OverpassPoiFetcher
from bike_routing_agent.poi.geo import LonLat, cumulative_lengths_m, haversine_m, project_onto_line
from bike_routing_agent.poi.models import Poi, PoiInfo
from bike_routing_agent.poi.wikimedia import WikimediaResolver

logger = logging.getLogger(__name__)

FameStatus = Literal["ok", "partial", "unavailable", "skipped"]

# Two elements of the same category and name this close are one place (a castle mapped as
# both a node and a building outline).
_DUPLICATE_RADIUS_M = 150.0


@dataclass(frozen=True)
class PoiSearchResult:
    pois: list[Poi]
    # True when a per-category limit dropped results.
    truncated: bool
    fame_status: FameStatus


class PoiService:
    def __init__(
        self,
        fetcher: OverpassPoiFetcher,
        resolver: WikimediaResolver,
        *,
        per_category_limit: int = 40,
    ) -> None:
        self._fetcher = fetcher
        self._resolver = resolver
        self._per_category_limit = per_category_limit

    async def along_route(
        self,
        line: Sequence[LonLat],
        category_keys: Sequence[str] | None,
        *,
        buffer_m: float,
        per_category_limit: int | None = None,
        linked_only: bool = False,
    ) -> PoiSearchResult:
        """POIs within ``buffer_m`` of ``line`` (a lon/lat polyline).

        ``linked_only`` keeps only POIs with a Wikipedia/Wikidata link -- the ones that can
        have a fame at all (used to pick famous route stops).
        """
        categories = resolve_categories(list(category_keys) if category_keys else None)
        found = await self._fetcher.along(
            line, categories, buffer_m=buffer_m, linked_only=linked_only
        )
        cumulative = cumulative_lengths_m(line)
        located: list[Poi] = []
        for poi in found:
            offset, along = project_onto_line((poi.lon, poi.lat), line, cumulative)
            limit = min(buffer_m, SERVICE_MAX_OFFSET_M) if poi.kind == "service" else buffer_m
            # The query ran on a thinned polyline; measure against the real one.
            if offset > limit:
                continue
            located.append(
                poi.model_copy(
                    update={
                        "distance_from_route_m": round(offset, 1),
                        "along_route_km": round(along / 1000, 3),
                    }
                )
            )
        return await self._finish(located, categories, per_category_limit, linked_only)

    async def in_bbox(
        self,
        bbox: tuple[float, float, float, float],
        category_keys: Sequence[str] | None,
        *,
        per_category_limit: int | None = None,
    ) -> PoiSearchResult:
        categories = resolve_categories(list(category_keys) if category_keys else None)
        found = await self._fetcher.in_bbox(bbox, categories)
        return await self._finish(found, categories, per_category_limit, False)

    async def info(
        self,
        *,
        wikidata: str | None,
        wikipedia: str | None,
        osm_id: str | None,
        website: str | None,
        lang: str,
    ) -> PoiInfo:
        return await self._resolver.info(
            wikidata=wikidata, wikipedia=wikipedia, osm_id=osm_id, website=website, lang=lang
        )

    async def _finish(
        self,
        pois: list[Poi],
        categories: list[PoiCategory],
        per_category_limit: int | None,
        linked_only: bool,
    ) -> PoiSearchResult:
        if linked_only:
            pois = [p for p in pois if p.wikidata or p.wikipedia]
        pois = dedupe(pois)
        pois, fame_status = await self._add_fame(pois)
        ranked = rank(pois)
        limit = per_category_limit or self._per_category_limit
        kept: list[Poi] = []
        counts: dict[str, int] = {}
        for poi in ranked:
            counts[poi.category] = counts.get(poi.category, 0) + 1
            if counts[poi.category] <= limit:
                kept.append(poi)
        return PoiSearchResult(
            pois=kept, truncated=len(kept) < len(ranked), fame_status=fame_status
        )

    async def _add_fame(self, pois: list[Poi]) -> tuple[list[Poi], FameStatus]:
        sights = [p for p in pois if p.kind == "sight"]
        # Only sights are ranked by fame, and only those that link into Wikimedia can have one.
        titles = {p.wikipedia for p in sights if p.wikipedia and not p.wikidata}
        if not titles and not any(p.wikidata for p in sights):
            return pois, "skipped"
        try:
            by_title = await self._resolver.wikidata_for_titles(titles) if titles else {}
            resolved = [
                p.model_copy(update={"wikidata": by_title.get(p.wikipedia or "")})
                if not p.wikidata and p.wikipedia in by_title
                else p
                for p in pois
            ]
            wanted = {p.wikidata for p in resolved if p.kind == "sight" and p.wikidata}
            fame = await self._resolver.fame(wanted)
        except ProviderError as exc:  # the resolver already absorbs these; belt and braces
            logger.warning("POI fame lookup failed: %s", exc.message)
            return pois, "unavailable"
        out = [
            p.model_copy(update={"fame": fame[p.wikidata]})
            if p.kind == "sight" and p.wikidata in fame
            else p
            for p in resolved
        ]
        if not wanted:
            return out, "ok"
        found = sum(1 for q in wanted if q in fame)
        if found == 0:
            return out, "unavailable"
        return out, "ok" if found == len(wanted) else "partial"


def dedupe(pois: list[Poi]) -> list[Poi]:
    """Drop repeats of one place: the same Wikidata item, or the same name and category nearby."""
    kept: list[Poi] = []
    by_wikidata: dict[str, int] = {}
    for poi in sorted(pois, key=_richness):
        if poi.wikidata and poi.wikidata in by_wikidata:
            continue
        if poi.name and any(
            k.name
            and k.category == poi.category
            and k.name.casefold() == poi.name.casefold()
            and haversine_m((k.lon, k.lat), (poi.lon, poi.lat)) <= _DUPLICATE_RADIUS_M
            for k in kept
        ):
            continue
        if poi.wikidata:
            by_wikidata[poi.wikidata] = len(kept)
        kept.append(poi)
    return kept


def _richness(poi: Poi) -> tuple[int, int, str]:
    # Best first: the element that says most about the place, nodes before outlines; ties
    # broken by id so the result does not depend on Overpass's ordering.
    score = sum(1 for v in (poi.wikidata, poi.wikipedia, poi.name, poi.website) if v)
    return -score, 0 if poi.id.startswith("node/") else 1, poi.id


def rank(pois: list[Poi]) -> list[Poi]:
    """Most famous first; unranked (no fame) after, nearest the route first."""

    def key(p: Poi) -> tuple[int, int, float, float]:
        return (
            0 if p.fame is not None else 1,
            -(p.fame or 0),
            p.distance_from_route_m if p.distance_from_route_m is not None else 0.0,
            p.along_route_km if p.along_route_km is not None else 0.0,
        )

    return sorted(pois, key=key)
