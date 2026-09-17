# bike-routing-agent

[![CI](https://github.com/JonasHeinickeBio/pyBikeRouter/actions/workflows/ci.yml/badge.svg)](https://github.com/JonasHeinickeBio/pyBikeRouter/actions/workflows/ci.yml)

An OSM-based bike-routing service: a typed, provider-neutral domain model,
an openrouteservice adapter, and a LangGraph workflow that turns a
validated route request into an explainable, exportable cycling route.

The LLM (where used) may parse free text, ask clarification questions, and
explain a chosen route. It never invents coordinates, geometry, elevation,
or route suitability -- all of that comes from OSM-derived routing engines.

## Architecture

```
FastAPI
  |
  v
LangGraph RouteAgentState
  |
  +--> parse_request        (structured input first; LLM extraction is an injectable dependency)
  +--> validate_request      (Pydantic)
  +--> geocode_locations     (OSM Nominatim; ambiguity -> clarification, never a guess)
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
| `GEOCODER_BASE_URL` | Nominatim base URL | `https://nominatim.openstreetmap.org` |
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

Live tests that hit real external services are marked `@pytest.mark.live`
and excluded by default (see `tool.pytest.ini_options.addopts` in
`pyproject.toml`). Run them explicitly with:

```bash
poetry run pytest -m live
```

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
- Valhalla/BRouter adapters are stubs in this milestone; only
  openrouteservice is wired up end to end.

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
├── nodes/              One module per graph node
├── scoring/            Deterministic route scoring
└── exporters/          GeoJSON / GPX export
tests/
├── fixtures/           Sample provider responses
├── providers/          Adapter unit tests (mocked HTTP)
├── graph/              Graph branch tests
├── exporters/          Export format tests
└── live/                Tests marked @pytest.mark.live
```
