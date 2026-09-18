# bike-routing-agent

[![CI](https://github.com/JonasHeinickeBio/pyBikeRouter/actions/workflows/ci.yml/badge.svg)](https://github.com/JonasHeinickeBio/pyBikeRouter/actions/workflows/ci.yml)

An OSM-based bike-routing service: a typed, provider-neutral domain model,
a full openrouteservice API client plus routing adapter, and a LangGraph
workflow that turns a validated route request into an explainable,
exportable cycling route.

The LLM (where used) may parse free text, ask clarification questions, and
explain a chosen route. It never invents coordinates, geometry, elevation,
or route suitability -- all of that comes from OSM-derived routing engines.

## Documentation

The full documentation suite lives in [`docs/`](docs/README.md):

| Document | Contents |
| --- | --- |
| [Architecture](docs/architecture.md) | Graph nodes, state, branching, terminal statuses, error model |
| [API](docs/api.md) | Endpoint contracts, response statuses, artifact serving |
| [Configuration](docs/configuration.md) | Every environment variable, validation rules, deployment notes |
| [Providers](docs/providers.md) | Protocols, ORS client/adapter behaviour, geocoders, stubs, adding a backend |
| [Backend comparison](docs/providers-comparison.md) | Measured ORS vs BRouter behaviour per bike type, combining both engines |
| [Geocoding](docs/geocoding.md) | Nominatim vs Pelias, confidence/ambiguity semantics, tuning |
| [Scoring & exports](docs/scoring-and-exports.md) | Score math, uncertainty policy, explanation rules, GeoJSON/GPX |
| [Testing](docs/testing.md) | Test layout, mocking conventions, live tests, CI |
| [Roadmap](docs/roadmap.md) | Planned work and explicit non-goals |

## Architecture

```
FastAPI
  |
  v
LangGraph RouteAgentState
  |
  +--> parse_request        (structured input first; LLM extraction is an injectable dependency)
  +--> validate_request      (Pydantic)
  +--> geocode_locations     (Nominatim by default; Pelias on self-hosted ORS; ambiguity -> clarification, never a guess)
  +--> route_with_provider   (openrouteservice; engine-neutral RoutingRequest in, RouteCandidate out)
  +--> score_candidates      (deterministic distance/elevation/warning scoring)
  +--> explain_and_export    (facts-only explanation + GeoJSON/GPX export)
  |
  v
JSON response + GeoJSON/GPX artifacts
```

Graph branches:

```
START -> parse_request
      -> validate_request -> (invalid | awaiting_clarification) -> END
      -> geocode_locations -> (awaiting_clarification | provider_failure) -> END
      -> route_with_provider -> (no_route | provider_failure) -> END
      -> score_candidates -> (no_route) -> END
      -> explain_and_export -> END
```

Every path ends with an explicit `status`; the graph never calls a routing
provider unless validation and geocoding both succeeded, and it never
returns without a terminal status.

## Setup

Requires Python 3.11+ and [Poetry](https://python-poetry.org/docs/#installation).

```bash
poetry install
cp .env.example .env
```

`poetry install` creates an in-project virtualenv (`.venv/`, see
`poetry.toml`) with both runtime and dev dependencies. Run any command
inside it with `poetry run <command>`, or `poetry shell` to activate it
directly.

Edit `.env` and set `ORS_API_KEY` to a valid
[openrouteservice](https://openrouteservice.org/dev/#/signup) API key.

### Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `ORS_API_KEY` | openrouteservice API key | (required for live routing) |
| `ORS_BASE_URL` | openrouteservice base URL | `https://api.openrouteservice.org` |
| `ORS_TIMEOUT_S` | ORS request timeout (seconds) | `10.0` |
| `ORS_MAX_RETRIES` | Retries for timeouts/5xx | `2` |
| `GEOCODER_PROVIDER` | Geocoder backend: `nominatim` (public API) or `pelias` (self-hosted ORS only; validated at startup) | `nominatim` |
| `GEOCODER_BASE_URL` | Nominatim base URL (ignored when `GEOCODER_PROVIDER=pelias`) | `https://nominatim.openstreetmap.org` |
| `GEOCODER_TIMEOUT_S` | Geocoder request timeout | `5.0` |
| `GEOCODER_USER_AGENT` | Required by Nominatim's usage policy | `bike-routing-agent/0.1` |
| `GEOCODER_CACHE_TTL_S` | In-memory geocode cache TTL | `3600` |
| `GEOCODER_AMBIGUITY_MARGIN` | Confidence gap below which top-2 results are "ambiguous" | `0.05` |
| `GEOCODER_MIN_CONFIDENCE` | Minimum confidence to auto-accept a top result | `0.3` |
| `VALHALLA_BASE_URL` | Base URL for the (stubbed) Valhalla adapter | `http://localhost:8002` |
| `EXPORT_DIR` | Local directory for GeoJSON/GPX artifacts | `exports` |
| `LOG_LEVEL` | Log level | `INFO` |

## Running the API

```bash
poetry run uvicorn bike_routing_agent.api:app --reload
```

The app also serves a built-in web UI (map-based route planner) at
<http://localhost:8000/>, with the interactive API docs at `/docs`.

## Command-line interface

Installing the package exposes a `bike-router` command (also runnable as
`python -m bike_routing_agent.cli`) organised into subcommand groups:

```bash
bike-router route plan --origin "Berlin Hbf" --destination "Potsdam" \
    --bike-type gravel --target-distance-km 30 --output plan.json
bike-router serve start --port 8000
bike-router docker up            # docker compose -f docker/compose.yaml up -d --build
bike-router docker logs -f
bike-router config show          # effective settings, secrets redacted
bike-router config check         # validate settings against the environment
bike-router providers list       # provider names and bike-type -> ORS profile map
```

`route plan` accepts `lat,lon` pairs or place text for `--origin`,
`--destination` and repeated `--via` waypoints, prints the JSON result and
exits `0` on `ready`, `1` on clarification/provider failure, `2` on usage
errors.

## Running with Docker

All container assets live in [`docker/`](docker/):

```bash
cp .env.example .env        # set ORS_API_KEY
docker compose -f docker/compose.yaml up --build
```

Open <http://localhost:8000/> for the web UI. Route artifacts are stored in
the named `pybikerouter-exports` volume. See
[docs/configuration.md](docs/configuration.md) for the optional
`--profile self-hosted` OpenRouteService stack.

### Example request

```bash
curl -X POST http://localhost:8000/v1/route/plan \
  -H 'Content-Type: application/json' \
  -d '{
    "origin": "Braunschweig Hauptbahnhof",
    "destination": "Wolfenbüttel",
    "constraints": {
      "bike_type": "gravel",
      "avoid_high_traffic_roads": true,
      "max_ascent_m": 500
    }
  }'
```

### Example response

```json
{
  "status": "ready",
  "route": {
    "provider": "ors",
    "provider_profile": "cycling-regular",
    "geometry_geojson": {"type": "LineString", "coordinates": []},
    "metrics": {
      "distance_m": 12500,
      "duration_s": 2700,
      "ascent_m": 85,
      "descent_m": 90,
      "surface_coverage": {},
      "unknown_surface_fraction": null
    },
    "score": 0.91,
    "score_breakdown": {"distance_fit": 1.0, "elevation_fit": 1.0, "warning_penalty": 0.0},
    "warnings": [],
    "provenance": {"provider": "ors", "profile": "cycling-regular"}
  },
  "explanation": "...",
  "artifacts": {
    "geojson_url": "/v1/routes/<id>.geojson",
    "gpx_url": "/v1/routes/<id>.gpx"
  },
  "clarification": [],
  "errors": []
}
```

If `origin`/`destination` are ambiguous or unresolved, `status` is
`awaiting_clarification` and `clarification` lists the candidate places to
choose from -- no route is generated from a guess.

## Bike types

`constraints.bike_type` selects the routing profile per engine and defaults to
`gravel`. Supported values: `road`, `gravel`, `touring`, `mountain`, `city`,
`ebike`, `commuter` (fast, low-traffic), `recumbent`. ORS ships four cycling
profiles, so e.g. `ebike` uses `cycling-electric` while `commuter` rides on
`cycling-regular`; with the self-hosted BRouter provider each type gets a
distinct cost model (see [docs/configuration.md](docs/configuration.md)).

## Linting and type checking

```bash
poetry run ruff check .
poetry run mypy src
```

Both run in CI on every push and pull request (see
`.github/workflows/ci.yml`).

## Running tests

```bash
poetry run pytest
```

Coverage is collected on every run and the suite fails below 80%
(`--cov-fail-under=80` in `tool.pytest.ini_options.addopts`; currently 97%
total, every module ≥84%).

Live tests that hit real external services are marked `@pytest.mark.live`
and excluded by default (see `tool.pytest.ini_options.addopts` in
`pyproject.toml`). Run them explicitly with:

```bash
poetry run pytest -m live
```

## Pre-commit hooks

Quality gates run automatically before each commit/push via
[pre-commit](https://pre-commit.com). Install the git hooks once after
`poetry install`:

```bash
poetry run pre-commit install --hook-type pre-push
```

On commit it runs whitespace/EOF/YAML/TOML/merge-conflict/large-file checks,
`ruff check --fix` and `mypy src`; the full pytest suite runs on `git push`
(mirroring CI). Run everything manually with
`poetry run pre-commit run --all-files`.

## Known limitations

- OSM tag completeness varies by region; surface/access metadata is
  frequently missing and is represented as uncertainty, not treated as
  favorable.
- Routing engines (ORS, Valhalla, BRouter) map bike categories to their own
  profiles differently; the mapping in `config.py` is an approximation,
  especially for gravel riding.
- Elevation data (ascent/descent) varies in accuracy between providers and
  is not always available.
- No route produced by this service is a safety guarantee. Explanations use
  hedged language ("better aligned with available map metadata") rather
  than claims like "safe route".
- The Valhalla adapter is a stub in this milestone; BRouter is wired up
  end to end but only against a self-hosted RouteServer (`ROUTING_PROVIDER=brouter`,
  see [docker/brouter/README.md](docker/brouter/README.md)), and
  openrouteservice remains the default engine.
- The Pelias geocoder option requires a self-hosted openrouteservice
  instance; the public `api.openrouteservice.org` does not serve Pelias
  (see [docs/geocoding.md](docs/geocoding.md)).

## Repository layout

```
src/bike_routing_agent/
├── api.py            FastAPI app (POST /v1/route/plan)
├── config.py          Settings + provider profile mapping
├── models.py          Pydantic v2 domain models
├── state.py            LangGraph RouteAgentState
├── graph.py            LangGraph wiring
├── errors.py           Structured provider/geocoding errors
├── providers/         GeocodeProvider / RoutingProvider adapters
│                      (ors_client.py: full ORS client -- directions, export,
│                       isochrones, matrix, snap, POIs, Vroom, elevation,
│                       Pelias geocoding, health; ors.py: the RoutingProvider;
│                       geocoder.py: Nominatim; pelias.py: Pelias on
│                       self-hosted ORS)
├── nodes/              One module per graph node
├── scoring/            Deterministic route scoring
├── exporters/          GeoJSON / GPX export
├── frontend/           Static web UI served at /
└── cli/                bike-router command line (route/serve/docker/config/providers)
docker/                 Dockerfile + compose.yaml
tests/
├── fixtures/           Sample provider responses
├── nodes/               One test module per graph node
├── providers/          Adapter unit tests (mocked HTTP)
├── graph/              Graph branch tests
├── exporters/          Export format tests
└── live/                Tests marked @pytest.mark.live
docs/                   Documentation suite (see docs/README.md)
```
