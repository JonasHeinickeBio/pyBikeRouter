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
- Every candidate carries `rank` (1-based position in the returned list),
  `rank_rationale` (a facts-only comparison to rank 1) and the duplicate
  bookkeeping `duplicates` / `duplicate_of`; all are optional additions,
  so existing clients can ignore them.
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

## GET /healthz

Liveness probe for the API process itself: `{"status": "ok"}`. It does not
probe the routing or geocoding providers -- provider health is available
separately via the adapters' `health()` methods (not exposed over HTTP in
this milestone).

## Errors and status codes

| Situation | Result |
| --- | --- |
| Malformed request body | `422` from FastAPI validation |
| Valid request, no route / ambiguous place / provider down | `200` with the matching `status` |
| Unknown or malformed artifact filename | `404` |

The workflow guarantees a terminal status on every path, so a well-formed
request should never produce an unhandled `500` from planning logic itself.
