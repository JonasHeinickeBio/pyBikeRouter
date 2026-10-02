# Persistence: route history and artifact storage (issue #7)

Off by default -- without `DATABASE_URL` the service is as stateless as
before (exports on local disk, nothing recorded). Setting it turns on a
PostGIS-backed **route history** and the `/v1/history/*` endpoints;
`ARTIFACT_BACKEND=database` additionally moves the GeoJSON/GPX exports into
the database so several API instances can share them.

## Layers

| Piece | Module | Role |
| --- | --- | --- |
| `ArtifactStore` protocol | `storage/artifacts.py` | `put(name, content)` / `get(name)`; what `explain_and_export` writes through and `GET /v1/routes/{filename}` reads from. |
| `LocalArtifactStore` | `storage/artifacts.py` | Files under `EXPORT_DIR` (the pre-#7 behavior, still the default). |
| `RouteHistory` protocol | `storage/history.py` | `save(record)` / `get(plan_id)` / `query(filter)`. |
| `InMemoryRouteHistory` | `storage/history.py` | Process-local reference implementation with the same filter semantics as SQL; used by tests. |
| `PostgresDatabase`, `PostgresRouteHistory`, `PostgresArtifactStore` | `storage/postgres.py` | PostGIS backend. Lazy connection pool, schema created on first use. |

`psycopg` is the optional `db` extra (`poetry install --extras db`); it is
imported only when `DATABASE_URL` is set, so a deployment without a
database never needs it. The Docker image and CI install it.

## What is recorded

Every plan the API answers is saved -- failures too (`provider_failure`,
`no_route`, `awaiting_clarification`, `invalid`), because provider failure
rates and ambiguity are exactly what evaluation needs.

- **plan**: id, timestamp, status, bike type, the request as received, the
  parsed constraints, resolved origin/destination (PostGIS points), errors,
  explanation, artifact file names.
- **candidates** (`ready` plans only): *every* scored candidate, with
  provider, profile, score and breakdown, distance/duration/ascent/descent,
  warnings, provenance, rank and whether it was the one returned. The full
  candidate JSON is stored losslessly (elevations included); a 2D
  `LineString` copy is stored in a GiST-indexed `geom` column for spatial
  queries. A geometry that cannot be reduced to one line (a disconnected
  MultiLineString) keeps its JSON and gets a `NULL` `geom`.
- **Never** `raw_provider_response`: the API contract says raw provider
  payloads never leave the server, and history is built from the same
  neutralised candidates.

The plan id equals the artifact id (`/v1/routes/<plan_id>.geojson`), so a
downloaded file always links back to its history entry.

Recording is best effort: it runs after the plan is computed, and a failure
is logged and reported as `plan_id: null` instead of failing the request.

## Provenance queries

`GET /v1/history/plans` and `GET /v1/history/plans/{plan_id}` -- see
[api.md](api.md). `provider`/`profile` match any recorded candidate; add
`selected_only=true` to ask "which plans did *this* engine actually win".
`bbox` matches candidate geometries by bounding-box intersection (PostGIS
`&&`). These are the same columns a calibration or dashboard job would
aggregate (`candidates.provider`, `provider_profile`, `score`,
`score_breakdown`, ...).

## Running it

```bash
docker compose -f docker/compose.yaml --profile postgis up -d
poetry install --extras db
```

```dotenv
DATABASE_URL=postgresql://bikerouter:bikerouter@127.0.0.1:5432/bikerouter
ARTIFACT_BACKEND=database   # optional
```

Inside the compose network the host is `postgis`, not `127.0.0.1`. The demo
credentials in the compose file are for local development only.

## Open follow-ups

- **Evaluation dashboards** (the last bullet of issue #7): the schema and
  query endpoint are the foundation; aggregate endpoints and a frontend
  page are not built.
- **Schema migrations**: tables are created with `CREATE ... IF NOT EXISTS`
  on first use. There is no migration tool yet, so a column change needs
  one (Alembic or plain versioned SQL) before the schema evolves.
- **Retention**: neither history nor artifacts are pruned (see the artifact
  lifecycle item in [roadmap.md](roadmap.md)).
- **CLI**: `bike-router route plan` honors `ARTIFACT_BACKEND` but does not
  record history; only the API does.
- **Object storage** (S3 etc.) is a third `ArtifactStore`; nothing in the
  protocol assumes a database.
