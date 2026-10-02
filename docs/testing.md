# Testing

```bash
poetry run pytest            # default run: everything except `live`
poetry run pytest -m live    # tests that hit real external services
poetry run pytest tests/nodes -q   # one area
```

Configuration lives in `pyproject.toml`: `testpaths = ["tests"]`,
`asyncio_mode = "auto"` (no per-test asyncio decorators needed), and

```toml
addopts = "-m 'not live' --cov=bike_routing_agent --cov-report=term --cov-fail-under=80"
```

so the default run never touches the network and always reports coverage.

## Layout

| Path | What it covers | Network |
| --- | --- | --- |
| `tests/test_models.py` | Domain models: bounds, frozen `Coordinate`, `PlaceInput` exactly-one-of, constraint cross-checks | mocked/none |
| `tests/test_config.py` | `Settings` validation (pelias/public-ORS rejection), `build_providers` wiring | none |
| `tests/test_errors.py` | Error `to_dict()` shapes and codes | none |
| `tests/test_scoring.py` | Score math: weights, linear penalties, floors, uncertainty notes | none |
| `tests/test_api.py` | Endpoint contracts via `TestClient` + a fake-graph patch: statuses, clarification, artifact URL pattern, filename/path-traversal 404s | fake providers |
| `tests/nodes/` | One file per graph node, calling node builders with fake providers/fakes for everything else | fake providers |
| `tests/graph/test_graph.py` | Full compiled graph through every branch/terminal status | fake providers |
| `tests/providers/` | ORS client, ORS adapter, Nominatim and Pelias geocoders | `respx`-mocked HTTP |
| `tests/exporters/` | GeoJSON/GPX output shapes | none |
| `tests/fixtures/` | Recorded sample responses (ORS directions, ORS no-route, Nominatim single/ambiguous) | -- |
| `tests/storage/` | Artifact stores and the route-history contract. `test_history.py` runs the same behavioral tests against the in-memory and PostGIS backends | none (PostGIS params are `live`) |
| `tests/live/` | Real ORS/Nominatim calls through the API | **real** |

`tests/conftest.py` exposes the fixtures as `ors_directions_response`,
`ors_no_route_response`, `nominatim_single_response`,
`nominatim_ambiguous_response`.

### PostGIS tests

The `postgres` params of `tests/storage/test_history.py` and everything in
`tests/storage/test_postgres.py` are `live` and need a PostGIS server
(they drop and recreate their tables, so never point them at real data):

```bash
docker run --rm -d --name pg-test -p 127.0.0.1:55432:5432 -e POSTGRES_USER=test \
    -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test postgis/postgis:16-3.4
TEST_DATABASE_URL=postgresql://test:test@127.0.0.1:55432/test pytest -m live tests/storage
```

Without `TEST_DATABASE_URL` they skip.

## Conventions

- **HTTP is mocked with `respx`** everywhere except `live/`. Tests assert on
  the structured error mapping (429 -> rate-limited, 5xx -> unavailable after
  retries, malformed -> bad-response, empty features -> no-route), not just
  the happy path.
- **Node tests build nodes with fakes**: fake `RoutingProvider`/
  `GeocodeProvider`/`llm_parser` objects, driving each node's status
  decisions directly. A node test never imports a concrete adapter.
- **Graph tests** call `build_graph` with fakes and assert the final state's
  terminal status per branch -- including the guarantee that invalid input
  never reaches the routing provider.
- **API tests** patch the module graph with a fake final state and assert
  response shaping (stripped raw payloads, artifact URL pattern, `404` for
  anything off `^[0-9a-f]{32}\.(geojson|gpx)$`).
- **Live tests** (`tests/live/`) are the integration truth: real geocoding,
  real directions, real export round-trip. They are opt-in because they need
  `ORS_API_KEY` and network access.

## Coverage

`pytest` runs with `--cov=bike_routing_agent --cov-fail-under=80` (set in
`addopts`): any run below 80% total coverage fails. The per-module floor is
respected too -- every module currently sits at 84% or above, most at 100%.

## CI

`.github/workflows/ci.yml` runs on every push to `main`, every pull request,
manually (`workflow_dispatch`) and weekly on `main` (to catch upstream
drift). A newer push to the same PR cancels the older run; every job has a
timeout and the token is read-only.

| Job | What it does |
| --- | --- |
| `lint` | `ruff check .` (annotations appear inline on the PR) |
| `typecheck` | `mypy src` (tests are linted and run, not mypy-checked) |
| `test (3.11)`, `test (3.12)` | `pytest --maxfail=1`; coverage gate (80%) comes from `addopts`; 3.12 uploads `coverage.xml` and a coverage table in the run summary |
| `postgis (route history + artifacts)` | Starts a PostGIS service and runs the `live`-marked `tests/storage` suite -- the PostGIS backend is otherwise never exercised by the default run |
| `frontend (js syntax)` | `node --check` on every script in `frontend/` |
| `shellcheck + workflow lint` | `shellcheck` on `scripts/*.sh` and `actionlint` on the workflows |
| `docker build + smoke test` | Builds `docker/Dockerfile` (layer-cached) and checks the container serves `/healthz`, the planner, the dashboard, and answers `503` on the history endpoints without a database |
| `CI passed` | Aggregates the jobs above under one name -- require this single check in branch protection instead of each job |

Other workflows:

- `codeql.yml` -- CodeQL (`security-extended`) for Python, the JavaScript
  frontend and the workflows themselves, on push, PRs and weekly.
- `dependency-review.yml` -- on PRs that change dependencies, Dockerfile or
  workflows, fails on newly introduced `high`+ severity vulnerabilities.
- `dependabot.yml` -- weekly updates for pip (minor/patch grouped), GitHub
  Actions (including the local composite action) and the Docker base image.

The Python/Poetry setup is shared by the jobs through the local composite
action `.github/actions/setup-python-poetry` (pinned Poetry, lock-keyed venv
cache, `--extras db`). Live tests that need external services or secrets (ORS,
Overpass, BRouter) are still run by hand, not in CI.
