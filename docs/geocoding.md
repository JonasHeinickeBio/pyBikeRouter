# Geocoding

The `geocode_locations` graph node resolves any free-text place (origin,
destination, via points) to a coordinate through a pluggable
`GeocodeProvider`. The node itself is provider-neutral: it only knows the
`geocode(query, limit) -> list[GeocodeCandidate]` protocol from
`providers/base.py`, plus the ambiguity/clarification policy applied to the
returned candidates.

Two backends are implemented:

| Backend | Default | Transport | Requires |
| --- | --- | --- | --- |
| `nominatim` (`providers/geocoder.py`) | yes | Direct HTTP to OSM Nominatim (`/search`) | Valid `User-Agent` (usage policy), 1 req/s max |
| `pelias` (`providers/pelias.py`) | no | `OpenRouteServiceClient` → self-hosted ORS `GET /pelias/v1/search` | A **self-hosted** openrouteservice instance |

## Choosing a backend

Set `GEOCODER_PROVIDER` in `.env`:

```bash
GEOCODER_PROVIDER=nominatim   # default
# or
GEOCODER_PROVIDER=pelias
ORS_BASE_URL=https://ors.your-instance.example.org
```

### Why Pelias needs a self-hosted ORS

The public `api.openrouteservice.org` does **not** serve the Pelias
geocoding endpoints — `GET /pelias/v1/*` returns 404 there, the same as
`GET /v2/health` (both are part of the self-hosted ORS stack). Therefore
`Settings` validates the combination at startup and refuses to boot when
`GEOCODER_PROVIDER=pelias` points at the public base URL:

```
geocoder_provider='pelias' requires a self-hosted openrouteservice backend:
the public api.openrouteservice.org does not expose the Pelias geocoding
endpoints (they return 404). Use geocoder_provider='nominatim' (the default)
for the public API.
```

This mirrors the health-probe behaviour: `health()` reports `unknown`
rather than failing on the public API.

### When to prefer each

**Nominatim (default)** is the right choice when routing through the public
ORS: it is a free, public endpoint with no per-request credential beyond a
usage-policy-compliant `User-Agent`.

**Pelias** pays off when you already run (or plan to run) a self-hosted ORS
instance:

- One vendor, one credential, one base URL for routing *and* geocoding.
  `api.build_providers` wires the geocoder and the routing adapter to share
  a single `OpenRouteServiceClient`, so they reuse one connection pool and
  one retry/timeout policy.
- No Nominatim usage-policy constraints (1 req/s, mandatory
  `User-Agent`, cache-your-results expectation).
- Real per-result match confidence (see below).
- Boundary-aware disambiguation: `boundary.countries`,
  `boundary.geometries`, `boundary.rect` parameters (already part of the
  client) let an operator pin geocoding to their service region.

## How each backend normalizes results

Both adapters return `GeocodeCandidate(label, coordinate, confidence,
source)` sorted by descending confidence, and both raise
`GeocodingNotFoundError` for empty results and `ProviderBadResponseError`
for malformed responses — the graph node treats those identically.

- **Nominatim** normalizes the query first (`str.`/`Strasse` abbreviations,
  `in` → comma separator), sends `format=jsonv2` with `addressdetails=1` and
  maps `display_name` → label and `importance` (0–1, a *notoriety* ranking) →
  confidence, with two match-precision floors (see below). If the free-text
  search returns nothing and the query parses into structured parts
  (`street=…&postalcode=…&city=…`), a second structured request is made
  before declaring failure.
- **Pelias** maps `properties.label` (falling back to `properties.name`) →
  label, `properties.confidence` (0–1, a *match quality* score) →
  confidence, and `geometry.coordinates` (`[lon, lat]`) → coordinate.
  Confidence is clamped into `[0, 1]` defensively.

### Match-precision floors (Nominatim)

`importance` measures notoriety, not match precision, so exact address hits
can score near 0. The adapter therefore raises confidence in two cases:

- the query contains a house number, the result's `address.house_number`
  matches it, and the street tokens appear in the label →
  confidence is at least `HOUSE_NUMBER_CONFIDENCE` (0.9);
- the query has no house number and the result is a street
  (`category == "highway"`) whose tokens appear in the label → at least
  `STREET_MATCH_CONFIDENCE` (0.6).

This lets a unique exact address ("Kasernenstraße 23, 38106 Braunschweig")
auto-resolve instead of being clarified for having importance ≈ 0, while
homonym-prone place names keep their importance-based behaviour.

### Confidence semantics — read this before tuning

The geocode node's clarification policy is tuned on confidence values:

- top result below `GEOCODER_MIN_CONFIDENCE` (default `0.3`) → clarify, and
- top-2 gap below `GEOCODER_AMBIGUITY_MARGIN` (default `0.05`) → clarify.

The two backends populate `confidence` differently:

- Nominatim `importance` is how *notorious* a place is. Major cities get
  near-1.0 scores, small villages sit near 0 — so the defaults are tuned to
  auto-accept famous places and clarify obscure ones.
- Pelias `confidence` is how well the result *matches the query*. A single
  unambiguous small village can score 1.0, while a place-name search that
  hits several homonyms may return a cluster of near-equal confidences.

**Consequence:** the default `0.05`/`0.3` constants are validated against
Nominatim's distribution, *not* Pelias's. When switching to Pelias, expect
to re-tune `GEOCODER_AMBIGUITY_MARGIN` / `GEOCODER_MIN_CONFIDENCE` against
your data (Pelias clusters of equal confidence will over-trigger
clarification at margin `0.05`). Both are already settings, so this is a
config change, not a code change.

## Caching

Both adapters cache geocoded results in an in-process TTL cache
(`InMemoryTTLCache` from `providers/base.py`), keyed by
`(provider, query, limit)` with a SHA-256 digest (the Nominatim adapter
keys on the *normalized* query, so `Kasernenstr 23 ...` and
`Kasernenstraße 23, ...` share one cache entry), TTL from
`GEOCODER_CACHE_TTL_S` (default 3600 s). A shared `CacheBackend` (e.g.
Redis) can be injected for multi-instance deployments. For Pelias the
cache lives in the same process as the ORS client — no extra HTTP layer.

## Error handling

- **Nominatim** maps `httpx` exceptions itself (timeout →
  `ProviderBadResponseError`, HTTP errors → `ProviderBadResponseError` with
  status code) and invalid JSON → `ProviderBadResponseError`.
- **Pelias** inherits the full client transport mapping: 429 →
  `ProviderRateLimitError` (with `Retry-After`), 5xx/timeout retries
  (backoff, `ORS_MAX_RETRIES`) → `ProviderUnavailableError`, ORS/bad JSON →
  `ProviderBadResponseError`. Either way the `geocode_locations` node turns
  any `ProviderError` into a structured `provider_failure` response.

## Boundary parameters (Pelias)

`PeliasGeocoder` accepts `boundary_countries` (ISO 3166-1 alpha-2,
comma-separated), `boundary_geometries` (geometry codes) and
`boundary_rect` (`lon1,lat1,lon2,lat2`) at construction; they are sent with
every search. They are constructor-level because the `GeocodeProvider`
protocol only carries `query` and `limit` — per-request boundaries would
require extending the protocol (deliberately not done in this milestone).

## Tests

- `tests/providers/test_pelias_geocoder.py` — normalization (sorting,
  label fallback, confidence clamping), request shape (params, auth
  header, boundary defaults), errors (empty → not-found, malformed →
  bad-response, 429 propagation), cache hit on second call. All HTTP mocked
  with respx.
- `tests/test_config.py` — `Settings` validation (pelias rejected for the
  public URL, accepted for self-hosted, default is nominatim) and
  `build_providers` wiring (default returns Nominatim; pelias shares one
  `OpenRouteServiceClient` between geocoder and routing adapter).
