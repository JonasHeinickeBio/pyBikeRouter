# HTTP API

The FastAPI app (`bike_routing_agent.api:app`) exposes three endpoints. Run
it with:

```bash
poetry run uvicorn bike_routing_agent.api:app --reload
```

## POST /v1/route/plan

Plans a bike route. The request body is validated as `RoutePlanAPIRequest`
before the graph runs; anything that fails Pydantic validation is rejected by
FastAPI with `422` and never reaches the workflow.

### Request

```json
{
  "origin": "Braunschweig Hauptbahnhof",
  "destination": {"lon": 10.5233, "lat": 52.1553},
  "via": ["Riddagshausen"],
  "constraints": {
    "bike_type": "gravel",
    "target_distance_km": 25,
    "max_ascent_m": 300,
    "prefer_surfaces": ["paved"],
    "avoid_surfaces": ["gravel"],
    "avoid_high_traffic_roads": true,
    "avoid_ferries": true
  }
}
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `origin` | non-empty string **or** `Coordinate` | yes | A string is geocoded; a coordinate is used directly. |
| `destination` | non-empty string **or** `Coordinate` | yes* | Same. Omit for loop requests -- see `return_to_origin` below. |
| `via` | list, max 10 | no | Same shape as `origin`. |
| `constraints` | `RouteConstraints` | no | Defaults apply when omitted. |
| `departure_time` | ISO-8601 datetime | no | When the ride starts, for the weather forecast ([weather.md](weather.md)). Omitted: now. No UTC offset = UTC; more than 14 days ahead is rejected (`422`). |
| `max_alternatives` | integer `1..5` | no | Return at most this many *distinct* routes; see [Alternatives](#alternatives). Omitted: every scored candidate is returned. |

`Coordinate` is `{"lon": -180..180, "lat": -90..90}`. Constraint fields and
defaults:

| Field | Default | Constraints |
| --- | --- | --- |
| `bike_type` | `"gravel"` | one of `road`, `gravel`, `touring`, `mountain`, `city`, `ebike`, `commuter`, `recumbent` |
| `target_distance_km` | `null` | `0 < x <= 1000` |
| `max_distance_km` | `null` | `0 < x <= 1000`; must be >= `target_distance_km` |
| `max_ascent_m` | `null` | `0 <= x <= 10000` |
| `prefer_surfaces` / `avoid_surfaces` | `[]` | surface categories (`paved`, `masonry`, `compacted`, `loose`, `natural_soft`) or OSM `surface` values; the two lists may not resolve to the same category. Unrecognised words are accepted but unscored. Scored from OSM enrichment only when enabled and calibrated (see [scoring](scoring-and-exports.md#surface-preferences-issue-23)) |
| `avoid_high_traffic_roads` | `true` | |
| `avoid_ferries` | `true` | |
| `return_to_origin` | `false` | loop request: `destination` must be omitted and `target_distance_km` is required. |
| `loop_direction` | `"clockwise"` | one of `clockwise`, `counterclockwise`; only meaningful with `return_to_origin`. |

### Alternatives

With several routing engines (`ROUTING_PROVIDER=all`) the same corridor
often comes back more than once, snapped slightly differently. The score
node ranks all candidates (best score first; ties: shorter, then provider)
and recognises *near-identical* routes: two routes whose discrete Frechet
distance is within `ALTERNATIVE_DEDUP_THRESHOLD_M` (default 50 m) are one
alternative, and the better-scored one is kept.

- **`max_alternatives` omitted** (default): behaviour is unchanged -- every
  scored candidate is returned. Near-copies are still annotated:
  `duplicate_of` names the better route, and that route lists them under
  `duplicates`.
- **`max_alternatives = N`**: near-copies are dropped (still listed under the
  kept route's `duplicates`) and at most `N` distinct routes are returned.
  `route` is always `candidates[0]`, rank 1.
- `rank_rationale` is built from facts only, e.g. `rank 2: score 0.84 (0.07
  below rank 1); 1.2 km longer; 40 m less ascent`. It never claims a route
  is safer or better for you; it states how the routes differ.
- It only has an effect when more than one engine is configured; with one
  engine the single candidate is simply rank 1.
- Exports and the explanation always describe rank 1.
- **Style alternatives (BRouter).** With `BROUTER_ALTERNATIVES=true` (the default) one
  engine is enough to get alternatives: BRouter also routes the trip under the other
  profiles suggested for the bike type (e.g. gravel -> touring and mountain bike; see
  `BROUTER_ALTERNATIVE_PROFILES`). They carry `provenance.alternative_of` and are ranked
  like any candidate, except that they take rank 1 from the route made for the rider's own
  bike type only with a clear lead (+0.10 score, warning penalties equalised for this
  comparison), so a gravel request is not answered with a mountain-bike route on a near-tie.
- **`pros` / `cons`** (per distinct route, at most 3 each; empty with a single route) are
  facts with numbers relative to the other distinct routes: your own limits and target
  distance first, then shortest, fastest, least climbing, least on main roads without a
  bike lane (`metrics.main_road_share`, BRouter only), headwind and precipitation. A
  difference must clear both a relative and an absolute bar to be mentioned, a metric a
  route does not have is skipped (never read as zero), and nothing claims a route is safer
  or better for you. `metrics.main_road_share` is the length share on
  trunk/primary/secondary/tertiary roads without a mapped cycle lane/track or
  `bicycle=designated`: a proxy for exposure to motor traffic, not a measurement.
- The web UI shows them as *Alternative route* cards under the result (click one to see it
  on the map); near-copies are left out of the cards.
- The web UI exposes this as *Distinct alternatives (1-5)* in the constraints
  panel (empty sends no cap) and marks merged routes in the comparison table
  (`+N similar` on the kept route, `near-copy` on a listed duplicate).

### Loop requests

Setting `return_to_origin: true` makes a single-origin request: no
`destination`, a required `target_distance_km` to size the circuit. The
agent synthesizes two way-points around the origin (`loop_direction` picks
the handedness) and routes back to the start; the response explains the
plan and reports the synthesized geometry. Supplying `via` alongside a loop
is honored verbatim instead of resynthesized.

### Response

Always a `RoutePlanResponse` (HTTP `200`) with a `status` -- even when the
plan fails. The failure modes are structured, not exceptions:

| `status` | `route` | `candidates` | `clarification` | `errors` |
| --- | --- | --- | --- | --- |
| `ready` | the selected `RouteCandidate` | all scored candidates, best first | `[]` | `[]` |
| `awaiting_clarification` | `null` | `[]` | entries per unresolved field | optional detail |
| `invalid` | `null` | `[]` | optional (e.g. missing places) | validation detail |
| `provider_failure` | `null` | `[]` | `[]` | structured provider errors |
| `no_route` | `null` | `[]` | `[]` | why nothing was produced |

`ready` example (trimmed):

```json
{
  "status": "ready",
  "route": {
    "provider": "ors",
    "provider_profile": "cycling-regular",
    "geometry_geojson": {"type": "LineString", "coordinates": []},
    "metrics": {
      "distance_m": 12500.0,
      "duration_s": 2700.0,
      "ascent_m": 85.0,
      "descent_m": 90.0,
      "surface_coverage": {},
      "unknown_surface_fraction": null
    },
    "score": 0.91,
    "score_breakdown": {"distance_fit": 1.0, "elevation_fit": 1.0, "warning_penalty": 0.0},
    "warnings": [],
    "provenance": {"provider": "ors", "profile": "cycling-regular"}
  },
  "candidates": [
    { "provider": "ors", "provider_profile": "cycling-regular", "score": 0.91,
      "geometry_geojson": {"type": "LineString", "coordinates": []},
      "metrics": { "distance_m": 12500.0, "duration_s": 2700.0 },
      "score_breakdown": { "distance_fit": 1.0, "elevation_fit": 1.0, "warning_penalty": 0.0 },
      "warnings": [], "provenance": { "provider": "ors", "profile": "cycling-regular" },
      "raw_provider_response": null
    },
    { "provider": "brouter", "provider_profile": "trekking", "score": 0.84,
      "geometry_geojson": {"type": "LineString", "coordinates": []},
      "metrics": { "distance_m": 12900.0, "duration_s": 2800.0 },
      "score_breakdown": { "distance_fit": 0.9, "elevation_fit": 1.0, "warning_penalty": 0.0 },
      "warnings": [], "provenance": { "provider": "brouter", "profile": "trekking" },
      "raw_provider_response": null
    }
  ],
  "explanation": "This 12.5 km route was generated by ors ...",
  "artifacts": {
    "geojson_url": "/v1/routes/<32-hex>.geojson",
    "gpx_url": "/v1/routes/<32-hex>.gpx"
  },
  "clarification": [],
  "errors": []
}
```

Notes:

- `metrics.ascent_m` / `descent_m` / `duration_s` may be `null` when the
  provider does not supply them -- absence is reported as absence.
- `candidates` (issue #6) carries the scored candidates for the
  request, ranked best first (see [Alternatives](#alternatives)).
  `candidates[0]` is the selected candidate and equals `route`; the rest are
  the alternatives a multi-engine run produced, so clients can compare
  providers side by side. With a single routing provider the list has
  exactly one entry. `candidates` is `[]` whenever `status` is not
  `ready`.
- Every candidate carries `weather` (the forecast along that route at the
  departure time: `summary`, `samples[]` with head/crosswind, `advisories[]`, the
  provider's `attribution`) or `null`, and the response has `weather_status`
  (`ok`, `unavailable`, `not_covered`, `skipped`, or `null` when weather is off).
  Weather is best effort and never changes `status` ([weather.md](weather.md)).
- Every candidate carries `rank` (1-based position in the returned list),
  `rank_rationale` (a facts-only comparison to rank 1) and the duplicate
  bookkeeping `duplicates` / `duplicate_of`; all are optional additions,
  so existing clients can ignore them.
- With `ARTIFACT_BACKEND=s3` and `S3_PRESIGNED_URL_TTL_S` set, `GET
  /v1/routes/{filename}` answers `307` with a presigned object-store URL
  instead of streaming the file ([persistence.md](persistence.md#object-storage-s3)).
- `raw_provider_response` is always neutralised in API responses, on
  `route` and on every entry of `candidates` (the key remains, but is
  always `null`; the raw payload never leaves the server).
- `plan_id` (issue #7) identifies the recorded history entry
  (`GET /v1/history/plans/{plan_id}`) and, for `ready` plans, equals the
  artifact id. It is `null` when no database is configured or recording
  failed -- recording never fails a plan.
- `clarification` entries are `{field, candidates, hint}`; `candidates` are
  `GeocodeCandidate`s (`label`, `coordinate`, `confidence`, `source`) to
  choose from -- resend the request with a chosen `{"lon": ..., "lat": ...}`
  as the place to disambiguate. When no candidate could be found at all,
  `candidates` is empty and `hint` explains how to reword the place
  (e.g. full street name with house number and postcode).
- The `explanation` string is generated from route facts only and uses hedged
  language; it is not a safety assessment (see
  [scoring-and-exports.md](scoring-and-exports.md)).

## GET /v1/routes/{filename}

Serves an exported artifact. Filenames are `<uuid4 hex>.geojson` /
`.gpx`; the handler matches `^[0-9a-f]{32}\.(geojson|gpx)$` before touching
any storage, so path traversal and anything else are `404`. Missing files
are `404` as well. Artifacts come from the configured artifact store: files
under `EXPORT_DIR` (mounted as `./exports` under Docker) by default, or the
database with `ARTIFACT_BACKEND=database` ([persistence.md](persistence.md)).
Content types are `application/geo+json` and `application/gpx+xml`.

## GET /v1/history/plans

Provenance query over recorded plans, newest first (issue #7). Only
available when `DATABASE_URL` is configured -- otherwise `503`. Returns a
list of summaries (no geometry):

```json
[{ "plan_id": "<32-hex>", "created_at": "2026-09-30T12:00:00Z",
   "status": "ready", "bike_type": "gravel",
   "origin": {"lon": 10.52, "lat": 52.26}, "destination": {"lon": 10.53, "lat": 52.16},
   "candidates": [
     { "provider": "ors", "provider_profile": "cycling-regular", "score": 0.91,
       "distance_m": 12500.0, "duration_s": 2700.0, "ascent_m": 84.3,
       "selected": true, "rank": 1 } ] }]
```

| Query parameter | Meaning |
| --- | --- |
| `provider`, `profile` | plans with a recorded candidate from this engine / provider profile |
| `selected_only` | with the above: only count the candidate that was returned to the caller |
| `status` | one of the plan statuses (failures are recorded too) |
| `bike_type` | the requested bike type |
| `since`, `until` | ISO-8601 timestamps; `since` inclusive, `until` exclusive |
| `bbox` | `min_lon,min_lat,max_lon,max_lat`; candidates whose geometry bounding box intersects it |
| `limit` (1-200, default 50), `offset` | pagination |

Malformed `bbox`/`status`/paging values are `422`.

## GET /v1/history/stats

Evaluation aggregates over recorded plans (issue #7); `503` without a
database. Optional filters: `bike_type`, `since` (inclusive) and `until`
(exclusive) as ISO-8601 timestamps.

```json
{ "total_plans": 61,
  "by_status": {"ready": 53, "provider_failure": 8},
  "ready_rate": 0.87,
  "providers": [
    { "provider": "ors", "provider_profile": "cycling-regular",
      "candidates": 53, "selected": 15, "win_rate": 0.28,
      "mean_score": 0.77, "mean_distance_m": 13800.0,
      "mean_duration_s": 2940.0, "mean_ascent_m": 115.0,
      "mean_score_breakdown": {"distance_fit": 0.54, "elevation_fit": 0.53, "warning_penalty": 0.10} } ],
  "daily": [ {"date": "2026-09-25", "total": 7, "by_status": {"ready": 6, "provider_failure": 1}} ] }
```

`providers` covers `ready` plans only. `win_rate` is `selected / candidates`
for that engine/profile. Means skip candidates lacking the value and are
`null` when none have it. `daily` buckets by UTC day. `ready_rate` is `null`
when no plans match. A page rendering this is served at `/dashboard.html`.

## GET /v1/history/plans/{plan_id}

One recorded plan in full: the request as received, parsed constraints,
resolved endpoints, errors, explanation, artifact file names, and every
candidate with its full geometry, score breakdown and provenance (never raw
provider payloads). `404` for unknown or malformed ids, `503` without a
database. `plan_id` is the `plan_id` of a plan response, which is also the
artifact id.

## POST /v1/route/plan-text

Plan from a description (issue #30; [llm-parser.md](llm-parser.md)).

```json
{"text": "50 km gravel loop from Braunschweig, tomorrow at 8",
 "timezone": "Europe/Berlin", "max_alternatives": 3}
```

`text` is 1-500 characters; `timezone` (optional IANA name) anchors words like
"tomorrow"; `max_alternatives` is the same cap as on `/v1/route/plan`. The
response is the usual plan response plus `interpretation` (`request`,
`departure_time`, `notes`, `parser` provenance). `503` when
`LLM_PARSER_ENABLED` is off; parse failures are `200` with `status: "invalid"`
and a `llm_parser_*` error code.

## GET /v1/capabilities

`{"text_planning": bool, "weather": bool, "history": bool}` -- which optional
features this instance has, used by the web form to show or hide controls.

## GET /healthz

Liveness probe for the API process itself: `{"status": "ok"}`. It does not
probe the routing or geocoding providers (use `/readyz` for that); restarting
the process would not fix a dead upstream, so this is what container
healthchecks should call.

## GET /readyz

Readiness (issue #25): *can this instance plan right now?* It probes every
configured routing engine, the geocoder, the artifact store and, when a
database is configured, the database.

```json
{
  "status": "degraded",
  "ready": true,
  "checked_at": "2026-10-03T21:00:16+00:00",
  "components": [
    {"name": "ors", "kind": "routing", "required": false, "status": "unknown",
     "latency_ms": 567, "detail": "no health signal from this deployment",
     "checked_at": "..."},
    {"name": "valhalla", "kind": "routing", "required": false,
     "status": "unavailable", "latency_ms": 84, "detail": "unreachable",
     "checked_at": "..."},
    {"name": "nominatim", "kind": "geocoder", "required": true, "status": "ok",
     "latency_ms": 331, "detail": null, "checked_at": "..."}
  ]
}
```

Component `status` is `ok`, `degraded` (answers, but not healthy, e.g. Valhalla
still loading tiles), `unavailable`, or `unknown` (no health signal -- the
public openrouteservice has no health endpoint). `unknown` does not make the
instance unready.

| Overall `status` | HTTP | Meaning |
| --- | --- | --- |
| `ok` | `200` | everything probed is fine |
| `degraded` | `200` | can plan, but something is not: an engine of several is down, or an optional dependency such as the history database |
| `unavailable` | `503` | cannot plan: the only engine (or every engine) is down, the geocoder or artifact store is down, or the database is down while it holds the exports (`ARTIFACT_BACKEND=database`) |

Notes:

- With `ROUTING_PROVIDER=all`, one working engine is enough; none of them is
  individually `required`. With a single engine it is.
- History is recorded best effort, so the `database` component only gates
  readiness when the exports live there.
- Probes are concurrent, time-bounded (`HEALTH_PROBE_TIMEOUT_S`) and **cached**
  (`HEALTH_CACHE_TTL_S`, and a longer `HEALTH_GEOCODER_CACHE_TTL_S` because the
  default geocoder is the public Nominatim): polling `/readyz` does not turn
  into upstream traffic. Failures are cached too.
- Responses carry names and a fixed phrase per status only -- never URLs,
  keys or exception text. Details go to the server log.
- The Nominatim probe is `GET /status`, not a search; the Pelias geocoder is
  judged by the self-hosted ORS that serves it.

## Errors and status codes

| Situation | Result |
| --- | --- |
| Malformed request body | `422` from FastAPI validation |
| Valid request, no route / ambiguous place / provider down | `200` with the matching `status` |
| Unknown or malformed artifact filename | `404` |

The workflow guarantees a terminal status on every path, so a well-formed
request should never produce an unhandled `500` from planning logic itself.
