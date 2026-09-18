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
| `tests/live/` | Real ORS/Nominatim calls through the API | **real** |

`tests/conftest.py` exposes the fixtures as `ors_directions_response`,
`ors_no_route_response`, `nominatim_single_response`,
`nominatim_ambiguous_response`.

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

`.github/workflows/ci.yml` runs, on every push and pull request:

```bash
ruff check .
mypy src
pytest --maxfail=1
```

Coverage gating comes from the `addopts` above, so CI enforces it as well.
Note CI type-checks `src` only; tests are linted and run, but not mypy-checked.
