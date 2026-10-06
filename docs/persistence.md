# Persistence: route history and artifact storage (issue #7)

Off by default -- without `DATABASE_URL` the service is as stateless as
before (exports on local disk, nothing recorded). Setting it turns on a
PostGIS-backed **route history** and the `/v1/history/*` endpoints;
`ARTIFACT_BACKEND=database` additionally moves the GeoJSON/GPX exports into
the database so several API instances can share them.

## Layers

| Piece | Module | Role |
| --- | --- | --- |
| `ArtifactStore` protocol | `storage/artifacts.py` | `put` / `get` / `list_artifacts` / `delete`; what `explain_and_export` writes through, `GET /v1/routes/{filename}` reads from, and retention prunes. |
| `LocalArtifactStore` | `storage/artifacts.py` | Files under `EXPORT_DIR` (the pre-#7 behavior, still the default). |
| `RouteHistory` protocol | `storage/history.py` | `save` / `get` / `query` / `stats`, plus the retention queries (`find_older_than`, `delete_plans`, `referenced_artifacts`, `maintenance_lock`). |
| `InMemoryRouteHistory` | `storage/history.py` | Process-local reference implementation with the same filter semantics as SQL; used by tests. |
| `PostgresDatabase`, `PostgresRouteHistory`, `PostgresArtifactStore` | `storage/postgres.py` | PostGIS backend. Lazy connection pool; the schema is managed by migrations. |
| `migrate` | `storage/migrate.py`, `storage/migrations/*.sql` | Versioned SQL migrations with a checksum-verifying, advisory-locked runner. |
| `S3ArtifactStore` | `storage/s3.py` | Any S3-compatible object store (AWS S3, Ceph, SeaweedFS, ...). Needs the `s3` extra. |
| `prune` | `storage/retention.py` | Expires old plans and the artifacts only they reference. |

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

## Evaluation dashboard

`GET /v1/history/stats` (see [api.md](api.md)) aggregates the recorded
plans, optionally narrowed by `bike_type`, `since` and `until`:

- **Outcomes**: total plans, counts per final status, and the `ready` rate.
- **Per engine/profile** (over `ready` plans): how many plans it took part
  in, how often its candidate was the one returned (`win_rate`), and mean
  score, distance, duration and ascent. Means skip candidates that did not
  report a value -- a missing ascent is not counted as 0 -- and are `null`
  when nobody reported one.
- **Mean score breakdown** per engine/profile, i.e. what a weight
  calibration (`scripts/calibrate.py`) reasons about, measured on real
  requests instead of the curated benchmark.
- **Daily volume** by UTC day and status.

`/dashboard.html` (linked from the planner's sidebar) renders these. It
shows a clear message instead of an empty page when no database is
configured (`503`) or no plans match the filters. The in-memory and PostGIS
backends are tested against the same aggregate contract.

Interpretation caveat: a win rate describes what happened in the recorded
traffic, scored by the current heuristic -- it says which engine the scorer
prefers, not which route is better or safer.

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

## Object storage (S3)

`ARTIFACT_BACKEND=s3` keeps the exports in an S3-compatible bucket, independent
of the database, so several API instances share them without PostGIS.

```dotenv
ARTIFACT_BACKEND=s3
S3_BUCKET=bike-exports
S3_PREFIX=routes            # optional key prefix
S3_ENDPOINT_URL=http://127.0.0.1:9000   # omit for AWS S3
S3_REGION=eu-central-1      # optional
S3_PATH_STYLE=true          # most self-hosted S3 servers need it
AWS_ACCESS_KEY_ID=...       # credentials: boto3's normal chain, never project settings
AWS_SECRET_ACCESS_KEY=...
```

Install the extra: `poetry install --extras s3` (or `pip install
'bike-routing-agent[s3]'`). The bucket must exist; the app never creates one.

`GET /v1/routes/{filename}` streams the object through the API by default, which
keeps one URL, the right media type and no bucket exposure. Setting
`S3_PRESIGNED_URL_TTL_S` (1-604800) makes it answer `307` with a presigned URL
instead, so downloads bypass the API (the bucket must then be reachable by
clients; the link is not checked for existence).

For development there is an `s3` compose profile with a local S3-compatible
store (SeaweedFS; MinIO withdrew its community container images in 2026 and
archived the project). It has **no authentication** -- loopback, development
only:

```bash
docker compose -f docker/compose.yaml --profile s3 up -d s3-dev
curl -X PUT http://127.0.0.1:8333/exports        # create the bucket
```

and point the settings above at `S3_ENDPOINT_URL=http://127.0.0.1:8333` with any
`AWS_*` credentials.

## Retention

Nothing is deleted unless you run it. `RETENTION_MAX_AGE_DAYS` (unset = keep
forever, the default) defines how long plans live; the command applies it:

```bash
bike-router retention prune                 # dry run: report only
bike-router retention prune --execute       # delete
bike-router retention prune --execute --max-age-days 30 --batch-size 200
```

The output is a JSON report (`plans`, `plan_artifacts`, `orphans`,
`bytes_freed`, `errors`); exit status is `0` on success, `1` on partial
failures or when another prune holds the lock, `2` when retention is off or the
arguments are invalid. Run it from cron, systemd or a Kubernetes CronJob; there
is deliberately no background thread in the API (surprising with several
workers).

Rules:

- A plan older than the max age is deleted from the history **first** (its
  candidates go with it), then the artifacts only it referenced. A crash in
  between leaves unreferenced artifacts, never a plan pointing at a missing
  file.
- An artifact still referenced by a *retained* plan is never deleted.
- Artifacts no plan references (a failed history write, an interrupted run) are
  orphans and are swept once older than `RETENTION_ORPHAN_GRACE_HOURS` (24), so
  an export whose plan record is about to be saved is not caught mid-way.
- Without a database there are no references to honour: artifacts are simply
  expired by age (local directory mtime, or the object's last-modified time).
- Concurrent runs are serialised by a PostgreSQL advisory lock; a second run
  exits with status 1 without touching anything.
- A dry run uses the same code path as a real run, so its numbers are what the
  real run will delete (and a failed delete is reported and does not stop the run).
- Only files named like generated exports (`<32 hex>.geojson|.gpx`) are ever
  listed or deleted; other files in the directory or bucket are left alone.

**Privacy.** History rows contain the origin and destination of real requests.
For a public deployment set `RETENTION_MAX_AGE_DAYS` to the shortest period your
evaluation needs and run the prune on a schedule. There is no per-person erasure
(the service has no user identity); deletion is by age only.

## Schema migrations

The schema is a list of plain SQL files, `storage/migrations/NNNN_name.sql`,
applied in order by a ~150-line runner (`storage/migrate.py`): no ORM, no extra
dependency, and every change is the reviewable SQL it is. A
`schema_migrations(version, name, checksum, applied_at)` table records what ran.

```bash
bike-router db status     # applied / pending (exit 1 when something is pending)
bike-router db migrate    # apply pending migrations (idempotent)
```

- **When it runs.** By default the first database use applies pending
  migrations (`AUTO_MIGRATE=true`), so a fresh database "just works". For
  deployments that migrate in a release step, set `AUTO_MIGRATE=false`: the app
  then never touches the schema and only logs a warning when migrations are
  pending; run `bike-router db migrate` before rolling out.
- **Several instances.** The runner holds the maintenance advisory lock (the
  same one retention uses) for the whole run, so instances starting together
  serialise: one migrates, the others wait and find nothing to do. A retention
  prune in progress also makes a starting instance wait.
- **Atomic.** Each migration runs in its own transaction together with its
  bookkeeping row; a failing migration leaves the database at the previous
  version and stops the run.
- **Guard rails.** The runner refuses to continue when an applied migration's
  file was edited (checksum) or when the database is *newer* than the code (an
  older release must not run against a newer schema). Versions must be
  contiguous from 1.
- **Adopting existing databases.** `0001_initial.sql` is idempotent (`IF NOT
  EXISTS` everywhere), so a database created before migrations existed is
  adopted by running it and recording version 1; no data is touched.
- **Writing one.** Add `NNNN_short_name.sql` with the next number. Never edit
  an applied file. Anything that must not run twice (an `ALTER TABLE`) is fine
  in a migration -- only `0001` has to be idempotent.
- **Down migrations are not supported** (restore from a backup instead).

## Recording from the CLI

`bike-router route plan` records the plan in the history when `DATABASE_URL` is
set, exactly like the API: best effort (a database outage prints a warning on
stderr and never fails the command), the `plan_id` appears in the JSON output
(`null` when nothing was recorded), and failed plans are recorded too.
`--no-record` skips it. Without `DATABASE_URL` nothing changes.

## Open follow-ups

- **Dashboard depth**: the aggregates are fixed-shape. Percentiles, score
  distributions, per-region breakdowns and CSV export are not built.
- **Retention by count/size** (`RETENTION_MAX_PLANS`, quotas) is not built;
  the policy is age-only.
