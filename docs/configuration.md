# Configuration

All configuration flows through `Settings` in `config.py`
(`pydantic-settings`): environment variables override defaults, and a `.env`
file in the working directory is loaded automatically
(`model_config = SettingsConfigDict(env_file=".env", extra="ignore")`).

Copy the template and fill in a key:

```bash
cp .env.example .env
```

## Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `ORS_API_KEY` | openrouteservice API key | empty (required for live routing) |
| `ORS_BASE_URL` | openrouteservice base URL | `https://api.openrouteservice.org` |
| `ORS_TIMEOUT_S` | per-request ORS timeout (seconds) | `10.0` |
| `ORS_MAX_RETRIES` | retries for timeouts/5xx (not 429) | `2` |
| `ROUTING_PROVIDER` | `ors`, `brouter`, `valhalla` or `all` -- `all` queries the three engines in parallel and scoring picks the best candidate; otherwise no automatic fallback | `ors` |
| `BROUTER_BASE_URL` | base URL of a local/self-hosted BRouter RouteServer | `http://127.0.0.1:17777` |
| `BROUTER_TIMEOUT_S` | per-request BRouter timeout (seconds) | `30.0` |
| `BROUTER_MAX_RETRIES` | retries for BRouter timeouts/5xx | `1` |
| `GEOCODER_PROVIDER` | `nominatim` or `pelias` (see below and [geocoding.md](geocoding.md)) | `nominatim` |
| `GEOCODER_BASE_URL` | Nominatim base URL (ignored for `pelias`) | `https://nominatim.openstreetmap.org` |
| `GEOCODER_TIMEOUT_S` | geocoder request timeout | `5.0` |
| `GEOCODER_USER_AGENT` | required by Nominatim's usage policy -- include a contact | `bike-routing-agent/0.1` |
| `GEOCODER_CACHE_TTL_S` | geocode cache TTL | `3600` |
| `CACHE_BACKEND` | `memory` (per process) or `redis` (shared; needs the `cache` extra and `CACHE_REDIS_URL`) -- [providers.md](providers.md#shared-cache-redis) | `memory` |
| `CACHE_REDIS_URL` | e.g. `redis://127.0.0.1:6379/0`; use `rediss://` and credentials for anything shared | unset |
| `CACHE_KEY_PREFIX` / `CACHE_REDIS_TIMEOUT_S` | key namespace per environment; connect/operation timeout (seconds) | `bike-routing` / `2.0` |
| `GEOCODER_AMBIGUITY_MARGIN` | top-2 confidence gap below which results are ambiguous | `0.05` |
| `GEOCODER_MIN_CONFIDENCE` | below this, the top result is clarified instead of accepted | `0.3` |
| `VALHALLA_BASE_URL` | base URL of a self-hosted Valhalla (meili) server | `http://localhost:8002` |
| `VALHALLA_TIMEOUT_S` | per-request Valhalla timeout (seconds) | `30.0` |
| `VALHALLA_MAX_RETRIES` | retries for Valhalla timeouts/5xx | `1` |
| `ALTERNATIVE_DEDUP_THRESHOLD_M` | routes within this many metres of each other (discrete Frechet distance) count as one alternative; see [API](api.md#alternatives) | `50` |
| `HEALTH_PROBE_TIMEOUT_S` | hard timeout per `/readyz` probe | `5.0` |
| `HEALTH_CACHE_TTL_S` | how long a probe result is reused (seconds; `0` disables) | `30` |
| `HEALTH_GEOCODER_CACHE_TTL_S` | same for the geocoder, longer because the default is the public Nominatim | `300` |
| `ORS_XMX` / `ORS_XMS` / `ORS_MEM_LIMIT` | self-hosted ORS container sizing (compose only; defaults `4g` / `128m` / `6g`); a city extract needs far less ([self-hosted.md](self-hosted.md#sizing-knobs)) | see left |
| `NOMINATIM_THREADS` / `NOMINATIM_MEM_LIMIT` | self-hosted Nominatim import threads and container limit (compose only; defaults `4` / `4g`) | see left |
| `EXPORT_DIR` | directory for GeoJSON/GPX artifacts | `exports` |
| `DATABASE_URL` | PostgreSQL + PostGIS URL; enables the route history and `/v1/history/*` ([persistence.md](persistence.md)). Needs the `db` extra | unset (stateless) |
| `DATABASE_POOL_MAX_SIZE` | connection pool size (>= 1) | `5` |
| `AUTO_MIGRATE` | apply pending schema migrations on first database use; `false` = only warn, migrate with `bike-router db migrate` ([persistence.md](persistence.md#schema-migrations)) | `true` |
| `ARTIFACT_BACKEND` | `local` (files under `EXPORT_DIR`), `database` (requires `DATABASE_URL`) or `s3` (requires `S3_BUCKET`, the `s3` extra) | `local` |
| `S3_BUCKET` / `S3_PREFIX` | bucket (must exist) and optional key prefix for `ARTIFACT_BACKEND=s3` | unset / empty |
| `S3_ENDPOINT_URL` / `S3_REGION` | endpoint for self-hosted S3-compatibles; optional region | unset |
| `S3_PATH_STYLE` | path-style addressing (most self-hosted S3 servers need it) | `false` |
| `S3_PRESIGNED_URL_TTL_S` | set (1-604800) to redirect artifact downloads to a presigned URL instead of streaming them | unset (stream) |
| `RETENTION_MAX_AGE_DAYS` | how long plans and artifacts live; applied only by `bike-router retention prune` ([persistence.md](persistence.md#retention)) | unset (keep forever) |
| `RETENTION_ORPHAN_GRACE_HOURS` | unreferenced artifacts younger than this are not swept | `24` |
| `RETENTION_BATCH_SIZE` | plans deleted per batch | `500` |
| `LOG_LEVEL` | log level | `INFO` |

## Startup validation

`Settings` refuses combinations that cannot work. Setting
`GEOCODER_PROVIDER=pelias` while `ORS_BASE_URL` is the public
`api.openrouteservice.org` raises at startup -- the public API does not serve
the Pelias endpoints (they 404). Details and rationale:
[geocoding.md](geocoding.md).

## Provider profile mapping

Bike categories are not a routing-engine concept; the mapping from internal
`BikeType` to ORS cycling profile lives in `config.ORS_PROFILE_MAP` so the
rest of the code stays provider-neutral:

| `bike_type` | ORS profile |
| --- | --- |
| `road` | `cycling-road` |
| `gravel` | `cycling-regular` |
| `touring` | `cycling-regular` |
| `mountain` | `cycling-mountain` |
| `city` | `cycling-regular` |
| `ebike` | `cycling-electric` |
| `commuter` | `cycling-regular` |
| `recumbent` | `cycling-regular` |

This mapping is an approximation -- ORS 9.x ships exactly four cycling
profiles (`cycling-regular`, `cycling-mountain`, `cycling-road`,
`cycling-electric`; the former `cycling-recreational` was removed), so several
bike types share one profile, and engine behaviour differs from real-world
bike categories. It is deliberately a config table, not hard-coded in the
adapter, so operators can adjust it. For a `bike_type` -> profile map per
engine, run `bike-router providers list`.

Against a self-hosted BRouter (`ROUTING_PROVIDER=brouter`, see
[providers.md](providers.md)) the same types map to stock or versioned
profiles with genuinely different cost models:

| `bike_type` | BRouter profile |
| --- | --- |
| `road` | `fastbike` (stock) |
| `gravel` | `custom_gravel-v1` (repo: `docker/brouter/profiles/`) |
| `touring` | `custom_touring-v1` (repo: `docker/brouter/profiles/`) |
| `mountain` | `mtb` (stock) |
| `city` | `trekking` (stock) |
| `ebike` | `fastbike` (stock; BRouter does not model e-assist) |
| `commuter` | `fastbike-verylowtraffic` (stock) |
| `recumbent` | `vm-forum-liegerad-schnell` (stock recumbent profile) |

Similarly, the ORS adapter only forwards `avoid_features` that cycling
profiles actually accept (`ferries`, `fords`, `steps`); unsupported requests
(e.g. `highways`, which ORS only honors for driving profiles) are dropped
with a recorded warning on the candidate rather than causing a provider error
-- see [providers.md](providers.md).

## Timeout / retry policy

`OpenRouteServiceClient` applies one policy for everything it calls (routing
and, in `pelias` mode, geocoding too):

- per-request timeout `ORS_TIMEOUT_S`;
- retries on timeouts and 5xx, linear backoff (`0.5s * attempt`), budget
  `ORS_MAX_RETRIES`;
- `429` is **not** retried -- it maps immediately to `ProviderRateLimitError`
  (with `Retry-After` when present).

`BRouterAdapter` applies the same shape of policy with its own budget:
per-request `BROUTER_TIMEOUT_S`, timeouts/5xx retried with `0.5s * attempt`
backoff up to `BROUTER_MAX_RETRIES`, then `ProviderTimeoutError` /
`ProviderUnavailableError` (BRouter's plain-text 400s are routing answers,
never retried -- see [providers.md](providers.md)).

## Running with Docker / compose

Container assets live in `docker/` (`Dockerfile`, `compose.yaml`,
`.dockerignore`). The compose file builds from the repo root with
`dockerfile: docker/Dockerfile`, publishes port 8000, loads the root `.env`
when present, and persists artifacts in the named
`pybikerouter-exports` volume:

```bash
docker compose -f docker/compose.yaml up --build
```

The image is a two-stage Poetry build (dependencies compiled in a builder
stage, runtime image runs as a non-root user) and ships the web UI, so
<http://localhost:8000/> serves the map frontend. `APP_HOST`/`APP_PORT`
environment variables adjust the uvicorn bind address, and the container
declares a `/healthz` (liveness) healthcheck; use `/readyz` for load balancers. `EXPORT_DIR` is set to `/app/exports` in
the image and compose mounts the volume there.

The compose file also defines optional `self-hosted` profiles: a local
openrouteservice and a local Nominatim built from one OSM extract, with a
bootstrap script. You can run both or just one (`--only routing` /
`--only geocoding`; the other keeps using its public service), which is the
way to go on a small machine -- [self-hosted.md](self-hosted.md) lists the
measured memory, disk and time each needs (openrouteservice has no geocoder of
its own, see [geocoding.md](geocoding.md)):

```bash
scripts/self-hosted-bootstrap.sh                   # both
scripts/self-hosted-bootstrap.sh --only routing    # local routing, public geocoding
```

And an optional `brouter` profile running a local BRouter RouteServer
(segments, versioned custom profiles, verification steps:
[`docker/brouter/README.md`](../docker/brouter/README.md)):

```bash
docker compose -f docker/compose.yaml --profile brouter up
```
