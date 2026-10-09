# Points of interest

Places worth a look near a route: viewpoints, castles, waterfalls, museums --
and, closer to the road, drinking water, cafés and bike shops. Found in
OpenStreetMap, ranked and described with Wikidata and Wikipedia, shown on the
map with a filter, addable to a route, and usable to *plan* a route past the
best-known sights (issue #55).

POIs are **informational**: they never change a route's score or ranking, and
they say nothing about safety or opening times beyond what a mapper wrote down.

## Where the data comes from

| Step | Service | What it gives | Notes |
| --- | --- | --- | --- |
| find | OpenStreetMap through **Overpass** | the places, their tags, `wikidata=*` / `wikipedia=*` links | one request for all wanted kinds, as bounding boxes along the route (see below); results cached 24 h |
| rank | **Wikidata** `wbgetentities` | how many language editions describe the item (its *sitelinks*) = `fame` | 50 items per request, keyless, cached 7 d |
| link | **Wikipedia** page properties | the Wikidata item for a bare `wikipedia=de:Title` tag | only for sights that lack `wikidata` |
| read | **Wikipedia Action API** (article intro, thumbnail, description), Wikidata labels, sitelinks | text, thumbnail, links to Wikipedia, Wikivoyage, Commons | on demand, when a marker is opened; cached 24 h; the title is a query parameter, never part of a URL path |

All of it is open data (ODbL, CC0, CC BY-SA); the UI and `PoiInfo.attribution`
carry the attribution. Wikimedia requires an identifying `User-Agent`
(`POI_USER_AGENT`). Overpass and Wikimedia only ever receive the route shape /
map view and ids -- no user data.

Considered and left out for now: OpenTripMap (needs an API key), Wikipedia
geosearch (returns every article with coordinates, villages included -- too
noisy as a POI source), pageview counts (one extra request per POI; sitelinks
are stable and good enough to order by).

## Categories

`GET /v1/pois/categories` lists them. Two kinds:

- **sight** -- worth a detour: `viewpoint`, `attraction`, `historic` (castles,
  ruins, forts, monuments, archaeological sites; memorials only when linked into
  Wikidata or Wikipedia), `museum`, `nature` (waterfalls, caves, hot springs; peaks only when
  linked), `religious` (churches linked into Wikidata or Wikipedia), `swimming`. Ranked by fame,
  usable as route stops.
- **service** -- needed on the way: `food` (cafés, beer gardens, ice cream),
  `water`, `rest` (shelters, picnic sites), `bike_service`. Only searched within
  500 m of the route, never ranked by fame, never route stops.

## How Overpass is asked

Along a route the search is a set of **bounding boxes** that follow the route in ~10 km
pieces (at most 40), not an `around` filter: on the public instance a union of
`around` filters over a polyline for every category times out (`504`), while box
lookups use the spatial index. The boxes over-cover a diagonal stretch, so every
result's real distance from the route is measured afterwards and anything beyond the
buffer is dropped. Sights and services are output separately (1500 / 1000 elements
at most) so a city full of cafés cannot crowd the sights out. When only well-known
places matter (route stops) the search takes everything tagged `wikidata` or
`wikipedia` in the boxes once and narrows it to the categories in memory, which is much
cheaper than one lookup per category. Overpass reports a query it gave up on as HTTP 200
with a `remark` and *empty or partial* elements; that is treated as a failure (and not
cached), never as "nothing found".

## Fame, and what "unknown" means

`fame` is the number of Wikidata sitelinks: 93 for Neuschwanstein Castle, 10 for
a regional abbey, `null` for something without a Wikidata item. A `null` is
**not** "obscure" -- it means nothing was measured. Ranking puts the measured
ones first (highest fame), then the rest by distance from the route, and the
famous-stops feature only ever picks from the measured ones. If Wikidata cannot
be reached, POIs are still returned, unranked, with `fame_status: "unavailable"`.

## API

See [api.md](api.md#points-of-interest) for the shapes.

- `POST /v1/pois/along-route` -- a route shape in, POIs out, each with
  `distance_from_route_m` and `along_route_km`.
- `GET /v1/pois/in-bbox` -- POIs in a map view (at most 0.5° wide), for browsing
  without a route.
- `GET /v1/pois/info` -- the text, thumbnail and links for one POI.
- `POST /v1/route/plan` with `poi_stops` -- route past the most famous sights.

## Routing past the most famous sights

```json
{"origin": "Füssen", "destination": "Oberammergau", "poi_stops": {"count": 2}}
```

The `select_poi_stops` node (after geocoding, before routing) searches a corridor
(`corridor_km`, default 5 km) around the straight line origin -> your own `via`
points -> destination, keeps the sights with a known fame of at least `min_fame` (default 5 languages),
takes the `count` best-known ones (at least 1 km apart and from your own places, not within 3 % of
either end of the line) and inserts them among the via points in travel order.
The response lists them in `poi_stops` with `poi_stops_status`:

| `poi_stops_status` | Meaning |
| --- | --- |
| `ok` | the stops were added |
| `none_found` | nothing well-known in the corridor; planned without |
| `unavailable` | POIs are off or Overpass/Wikidata failed; planned without (no "most famous" is ever guessed) |
| `unsupported` | loops (`return_to_origin`) have no origin-destination corridor |
| `dropped` | the engines could not route through the stops (a POI is a point on the map, not on a road); planned again without them |

Limits: not for loops; stops are chosen near the *straight line*, so on a very
winding trip a stop can add a long detour -- the route's distance and the
`max_distance_km` warnings show it.

## The map

When the server has POIs, the **settings panel** (the gear at the top right; it is hidden until
opened and slides out from the right, Esc or × closes it) has a master switch and one checkbox
per kind (sights on and services off by default; remembered in the browser). The form has
*Sights on the way*: *Route past the most famous sights* with a count, using the kinds ticked
in the settings.

- With a route on screen the active candidate's surroundings are searched; without
  one, the visible map is -- from zoom 11 and only while the view is under 0.5 degrees wide
  and tall (a wide window can exceed that even at zoom 11; the panel then asks for a zoom).
- A marker's popup shows the kind, the fame as a fact ("Described in 93
  languages"), the distance from the route and the opening hours as mapped.
  *Read more* loads the Wikipedia summary, a thumbnail and links to Wikipedia,
  Wikivoyage, Commons, Wikidata, OpenStreetMap and the website.
- *Add to route* fills an empty origin, then an empty destination, otherwise adds
  a via point **between the via points it lies between along the route**, and
  plans again when a route is shown.
- Sights the plan was routed past get a ringed marker and a note above the result.

## Configuration

| Variable | Meaning | Default |
| --- | --- | --- |
| `POI_ENABLED` | `false` switches POIs off (no request leaves the service; endpoints answer `503`) | `true` |
| `POI_OVERPASS_URLS` | comma-separated Overpass instances, tried in order -- the public one is often overloaded | `https://overpass-api.de/api/interpreter` |
| `POI_WIKIDATA_URL` / `POI_WIKIPEDIA_URL` | Wikimedia endpoints (`{lang}` is the article language) | public URLs |
| `POI_USER_AGENT` | identifies your deployment to Wikimedia and Overpass | `bike-routing-agent/0.1 (+https://github.com/...)` |
| `POI_TIMEOUT_S` / `POI_MAX_RETRIES` | per-request timeout; extra rounds over the instances after a failure | `25` / `1` |
| `POI_CACHE_TTL_S` | how long a search is reused (Redis if `CACHE_BACKEND=redis`) | `86400` |
| `POI_DEFAULT_BUFFER_M` / `POI_MAX_BUFFER_M` | how far from the route a sight may be, and the cap | `1500` / `5000` |
| `POI_PER_CATEGORY_LIMIT` | most POIs returned per kind (best-known first) | `40` |

## Which hosts are ever contacted

Overpass (`POI_OVERPASS_URLS`), Wikidata (`POI_WIKIDATA_URL`) and
`{lang}.wikipedia.org` (`POI_WIKIPEDIA_URL`). The language is the only client-influenced
part of any URL: it must be one of the Wikipedia editions listed in
`poi/wikipedia_languages.py` (a well-formed code that is not one, as in
`/v1/pois/info?lang=xx`, is read in English), and a `wikipedia=xx:Title` tag or sitelink for
an unknown edition is ignored. Titles are percent-encoded into the path. Cache keys never
contain client text (hashed), and keys are stripped of line breaks before they are logged.

## Reliability

The public Overpass service answers `504` under load -- during development of this
feature it failed a large share of requests for hours -- so a failed search is retried
once, then the next configured instance is tried, and a search that still fails
is a `502` from the POI endpoints -- never a failed plan: `poi_stops` falls back
to `unavailable` and the route is planned without. Identical searches are served
from the cache.

## Evidence: what was tested, and how

Written down so the next person knows what has been seen working and what has not
(all of it from the development of issue #55, 2026-10-09).

### Automated

The default suite (`tests/poi/`, `tests/nodes/test_poi_stops.py`,
`tests/graph/test_graph_poi_stops.py`, `tests/test_api_pois.py`,
`tests/test_frontend_pois.py`) never touches the network (`tests/conftest.py` sets
`POI_ENABLED=false`). Its fixtures are **real responses**, not invented ones:
`overpass_poi_corridor.json` (Braunschweig -> Goslar, ways and relations with
`center`), `wikidata_sitelinks_raw.json`, `wikidata_entity_raw.json`,
`wikipedia_extract_neuschwanstein.json`, `wikipedia_pageprops_raw.json` (including a
page that does not exist). The front-end helpers in `frontend/pois.js` run under node.

### Checked live

| What | Result |
| --- | --- |
| Wikidata fame, titles -> items, summaries, thumbnails, links | worked; e.g. Rammelsberg 36 languages, Herzog Anton Ulrich-Museum 23, Braunschweiger Dom 20, Imperial Palace of Goslar 16 |
| Overpass, all kinds, 1.5 km buffer, 40 km route | 289 POIs in 21 s, ranked by real fame |
| Overpass, linked-only corridor of 6 km | 304 elements, but only after 158 s (the instance was overloaded) |
| Plan Braunschweig -> Goslar with `poi_stops` against real BRouter | `ok`, 3 stops, 53.5 km, 10 s (corridor data replayed from a real capture) |
| Browser: popup, *Read more*, *Add to route* (re-plans), kind filter, remembered filter after reload, browsing without a route, 375 px phone width | all worked; no horizontal overflow |

### Findings from the live runs

1. **The public Overpass instance was unreliable for hours**: `504` after 10-50 s,
   200-OK answers after 20-160 s, and two alternative public instances answering `500`.
   Single small queries sometimes took 1 s and sometimes 60 s. This is why searches are
   cached, retried, spread over `POI_OVERPASS_URLS`, and why a failed search never fails
   a plan.
2. **`around` over a polyline is too expensive** when unioned for every category; bounding
   boxes along the route are not (see [How Overpass is asked](#how-overpass-is-asked)).
3. **Overpass reports a timed-out query as HTTP 200** with
   `remark: "runtime error: Query timed out ..."` and empty or partial `elements`. Read
   naively that is "no POIs here" and would be cached for 24 h; it is now an error.
4. **Wikipedia geosearch is not a POI source**: around Wolfenbüttel it returned schools,
   villages, a stream and a mast among its first 60 hits.
5. **Fame is modest for regional sights** (castles and museums 5-25 languages; only world
   landmarks such as Neuschwanstein reach about 90), so "famous" is relative to the area,
   and `min_fame` exists to keep local chapels from being presented as famous.
6. **The best-known places sit at the ends of a trip** (city centres) and are skipped as
   stops on purpose, so on a trip between two towns the mid-route picks can be modest
   (8, 5 and 4 languages on the test route).

### Not verified / known limits

- The complete `poi_stops` plan against the *real* public Overpass worked once; a second
  run hit `504`s and correctly fell back to planning without stops. The browser walk-through
  ran against a local stand-in answering with Overpass-shaped data (real Wikidata, Wikipedia
  and BRouter); the real corridor response was replayed through it for the plan above.
- The `dropped` fallback (the engines cannot route through a stop) is unit-tested. One real
  run reported it; the cause could not be reproduced afterwards, so it is not understood.
- Stops are chosen near the **straight line** origin -> destination; on a winding trip a
  stop can add a long detour. Loops are rejected.
- Opening hours are shown as mapped and not compared with the arrival time.
- No self-hosted Overpass, no pageview-based fame, no scoring effect -- see the roadmap.
- The Overpass quirks above are those of one day's public instance; if they no longer
  occur, `POI_OVERPASS_URLS` with a single URL is fine.
