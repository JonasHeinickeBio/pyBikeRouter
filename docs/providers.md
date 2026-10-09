# Providers

All backends sit behind the `Protocol`s in `providers/base.py`. Graph nodes
and scoring depend only on these interfaces, so a new backend is a new
adapter class plus wiring in `api.build_providers` / `api.build_routing_provider`
-- never a change to orchestration.

```python
class RoutingProvider(Protocol):
    name: str
    async def route(self, request: RoutingRequest) -> RouteCandidate: ...
    async def health(self) -> dict: ...

class GeocodeProvider(Protocol):
    name: str
    async def geocode(self, query: str, *, limit: int = 5) -> list[GeocodeCandidate]: ...

class CacheBackend(Protocol):
    async def get(self, key: str) -> object | None: ...
    async def set(self, key: str, value: object, *, ttl_s: float) -> None: ...
```

`InMemoryTTLCache` is the default `CacheBackend`: process-local, keyed by
`(provider, query, limit)` via a SHA-256 digest, TTL from
`GEOCODER_CACHE_TTL_S`. It is adequate for a single API instance; multi-
instance deployments use `CACHE_BACKEND=redis`
(`providers/redis_cache.py`, [below](#shared-cache-redis)) behind the same
protocol. `NamespacedCache` gives each user of one backend its own key prefix
(`geocode:`, `overpass:`).

### Shared cache (Redis)

`CACHE_BACKEND=redis` + `CACHE_REDIS_URL=redis://host:6379/0` (the `cache`
extra: `poetry install --extras cache`) makes every API instance share its
geocode and Overpass results, so a place resolved by one instance is not
looked up again by the others -- which matters for Nominatim's 1 request/second
policy and Overpass's rate limits. Behaviour that matters:

- **It is an optimisation, never a dependency.** Every Redis failure -- refused
  connection, timeout, protocol error -- degrades to a cache miss: `get`
  returns `None`, `set` does nothing, nothing raises, and a plan never fails
  because of the cache. After a failure the backend skips Redis for 30 s (a
  circuit breaker) so a dead server does not add its timeout to every request;
  one warning is logged per outage, and `/readyz` shows the `cache` component as
  `unavailable` (the instance is `degraded`, not unready).
- **JSON only.** Values are stored as JSON, never pickle: a shared cache must
  not be able to execute code on the reader. A value that is not JSON-
  serialisable is simply not cached; a corrupt entry reads as a miss.
- **Keys** are `<CACHE_KEY_PREFIX>:v<N>:<component>:<key>`. Bumping the
  schema version `N` in code (when a cached value's shape changes)
  invalidates old entries without a manual flush; use a different
  `CACHE_KEY_PREFIX` per environment sharing one Redis.
- **Not cached:** routing responses (inputs vary per request and results depend
  on the engine's data version), geocoding errors/not-found results, and
  anything that failed.
- **Security.** The compose `cache` profile binds loopback with no password and
  no persistence (capped memory, LRU eviction): fine for one host. A shared
  production Redis needs authentication/TLS in `CACHE_REDIS_URL`
  (`rediss://user:pass@host`) and network isolation -- it holds user-supplied
  place names.
- Run one locally: `docker compose -f docker/compose.yaml --profile cache up -d redis`.

## openrouteservice

Two layers:

### `OpenRouteServiceClient` (`providers/ors_client.py`)

A full async client for the ORS public API (Core 9.x) -- every documented
endpoint, not just the one the graph needs:

| Capability | Endpoints |
| --- | --- |
| Directions | `GET/POST /v2/directions/{profile}` (+ `/json`, `/gpx`, `/geojson`) |
| Network export | `POST /v2/export/{profile}` (+ `/topojson`, `/json`) |
| Isochrones | `POST /v2/isochrones/{profile}` |
| Distance matrix | `POST /v2/matrix/{profile}` |
| Snap to network | `POST /v2/snap/{profile}` (+ `/json`, `/geojson`) |
| POIs | `POST /openpoiservice/v0/pois` |
| Vehicle routing | `POST /vroom/v0` |
| Elevation | `POST /openelevationservice/v0/line`, `GET/POST .../v0/point` |
| Geocoding (Pelias) | `GET /pelias/v1/search`, `/autocomplete`, `/search/structured`, `/reverse` |
| Health | `GET /v2/health` |

Client conventions and behaviours:

- Coordinates are `[lon, lat]`, times in seconds, distances in meters (ORS
  conventions).
- Auth via `Authorization` header; one timeout/retry policy for all calls
  (see [configuration.md](configuration.md)).
- Errors are translated into the structured `errors.py` hierarchy: 429 ->
  `ProviderRateLimitError` (not retried, carries `Retry-After`), exhausted
  timeouts -> `ProviderTimeoutError`, exhausted 5xx ->
  `ProviderUnavailableError`, malformed payloads ->
  `ProviderBadResponseError`.
- ORS error codes 2009/2010 ("no track found") map to `ProviderNoRouteError`,
  keeping "no path exists" distinct from infrastructure failure.
- The health probe passes `raise_on_5xx=False` so a degraded service is
  reported as `degraded` instead of being retried away.

### `OpenRouteServiceAdapter` (`providers/ors.py`)

The `RoutingProvider` the graph actually uses, built on the client (only the
directions call). Responsibilities:

1. **Profile mapping.** `bike_type` -> ORS profile via
   `config.ORS_PROFILE_MAP`; an unmapped type is a structured
   `ProviderBadResponseError`, never a crash.
2. **Request building.** `[origin, *via, destination]` as `[lon, lat]`,
   `elevation: true`, `format: geojson`.
3. **Honest capability handling.** `avoid_high_traffic_roads` maps to ORS's
   `highways` avoid-feature, but ORS cycling profiles reject `highways` as a
   hard 400 -- the adapter filters requested features down to what cycling
   profiles accept (`ferries`, `fords`, `steps`) and records a warning on the
   candidate stating the unsupported features were *not applied*. A silently
   ignored constraint would be worse than a declared one.
4. **Normalization.** ORS GeoJSON -> `RouteCandidate`: geometry passthrough,
   `summary.distance/duration`, `ascent/descent` (kept `None` when absent --
   not coerced to 0), ORS `warnings` and `extras` summaries folded into
   `candidate.warnings`, `provenance` recorded, and the full raw payload kept
   on `raw_provider_response` for debugging (stripped before API responses).
5. **Failure classification.** Empty `features` -> `ProviderNoRouteError`;
   malformed payloads -> `ProviderBadResponseError` with the payload keys in
   `detail`.

## Geocoding

Nominatim (default) and Pelias (self-hosted ORS only), with a detailed
comparison, confidence semantics, and tuning guidance in
[geocoding.md](geocoding.md). Key summary: both adapters return
confidence-sorted `GeocodeCandidate`s and raise
`GeocodingNotFoundError`/`ProviderBadResponseError`; the `geocode_locations`
node -- not the adapters -- owns the ambiguity/clarification policy.

## BRouter (`providers/brouter.py`)

Full `RoutingProvider` for a self-hosted BRouter RouteServer
(`docker compose -f docker/compose.yaml --profile brouter up`, or any stock
`abrensch/brouter`
deployment). Bike types map to stock or versioned custom profiles via
`config.BROUTER_PROFILE_MAP` (see the table in
[configuration.md](configuration.md)); custom `.brf` profiles live in
`docker/brouter/profiles` and are mounted into the container with a
`custom_` prefix. Quirks the adapter absorbs: plain-text (never JSON) error
bodies on 400/500, string-typed GeoJSON summary values, no descent figure
(`descent_m` stays `None`), and no health endpoint -- `/robots.txt` is probed
instead. Unsupported-constraint honesty works the same way as for ORS: the
constraint is recorded as a candidate warning, not silently dropped.

A real-world A/B against ORS across all bike types -- where the engines
disagree and why -- is in
[providers-comparison.md](providers-comparison.md).

## Valhalla (`providers/valhalla.py`)

Full `RoutingProvider` for a self-hosted Valhalla HTTP meili server
(`POST /route`, health probe on `GET /status` -- any stock deployment; the
compose `valhalla` profile uses the pinned `ghcr.io/valhalla/valhalla-scripted`
image with a pre-built `./valhalla/valhalla_tiles.tar`). All eight bike types map to
Valhalla's single `bicycle` costing via `config.VALHALLA_PROFILE_MAP`:
current Valhalla has no per-bike-type or e-assist costing, so bike-type
differentiation comes from the other engines and the scoring step; the map
exists so a future costing can be wired in without touching the adapter.
Quirks the adapter absorbs: geometry is a plain-ASCII polyline6 string in
each leg's `shape` field (not GeoJSON coordinates), decoded by the
in-repo `decode_polyline6`; `trip.summary` distances are in kilometers and
converted to meters; elevation only exists when `elevation_interval` is
requested and the tiles carry it (ascent/descent stay `None` otherwise);
no-path answers are 4xx responses whose `status_message` carries markers
like "No path could be found for input" and map to
`ProviderNoRouteError` (other 4xx -> `ProviderBadResponseError`, 429 ->
`ProviderRateLimitError`, timeouts/5xx retried then raised), mirroring the
ORS adapter's classification. Unsupported constraints are recorded as
candidate warnings, not silently dropped.

`routing_provider="all"` polls ORS, BRouter, and Valhalla in parallel and
lets the scorer pick the best candidate across engines.

## Adding a new backend

1. Create `providers/<name>.py` implementing the protocol; translate all
   transport failures into `errors.py` exceptions and use
   `ProviderNoRouteError` only for genuine no-path answers.
2. Map engine-specific names (profiles, avoid-features) in `config.py`, and
   record unsupported-constraint situations as candidate warnings rather than
   dropping them silently.
3. Wire construction in `api.build_providers` (or pass the instance to
   `build_graph` directly).
4. Add adapter tests with `respx`-mocked HTTP mirroring
   `tests/providers/test_ors_adapter.py`, including the malformed-payload and
   no-route paths.


## BRouter per-request settings

BRouter has no request fields for constraints, but it accepts a **profile variable per
request** as `profile:<name>=<number>` (numbers only: `1` is true, `0` is false; `true`
makes BRouter answer 500). The adapter uses this for `avoid_high_traffic_roads`:

| Profile | Variable set |
| --- | --- |
| `custom_gravel-v1`, `custom_gravel-v2` | `consider_traffic_estimate` |
| `custom_touring-v1`, `custom_commuter-v1`, `trekking`, `fastbike`, `fastbike-verylowtraffic` | `consider_traffic` |
| `mtb`, `vm-forum-liegerad-schnell` | none: no traffic setting, declared in `warnings` |

`avoid_high_traffic_roads=true` (the default) sends `1`, `false` sends `0`; the value
actually sent is recorded in the candidate's `provenance.profile_overrides` (same
profile with a different switch is a different route, so the record matters). The
effect is real but moderate, measured over 14 routes (mean share of the route on main
roads without a bike lane, on vs off): touring 13 vs 22 %, trekking/city 14 vs 21 %,
commuter 14 vs 24 %, fastbike (road, e-bike) 56 vs 76 %, gravel 0.7 vs 2.3 %, at most
+3 % time ([profile-evaluation.md](profile-evaluation.md)). Before this, touring and city
were routed with the traffic estimate off whatever the request said; they now follow it
(on by default), so their default routes are calmer. Ferries are not covered yet: the
same mechanism would work for the profiles that have `allow_ferries`.
