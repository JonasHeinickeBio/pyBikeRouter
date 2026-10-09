"""The catalogue of POI categories and the OpenStreetMap tags behind each.

A category is a *question a rider asks* ("where is a viewpoint / a drinking
water tap?"), not an OSM tag, so the UI can offer a short list while the
fetcher translates it into Overpass selectors.

Two kinds, because they behave differently on a route:

- ``sight``: something worth a detour (viewpoint, castle, waterfall, ...). These
  can be ranked by how well known they are and used as route stops.
- ``service``: something you need on the way (water, food, a bike shop). Only
  useful close to the route, never "famous", never used as a route stop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PoiKind = Literal["sight", "service"]


@dataclass(frozen=True)
class PoiCategory:
    key: str
    label: str
    kind: PoiKind
    # Overpass tag filters, each one an AND of bracketed tests; the category is
    # their OR. Kept as data so a new category needs no code.
    selectors: tuple[str, ...]


CATEGORIES: tuple[PoiCategory, ...] = (
    PoiCategory("viewpoint", "Viewpoints", "sight", ('["tourism"="viewpoint"]',)),
    PoiCategory("attraction", "Attractions", "sight", ('["tourism"="attraction"]',)),
    PoiCategory(
        "historic",
        "Castles & historic sites",
        "sight",
        (
            '["historic"~"^(castle|ruins|fort|manor|palace|city_gate|tower|monastery'
            '|archaeological_site|monument)$"]',
            # Memorials are everywhere; only the ones linked into Wikidata / Wikipedia.
            '["historic"="memorial"]["wikidata"]',
            '["historic"="memorial"]["wikipedia"]',
        ),
    ),
    PoiCategory("museum", "Museums & galleries", "sight", ('["tourism"~"^(museum|gallery)$"]',)),
    PoiCategory(
        "nature",
        "Nature",
        "sight",
        (
            '["natural"~"^(waterfall|cave_entrance|hot_spring|geyser|arch)$"]',
            # Unnamed spot heights are noise; the notable peaks are linked into Wikidata.
            '["natural"="peak"]["wikidata"]',
            '["natural"="peak"]["wikipedia"]',
        ),
    ),
    PoiCategory(
        "religious",
        "Churches & cathedrals",
        "sight",
        (
            '["amenity"="place_of_worship"]["wikidata"]',
            '["amenity"="place_of_worship"]["wikipedia"]',
        ),
    ),
    PoiCategory(
        "swimming",
        "Swimming spots",
        "sight",
        ('["natural"="beach"]', '["leisure"="swimming_area"]'),
    ),
    PoiCategory(
        "food",
        "Cafés & food",
        "service",
        ('["amenity"~"^(cafe|biergarten|ice_cream)$"]',),
    ),
    PoiCategory("water", "Drinking water", "service", ('["amenity"="drinking_water"]',)),
    PoiCategory(
        "rest",
        "Shelters & picnic spots",
        "service",
        ('["amenity"="shelter"]', '["tourism"="picnic_site"]'),
    ),
    PoiCategory(
        "bike_service",
        "Bike shops & repair",
        "service",
        ('["amenity"="bicycle_repair_station"]', '["shop"="bicycle"]'),
    ),
)

CATEGORY_BY_KEY: dict[str, PoiCategory] = {c.key: c for c in CATEGORIES}
SIGHT_KEYS: tuple[str, ...] = tuple(c.key for c in CATEGORIES if c.kind == "sight")
ALL_KEYS: tuple[str, ...] = tuple(c.key for c in CATEGORIES)

# Services are only worth a few hundred metres of detour.
SERVICE_MAX_OFFSET_M = 500.0


def resolve_categories(keys: list[str] | tuple[str, ...] | None) -> list[PoiCategory]:
    """Categories for the given keys (default: all); unknown keys are an error."""
    if not keys:
        return list(CATEGORIES)
    unknown = sorted(set(keys) - set(CATEGORY_BY_KEY))
    if unknown:
        raise ValueError(f"unknown POI categories {unknown}; known: {list(ALL_KEYS)}")
    # Stable, de-duplicated, in catalogue order.
    wanted = set(keys)
    return [c for c in CATEGORIES if c.key in wanted]


def category_for_tags(tags: dict[str, str], allowed: list[PoiCategory]) -> PoiCategory | None:
    """The first of ``allowed`` whose selectors match an element's tags.

    Overpass answers the union of all selectors, so the category has to be
    worked out again from the tags that came back.
    """
    for category in allowed:
        if any(_matches(selector, tags) for selector in category.selectors):
            return category
    return None


def _matches(selector: str, tags: dict[str, str]) -> bool:
    # Selectors are a fixed set of `["key"]`, `["key"="v"]` and `["key"~"^(a|b)$"]` tests.
    for part in selector.split("]["):
        test = part.strip("[]")
        if "~" in test:
            key, _, pattern = test.partition("~")
            key = key.strip('"')
            options = pattern.strip('"').removeprefix("^(").removesuffix(")$").split("|")
            if tags.get(key) not in options:
                return False
        elif "=" in test:
            key, _, value = test.partition("=")
            if tags.get(key.strip('"')) != value.strip('"'):
                return False
        elif test.strip('"') not in tags:
            return False
    return True
