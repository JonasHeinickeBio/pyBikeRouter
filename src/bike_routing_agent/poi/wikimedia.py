"""Resolves POIs against the open Wikimedia services (keyless, CC0 / CC BY-SA).

Three jobs, all best effort -- a Wikimedia outage leaves a POI without extra
information, it never fails a search:

- **fame**: how many language editions describe a Wikidata item (its sitelinks).
  Stable and cheap -- 50 items per request -- and a good proxy for "well
  known"; an item that is only in one wiki is local, one in 100 is a landmark.
- **wikidata items for Wikipedia titles**: many mappers tag ``wikipedia=de:Title``
  without ``wikidata``; the page properties give the item back.
- **info**: the Wikipedia summary (text + thumbnail) and the links to the other
  Wikimedia projects (Wikivoyage, Commons) a Wikidata item carries.

Wikimedia requires a descriptive ``User-Agent`` (a generic one gets blocked).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable, Sequence
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.poi.fetcher import DEFAULT_USER_AGENT
from bike_routing_agent.poi.models import (
    LANG_RE,
    QID_RE,
    WIKIPEDIA_TAG_RE,
    PoiInfo,
    PoiLink,
)
from bike_routing_agent.providers.base import CacheBackend

logger = logging.getLogger(__name__)

WIKIDATA_BATCH = 50
_MAX_PARALLEL = 3
_EXTRACT_CHARS = 1200
_WIKI_SITE = re.compile(r"^([a-z]{2,3})wiki$")
_ATTRIBUTION = [
    "Descriptions: Wikipedia and Wikidata (CC BY-SA 4.0 / CC0)",
    "Map data: OpenStreetMap contributors (ODbL)",
]
# Preferred article languages after the requested one, then any other.
_FALLBACK_LANGS = ("en", "de", "fr", "es", "it")


class WikimediaResolver:
    name = "wikimedia"

    def __init__(
        self,
        *,
        wikidata_url: str = "https://www.wikidata.org/w/api.php",
        wikipedia_url: str = "https://{lang}.wikipedia.org",
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_s: float = 8.0,
        cache: CacheBackend | None = None,
        fame_ttl_s: float = 7 * 86_400.0,
        info_ttl_s: float = 86_400.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._wikidata_url = wikidata_url
        self._wikipedia_url = wikipedia_url
        self._user_agent = user_agent
        self._timeout_s = timeout_s
        self._cache = cache
        self._fame_ttl_s = fame_ttl_s
        self._info_ttl_s = info_ttl_s
        self._client = client

    # ------------------------------------------------------------------ fame

    async def fame(self, qids: Iterable[str]) -> dict[str, int]:
        """Sitelink count per Wikidata id; ids that could not be looked up are absent."""
        wanted = sorted({q for q in qids if QID_RE.match(q)})
        result: dict[str, int] = {}
        missing: list[str] = []
        for qid in wanted:
            cached = await self._cache_get(f"poi:fame:{qid}")
            if isinstance(cached, int) and not isinstance(cached, bool):
                result[qid] = cached
            else:
                missing.append(qid)

        gate = asyncio.Semaphore(_MAX_PARALLEL)

        async def fetch(batch: list[str]) -> None:
            async with gate:
                try:
                    payload = await self._get_json(
                        self._wikidata_url,
                        {
                            "action": "wbgetentities",
                            "ids": "|".join(batch),
                            "props": "sitelinks",
                            "format": "json",
                        },
                    )
                except ProviderError as exc:
                    logger.warning("wikidata fame lookup failed: %s", exc.message)
                    return
            entities = (payload or {}).get("entities")
            if not isinstance(entities, dict):
                logger.warning("wikidata fame lookup: unexpected response shape")
                return
            for qid in batch:
                entity = entities.get(qid)
                if not isinstance(entity, dict) or "missing" in entity:
                    continue
                sitelinks = entity.get("sitelinks")
                if not isinstance(sitelinks, dict):
                    continue  # not what was asked for: unknown, not zero
                result[qid] = len(sitelinks)
                await self._cache_set(f"poi:fame:{qid}", len(sitelinks), self._fame_ttl_s)

        await asyncio.gather(
            *(
                fetch(missing[i : i + WIKIDATA_BATCH])
                for i in range(0, len(missing), WIKIDATA_BATCH)
            )
        )
        return result

    # ----------------------------------------------------- title -> wikidata

    async def wikidata_for_titles(self, tags: Iterable[str]) -> dict[str, str]:
        """``{"de:Title": "Q123"}`` for OSM ``wikipedia`` tags without a ``wikidata`` tag."""
        by_lang: dict[str, list[str]] = {}
        for tag in sorted(set(tags)):
            match = WIKIPEDIA_TAG_RE.match(tag)
            if match:
                by_lang.setdefault(match.group(1), []).append(match.group(2))

        result: dict[str, str] = {}

        async def fetch(lang: str, titles: list[str]) -> None:
            try:
                payload = await self._get_json(
                    self._wikipedia_base(lang) + "/w/api.php",
                    {
                        "action": "query",
                        "prop": "pageprops",
                        "ppprop": "wikibase_item",
                        "titles": "|".join(titles),
                        "redirects": "1",
                        "format": "json",
                        "formatversion": "2",
                    },
                )
            except ProviderError as exc:
                logger.warning("wikipedia title lookup failed: %s", exc.message)
                return
            query = (payload or {}).get("query")
            if not isinstance(query, dict):
                return
            # Titles come back normalised ("a_b" -> "a b") and redirected; walk them back.
            # Entries of another shape are skipped: a Wikimedia oddity never fails a search.
            moves = [*_as_list(query.get("normalized")), *_as_list(query.get("redirects"))]
            moved = {
                m["from"]: m["to"]
                for m in moves
                if isinstance(m, dict) and isinstance(m.get("from"), str) and "to" in m
            }
            items = {
                p["title"]: p["pageprops"]["wikibase_item"]
                for p in _as_list(query.get("pages"))
                if isinstance(p, dict)
                and "title" in p
                and isinstance(p.get("pageprops"), dict)
                and "wikibase_item" in p["pageprops"]
            }
            for title in titles:
                seen = title
                for _ in range(3):  # normalise, then follow a redirect
                    seen = moved.get(seen, seen)
                qid = items.get(seen)
                if qid and QID_RE.match(qid):
                    result[f"{lang}:{title}"] = qid

        await asyncio.gather(
            *(
                fetch(lang, titles[i : i + WIKIDATA_BATCH])
                for lang, titles in by_lang.items()
                for i in range(0, len(titles), WIKIDATA_BATCH)
            )
        )
        return result

    # ------------------------------------------------------------------ info

    async def info(
        self,
        *,
        wikidata: str | None,
        wikipedia: str | None,
        osm_id: str | None,
        website: str | None,
        lang: str,
    ) -> PoiInfo:
        """Text, thumbnail and links for one POI. Whatever could not be fetched is left out."""
        if not LANG_RE.fullmatch(lang):
            raise ValueError(f"invalid language code {lang!r}")
        cache_key = f"poi:info:{wikidata or ''}:{wikipedia or ''}:{lang}"
        cached = await self._cache_get(cache_key)
        base: PoiInfo | None = None
        if isinstance(cached, dict):
            try:
                base = PoiInfo.model_validate(cached)
            except ValueError:
                base = None

        if base is None:
            try:
                base = await self._fetch_info(wikidata, wikipedia, lang)
            except (KeyError, TypeError, AttributeError, ValueError) as exc:
                # A response of an unexpected shape leaves the POI without extra information.
                logger.warning("wikimedia info lookup: unexpected response (%s)", exc)
                base = PoiInfo()
            # A thin result (a lookup failed) is not worth keeping for a day.
            if base.extract or base.links:
                await self._cache_set(cache_key, base.model_dump(mode="json"), self._info_ttl_s)

        links = list(base.links)
        if wikidata and QID_RE.match(wikidata):
            links.append(
                PoiLink(
                    kind="wikidata",
                    label="Wikidata",
                    url=f"https://www.wikidata.org/wiki/{wikidata}",
                )
            )
        if osm_id and re.match(r"^(node|way|relation)/\d+$", osm_id):
            links.append(
                PoiLink(
                    kind="osm", label="OpenStreetMap", url=f"https://www.openstreetmap.org/{osm_id}"
                )
            )
        if website and website.startswith(("http://", "https://")):
            links.append(PoiLink(kind="website", label="Website", url=website))
        return base.model_copy(update={"links": links, "attribution": list(_ATTRIBUTION)})

    async def _fetch_info(self, wikidata: str | None, wikipedia: str | None, lang: str) -> PoiInfo:
        label = description = None
        sitelinks: dict[str, str] = {}
        if wikidata and QID_RE.match(wikidata):
            try:
                payload = await self._get_json(
                    self._wikidata_url,
                    {
                        "action": "wbgetentities",
                        "ids": wikidata,
                        "props": "labels|descriptions|sitelinks",
                        "languages": f"{lang}|en",
                        "format": "json",
                    },
                )
            except ProviderError as exc:
                logger.warning("wikidata entity lookup failed: %s", exc.message)
                payload = None
            entity = ((payload or {}).get("entities") or {}).get(wikidata)
            if isinstance(entity, dict) and "missing" not in entity:
                label = _localised(entity.get("labels"), lang)
                description = _localised(entity.get("descriptions"), lang)
                sitelinks = {
                    key: str(value.get("title"))
                    for key, value in (entity.get("sitelinks") or {}).items()
                    if isinstance(value, dict) and value.get("title")
                }

        links: list[PoiLink] = []
        article_lang, article_title = _pick_article(sitelinks, wikipedia, lang)
        extract = thumbnail = language = None
        title = label
        if article_title and article_lang:
            summary = await self._summary(article_lang, article_title)
            if summary is not None:
                extract = summary.get("extract") or None
                if extract and len(extract) > _EXTRACT_CHARS:
                    extract = extract[:_EXTRACT_CHARS].rsplit(" ", 1)[0] + "…"
                language = article_lang
                title = summary.get("title") or title or article_title
                description = description or summary.get("description")
                thumbnail = _safe_image(summary.get("thumbnail"))
                page = ((summary.get("content_urls") or {}).get("desktop") or {}).get("page")
                links.append(
                    PoiLink(
                        kind="wikipedia",
                        label=f"Wikipedia ({article_lang})",
                        url=_https(page) or _wiki_url(article_lang, "wikipedia", article_title),
                    )
                )
            else:
                links.append(
                    PoiLink(
                        kind="wikipedia",
                        label=f"Wikipedia ({article_lang})",
                        url=_wiki_url(article_lang, "wikipedia", article_title),
                    )
                )

        voyage = _pick_site(sitelinks, "wikivoyage", lang)
        if voyage:
            links.append(
                PoiLink(
                    kind="wikivoyage",
                    label=f"Wikivoyage ({voyage[0]})",
                    url=_wiki_url(voyage[0], "wikivoyage", voyage[1]),
                )
            )
        commons = sitelinks.get("commonswiki")
        if commons:
            links.append(
                PoiLink(
                    kind="commons",
                    label="Wikimedia Commons",
                    url="https://commons.wikimedia.org/wiki/"
                    + quote(commons.replace(" ", "_"), safe=":_(),'"),
                )
            )

        return PoiInfo(
            title=title,
            description=description,
            extract=extract,
            language=language,
            thumbnail_url=thumbnail,
            sitelinks=len(sitelinks) if sitelinks else None,
            links=links,
        )

    async def _summary(self, lang: str, title: str) -> dict[str, Any] | None:
        url = (
            self._wikipedia_base(lang)
            + "/api/rest_v1/page/summary/"
            + quote(title.replace(" ", "_"), safe="")
        )
        try:
            payload = await self._get_json(url, None)
        except ProviderError as exc:
            logger.warning("wikipedia summary lookup failed: %s", exc.message)
            return None
        # A disambiguation page is not a description of the place.
        if not isinstance(payload, dict) or payload.get("type") == "disambiguation":
            return None
        return payload

    # ------------------------------------------------------------------ http

    def _wikipedia_base(self, lang: str) -> str:
        """``https://{lang}.wikipedia.org`` -- the only place a language enters a host name, so
        it is checked here: a bare two/three letter code (optionally ``-xx``), never anything
        that could change the host or add a path."""
        if not LANG_RE.fullmatch(lang):
            raise ValueError(f"invalid language code {lang!r}")
        return self._wikipedia_url.format(lang=lang)

    async def _get_json(self, url: str, params: dict[str, str] | None) -> Any:
        """GET and decode; ``None`` for a 404; one retry on timeouts and 5xx."""
        attempt = 0
        while True:
            try:
                if self._client is not None:
                    response = await self._client.get(
                        url, params=params, headers=self._headers(), timeout=self._timeout_s
                    )
                else:
                    async with httpx.AsyncClient(
                        timeout=self._timeout_s, headers=self._headers()
                    ) as client:
                        response = await client.get(url, params=params)
            except httpx.TimeoutException as exc:
                if attempt >= 1:
                    raise ProviderTimeoutError(
                        "Wikimedia request timed out", provider=self.name
                    ) from exc
                attempt += 1
                await asyncio.sleep(0.4)
                continue
            except httpx.HTTPError as exc:
                raise ProviderUnavailableError(
                    "Wikimedia request failed", provider=self.name, detail={"error": str(exc)}
                ) from exc

            if response.status_code == 404:
                return None
            if response.status_code == 429:
                raise ProviderRateLimitError(
                    "Wikimedia rate limited the request", provider=self.name
                )
            if response.status_code >= 500 and attempt < 1:
                attempt += 1
                await asyncio.sleep(0.4)
                continue
            if response.status_code >= 400:
                raise ProviderUnavailableError(
                    f"Wikimedia returned HTTP {response.status_code}", provider=self.name
                )
            try:
                decoded = response.json()
            except ValueError as exc:
                raise ProviderBadResponseError(
                    "Wikimedia returned invalid JSON", provider=self.name
                ) from exc
            if not isinstance(decoded, dict):
                raise ProviderBadResponseError(
                    "Wikimedia returned an unexpected response", provider=self.name
                )
            return decoded

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self._user_agent, "Accept": "application/json"}

    async def _cache_get(self, key: str) -> object | None:
        return await self._cache.get(key) if self._cache is not None else None

    async def _cache_set(self, key: str, value: object, ttl_s: float) -> None:
        if self._cache is not None:
            await self._cache.set(key, value, ttl_s=ttl_s)


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _localised(values: object, lang: str) -> str | None:
    if not isinstance(values, dict):
        return None
    for code in (lang, "en"):
        entry = values.get(code)
        if isinstance(entry, dict) and entry.get("value"):
            return str(entry["value"])
    return None


def _pick_site(sitelinks: dict[str, str], project: str, lang: str) -> tuple[str, str] | None:
    """``(language, title)`` of a project's article: requested language, English, then any."""
    pattern = re.compile(rf"^([a-z]{{2,3}}){project}$")
    found = {m.group(1): title for key, title in sitelinks.items() if (m := pattern.match(key))}
    for code in (lang, *_FALLBACK_LANGS):
        if code in found:
            return code, found[code]
    if found:
        code = sorted(found)[0]
        return code, found[code]
    return None


def _pick_article(
    sitelinks: dict[str, str], osm_tag: str | None, lang: str
) -> tuple[str | None, str | None]:
    picked = _pick_site(sitelinks, "wiki", lang)
    if picked:
        return picked
    match = WIKIPEDIA_TAG_RE.match(osm_tag or "")
    if match:
        return match.group(1), match.group(2)
    return None, None


def _wiki_url(lang: str, project: str, title: str) -> str:
    return f"https://{lang}.{project}.org/wiki/" + quote(title.replace(" ", "_"), safe=":_(),'")


def _https(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    return (
        url
        if parsed.scheme == "https"
        and parsed.hostname
        and parsed.hostname.endswith(".wikipedia.org")
        else None
    )


def _safe_image(thumbnail: object) -> str | None:
    """The thumbnail URL if it is an https Wikimedia-hosted image (it ends up in an <img>)."""
    if not isinstance(thumbnail, dict):
        return None
    source = thumbnail.get("source")
    if not isinstance(source, str):
        return None
    parsed = urlparse(source)
    if parsed.scheme == "https" and parsed.hostname and parsed.hostname.endswith(".wikimedia.org"):
        return source
    return None


def sitelink_languages(sitelinks: Sequence[str]) -> list[str]:  # pragma: no cover - debugging aid
    return [m.group(1) for key in sitelinks if (m := _WIKI_SITE.match(key))]
