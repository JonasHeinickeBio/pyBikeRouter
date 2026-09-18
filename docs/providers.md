# Providers

All backends sit behind the `Protocol`s in `providers/base.py`. Graph nodes
and scoring depend only on these interfaces, so a new backend is a new
adapter class plus wiring in `api.build_providers` -- never a change to
orchestration.

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
instance deployments should inject a shared backend (e.g. Redis) behind the
same protocol -- the seam exists, the Redis implementation does not yet
([roadmap.md](roadmap.md)).

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

## Stubs: Valhalla and BRouter

`providers/valhalla.py` and `providers/brouter.py` implement
`RoutingProvider` but raise `ProviderUnavailableError` on `route()`. They
exist so the protocol has second implementers to test against and so provider
selection can be exercised without live services. Wiring one up for real is
tracked in [roadmap.md](roadmap.md).

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
