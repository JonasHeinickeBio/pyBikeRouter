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
| `GEOCODER_PROVIDER` | `nominatim` or `pelias` (see below and [geocoding.md](geocoding.md)) | `nominatim` |
| `GEOCODER_BASE_URL` | Nominatim base URL (ignored for `pelias`) | `https://nominatim.openstreetmap.org` |
| `GEOCODER_TIMEOUT_S` | geocoder request timeout | `5.0` |
| `GEOCODER_USER_AGENT` | required by Nominatim's usage policy -- include a contact | `bike-routing-agent/0.1` |
| `GEOCODER_CACHE_TTL_S` | in-memory geocode cache TTL | `3600` |
| `GEOCODER_AMBIGUITY_MARGIN` | top-2 confidence gap below which results are ambiguous | `0.05` |
| `GEOCODER_MIN_CONFIDENCE` | below this, the top result is clarified instead of accepted | `0.3` |
| `VALHALLA_BASE_URL` | base URL for the (stubbed) Valhalla adapter | `http://localhost:8002` |
| `EXPORT_DIR` | directory for GeoJSON/GPX artifacts | `exports` |
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

This mapping is an approximation -- ORS has no dedicated gravel profile, and
engine behaviour differs from real-world bike categories. It is deliberately
a config table, not hard-coded in the adapter, so operators can adjust it.

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
declares a `/healthz` healthcheck. `EXPORT_DIR` is set to `/app/exports` in
the image and compose mounts the volume there.

The compose file also defines an optional `self-hosted` profile with a
self-hosted OpenRouteService container (geocoding/Pelias only works against
such an instance, see [geocoding.md](geocoding.md)):

```bash
docker compose -f docker/compose.yaml --profile self-hosted up
```
