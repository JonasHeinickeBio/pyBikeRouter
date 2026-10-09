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
| read | **Wikipedia REST** summary, Wikidata labels, sitelinks | text, thumbnail, links to Wikipedia, Wikivoyage, Commons | on demand, when a marker is opened; cached 24 h |

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

When the server has POIs the form shows a *Points of interest* panel: a master
switch, one checkbox per kind (sights on and services off by default; remembered
in the browser), and *Route past the most famous sights* with a count.

- With a route on screen the active candidate's surroundings are searched; without
  one, the visible map (from zoom 11) is.
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

## Reliability

The public Overpass service answers `504` under load -- during development of this
feature it failed a large share of requests for hours -- so a failed search is retried
once, then the next configured instance is tried, and a search that still fails
is a `502` from the POI endpoints -- never a failed plan: `poi_stops` falls back
to `unavailable` and the route is planned without. Identical searches are served
from the cache.
