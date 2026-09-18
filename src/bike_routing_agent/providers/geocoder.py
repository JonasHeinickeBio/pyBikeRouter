"""OSM Nominatim geocoding adapter."""

from __future__ import annotations

import hashlib
import json
import re
from typing import cast

import httpx

from bike_routing_agent.errors import GeocodingNotFoundError, ProviderBadResponseError
from bike_routing_agent.models import Coordinate, GeocodeCandidate
from bike_routing_agent.providers.base import CacheBackend, InMemoryTTLCache

# Confidence floors applied to results whose match precision exceeds what
# Nominatim's popularity-based "importance" can express: an exact house-number
# match or a unique street match is unambiguous even when importance is ~0.
HOUSE_NUMBER_CONFIDENCE = 0.9
STREET_MATCH_CONFIDENCE = 0.6

_ABBREV_STR = re.compile(r"(?i)(?<=[\wäöüß])str\.?(?=$|[\s,])")
_TRAILING_IN = re.compile(r"(?i)\s+in\s+")
_HOUSE_NUMBER = re.compile(r"(?<!\d)(\d{1,4}[a-zA-Z]?)(?!\d)")
_POSTAL_CITY = re.compile(r"^(\d{4,6})\s+(.+)$")
_STREET_NUMBER = re.compile(r"^(.+?)\s+(\d{1,4}[a-zA-Z]?)$")


def normalize_query(query: str) -> str:
    """Light German-address normalisation: whitespace, 'str.' abbreviations,
    'Strasse' spellings and an 'in' separator between street and city."""
    q = " ".join(query.split())
    q = re.sub(r"(?i)\bstrasse\b", "straße", q)
    q = _ABBREV_STR.sub("straße", q)
    if "," not in q:
        q = _TRAILING_IN.sub(", ", q, count=1)
    return q


def extract_house_number(query: str) -> str | None:
    match = _HOUSE_NUMBER.search(query)
    if match is None:
        return None
    return match.group(1).lower().lstrip("0") or "0"


def parse_structured_query(normalized: str) -> dict[str, str] | None:
    """Derive Nominatim structured params (street/postalcode/city) from a
    comma-separated 'Street 23, 12345 City' style query, else None."""
    parts = [p.strip() for p in normalized.split(",") if p.strip()]
    street = postalcode = city = None
    for part in parts:
        pc = _POSTAL_CITY.match(part)
        if pc and postalcode is None:
            postalcode, city = pc.group(1), pc.group(2)
            continue
        sn = _STREET_NUMBER.match(part)
        if sn and street is None:
            street = f"{sn.group(1)} {sn.group(2)}"
    if street is None or (postalcode is None and city is None):
        return None
    params = {"street": street}
    if postalcode:
        params["postalcode"] = postalcode
    if city:
        params["city"] = city
    return params


def _cache_key(query: str, limit: int) -> str:
    return hashlib.sha256(f"{query}|{limit}".encode()).hexdigest()


class NominatimGeocoder:
    """GeocodeProvider backed by the OSM Nominatim search API."""

    name = "nominatim"

    def __init__(
        self,
        *,
        base_url: str,
        user_agent: str,
        timeout_s: float,
        cache: CacheBackend | None = None,
        cache_ttl_s: float = 3600.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent
        self._timeout_s = timeout_s
        self._cache = cache if cache is not None else InMemoryTTLCache()
        self._cache_ttl_s = cache_ttl_s
        self._client = client

    async def geocode(self, query: str, *, limit: int = 5) -> list[GeocodeCandidate]:
        normalized = normalize_query(query)
        key = _cache_key(normalized, limit)
        cached = await self._cache.get(key)
        if cached is not None:
            return [GeocodeCandidate.model_validate(c) for c in cast(list, cached)]

        params: dict[str, str] = {
            "q": normalized,
            "format": "jsonv2",
            "limit": str(limit),
            "addressdetails": "1",
        }
        payload = await self._request(params, normalized)

        if not payload:
            structured = parse_structured_query(normalized)
            if structured is not None:
                payload = await self._request(
                    {**structured, "format": "jsonv2", "limit": str(limit), "addressdetails": "1"},
                    normalized,
                )

        if not payload:
            raise GeocodingNotFoundError(f"no geocoding results for '{query}'", query=query)

        candidates = self._normalize(payload, normalized)
        dumped = [c.model_dump(mode="json") for c in candidates]
        await self._cache.set(key, dumped, ttl_s=self._cache_ttl_s)
        return candidates

    async def _request(self, params: dict[str, str], query: str) -> list[dict]:
        headers = {"User-Agent": self._user_agent}
        search_url = f"{self._base_url}/search"
        try:
            if self._client is not None:
                response = await self._client.get(
                    search_url, params=params, headers=headers, timeout=self._timeout_s
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.get(search_url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderBadResponseError(
                "geocoder request timed out", provider=self.name, detail={"query": query}
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderBadResponseError(
                f"geocoder returned HTTP {exc.response.status_code}",
                provider=self.name,
                detail={"query": query, "status_code": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderBadResponseError(
                "geocoder request failed",
                provider=self.name,
                detail={"query": query, "error": str(exc)},
            ) from exc
        except json.JSONDecodeError as exc:
            raise ProviderBadResponseError(
                "geocoder returned invalid JSON", provider=self.name, detail={"query": query}
            ) from exc

        if not isinstance(payload, list):
            raise ProviderBadResponseError(
                "unexpected geocoder response shape", provider=self.name, detail={"query": query}
            )
        return payload

    def _normalize(self, payload: list[dict], query: str) -> list[GeocodeCandidate]:
        candidates: list[GeocodeCandidate] = []
        query_number = extract_house_number(query)
        for item in payload:
            try:
                lon = float(item["lon"])
                lat = float(item["lat"])
                label = str(item.get("display_name", ""))
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderBadResponseError(
                    "malformed geocoder result entry", provider=self.name, detail={"item": item}
                ) from exc
            confidence = self._confidence(item, query, query_number, label)
            candidates.append(
                GeocodeCandidate(
                    label=label,
                    coordinate=Coordinate(lon=lon, lat=lat),
                    confidence=confidence,
                    source=self.name,
                )
            )
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        return candidates

    @staticmethod
    def _confidence(item: dict, query: str, query_number: str | None, label: str) -> float:
        importance = float(item.get("importance") or 0.0)
        confidence = max(0.0, min(1.0, importance))
        raw_address = item.get("address")
        address: dict = raw_address if isinstance(raw_address, dict) else {}
        result_number = str(address.get("house_number") or "").lower().lstrip("0")
        if query_number and result_number == query_number and _street_in_label(query, label):
            return max(confidence, HOUSE_NUMBER_CONFIDENCE)
        if (
            query_number is None
            and item.get("category") == "highway"
            and _street_in_label(query, label)
        ):
            return max(confidence, STREET_MATCH_CONFIDENCE)
        return confidence


def _street_in_label(query: str, label: str) -> bool:
    first_segment = query.split(",")[0].strip()
    tokens = [t.lower() for t in first_segment.split() if not _HOUSE_NUMBER.fullmatch(t)]
    lowered = label.lower()
    return bool(tokens) and all(t in lowered for t in tokens)
