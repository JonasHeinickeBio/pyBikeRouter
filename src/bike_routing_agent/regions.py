"""Which BRouter map tiles cover a place you ride in: presets and "around a town".

BRouter routes only where it has the 5 x 5 degree tiles (``E10_N50.rd5``: east 10-15 degrees,
north 50-55 degrees). Most people want "Germany" or "around my town", not a list of tile
names, so the easy-install wizard (``bike-router setup``) offers these.

The presets are curated by hand from the tile grid: each lists the tiles that cover a country's
cycling-relevant area. A few fringes (Sylt, the Faroe-side of Scotland, Corsica) fall outside the
listed tiles; "around a town" or ``--tiles`` covers those.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from bike_routing_agent.providers.brouter_downloads import SEGMENT_NAME_RE

TILE_DEGREES = 5


@dataclass(frozen=True)
class Region:
    key: str
    label: str
    tiles: tuple[str, ...]
    note: str = ""


REGIONS: tuple[Region, ...] = (
    Region(
        "germany",
        "Germany",
        ("E5_N45", "E5_N50", "E10_N45", "E10_N50"),
        "all of it except the island of Sylt",
    ),
    Region(
        "austria_switzerland",
        "Austria and Switzerland",
        ("E5_N45", "E10_N45", "E15_N45"),
        "",
    ),
    Region(
        "benelux",
        "Netherlands, Belgium and Luxembourg",
        ("E0_N50", "E5_N50"),
        "",
    ),
    Region(
        "denmark",
        "Denmark",
        ("E5_N55", "E10_N55"),
        "",
    ),
    Region(
        "great_britain",
        "Great Britain",
        ("W10_N50", "W5_N50", "E0_N50", "W10_N55", "W5_N55"),
        "England, Wales and Scotland",
    ),
    Region("ireland", "Ireland", ("W10_N50",), "the whole island except its northern tip"),
    Region(
        "france",
        "France",
        ("W5_N40", "E0_N40", "E5_N40", "W5_N45", "E0_N45", "E5_N45", "W5_N50", "E0_N50"),
        "mainland France; about 8 tiles, a large download",
    ),
    Region(
        "italy",
        "Italy",
        ("E5_N40", "E10_N40", "E15_N40", "E5_N45", "E10_N45", "E10_N35", "E15_N35"),
        "mainland Italy, Sicily and Sardinia; about 7 tiles, a large download",
    ),
)

REGIONS_BY_KEY = {r.key: r for r in REGIONS}


def tile_name(lon_edge: int, lat_edge: int) -> str:
    """The tile whose south-west corner is at (``lon_edge``, ``lat_edge``) degrees."""
    return (
        f"{'E' if lon_edge >= 0 else 'W'}{abs(lon_edge)}"
        f"_{'N' if lat_edge >= 0 else 'S'}{abs(lat_edge)}"
    )


def tiles_covering(lon_min: float, lat_min: float, lon_max: float, lat_max: float) -> list[str]:
    """The tiles that cover a bounding box, west to east and south to north."""
    if not (-180 <= lon_min <= lon_max <= 180 and -90 <= lat_min <= lat_max <= 90):
        raise ValueError("the bounding box is outside the world")
    west = math.floor(lon_min / TILE_DEGREES) * TILE_DEGREES
    # A box ending on the world's edge (180 E, 90 N) would name a tile that starts there: none does.
    east = min(math.floor(lon_max / TILE_DEGREES) * TILE_DEGREES, 180 - TILE_DEGREES)
    south = math.floor(lat_min / TILE_DEGREES) * TILE_DEGREES
    north = min(math.floor(lat_max / TILE_DEGREES) * TILE_DEGREES, 90 - TILE_DEGREES)
    return [
        tile_name(lon, lat)
        for lat in range(south, north + 1, TILE_DEGREES)
        for lon in range(west, east + 1, TILE_DEGREES)
    ]


def tiles_around(lon: float, lat: float, radius_km: float = 80.0) -> list[str]:
    """The tiles within about ``radius_km`` of a point: one to four tiles for a day trip."""
    if not 1 <= radius_km <= 600:
        raise ValueError("the radius must be between 1 and 600 km")
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.05))
    return tiles_covering(
        max(lon - dlon, -180.0),
        max(lat - dlat, -90.0),
        min(lon + dlon, 180.0),
        min(lat + dlat, 90.0),
    )


def parse_tiles(text: str) -> list[str]:
    """Tile names typed by a person ("e10_n50, W5_N50"): split, upper-cased, validated."""
    names = [
        p.strip().upper().removesuffix(".RD5")
        for p in text.replace(";", ",").replace(" ", ",").split(",")
    ]
    names = [n for n in names if n]
    bad = [n for n in names if not SEGMENT_NAME_RE.match(n)]
    if bad:
        raise ValueError(f"{', '.join(bad)} is not a tile name; they look like E10_N50 or W5_N50")
    if not names:
        raise ValueError("no tile names given")
    return list(dict.fromkeys(names))
