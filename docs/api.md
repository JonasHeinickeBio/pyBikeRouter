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
| `poi_stops` | object | no | Route past the best-known sights: `{"count": 1..5 (2), "categories": [sight kinds], "corridor_km": 0.5..15 (5), "min_fame": 1..200 (5)}` (`min_fame`: described in at least this many languages). Not with `return_to_origin`; `via` + `count` may not exceed 10. See [pois.md](pois.md). |
| `routing_engines` | list of `ors` / `brouter` / `valhalla` | no | Plan with these engines for this request instead of the configured ones (only names in `capabilities.engines` are used; none usable = the configured ones). The web form's *Plan this trip with openrouteservice* after BRouter lacked map data. |
| `max_alternatives` | integer `1..5` | no | Return at most this many *distinct* routes; see [Alternatives](#alternatives). Omitted: every scored candidate is returned. |

`Coordinate` is `{"lon": -180..180, "lat": -90..90}`. Constraint fields and
defaults:

| Field | Default | Constraints |
| --- | --- | --- |
| `bike_type` | `"gravel"` | one of `road`, `gravel`, `touring`, `mountain`, `city`, `ebike`, `commuter`, `recumbent` |
| `target_distance_km` | `null` | `0 < x <= 1000` |
| `max_distance_km` | `null` | `0 < x <= 1000`; must be >= `target_distance_km` |
| `max_ascent_m` | `null` | `0 <= x <= 10000` |
| `prefer_surfaces` / `avoid_surfaces` | `[]` | surface categories (`paved`, `masonry`, `compacted`, `loose`, `natural_soft`), `unpaved` (all the unpaved ones) or OSM `surface` values; the two lists may not resolve to the same category. Unrecognised words are accepted but unscored, and the `explanation` names them. Scored from the OSM enrichment when it ran, else from the engine's own surface shares (BRouter), when every candidate has them (see [scoring](scoring-and-exports.md#surface-preferences-issue-23)) |
| `avoid_high_traffic_roads` | `true` | BRouter applies it per request by setting the profile's traffic switch (`profile:consider_traffic=1`/`0`, `consider_traffic_estimate` for gravel; see [providers.md](providers.md#brouter-per-request-settings)); the setting used is recorded in `provenance.profile_overrides`. ORS and Valhalla cannot apply it (declared in `warnings`). It also enters the score when every candidate carries a traffic estimate (BRouter): [scoring-and-exports.md](scoring-and-exports.md#traffic-issue-23) |
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
- `stretches` (BRouter routes; empty from other engines): where the surface and the traffic exposure
  change, `{"from_index": 120, "to_index": 410, "surface": "unpaved", "traffic": "off_road"}` -- vertex
  indices into `geometry_geojson` (the stretch is the line from the first to the second), `surface` one
  of `paved`/`unpaved`/`cobbles`/`unknown`, `traffic` one of `busy`/`some`/`quiet`/`off_road`/`unknown`
  (`unknown` = untagged). The same tags as `engine_surface_shares` and `traffic_shares`, in order.
- `cues` (BRouter routes; empty from other engines): the turn-by-turn instructions in route order,
  `{"kind": "left", "distance_m": 412.3, "lon": ..., "lat": ..., "exit": null, "angle_deg": -90}`.
  `kind` is `left`, `right`, `slight_left`, `slight_right`, `sharp_left`, `sharp_right`, `keep_left`,
  `keep_right`, `uturn` or `roundabout` (then `exit` says which); `distance_m` is from the start. The
  engine gives no street names ([providers.md](providers.md#brouter-providersbrouterpy)).
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
  comparison), so a gravel request is not answered with a mountain-bike route on a near-tie;
  `rank_rationale` then says so ("... an alternative profile must lead by 0.10 to rank above the
  route for your bike type") instead of crediting the raw score. They are best effort within
  `BROUTER_ALTERNATIVES_TIMEOUT_S`: alternatives still running at the deadline are dropped.
- **`pros` / `cons`** (per distinct route, at most 3 each; empty with a single route) are
  facts with numbers relative to the other distinct routes: your own limits and target
  distance first, then shortest, fastest, least climbing, least on main roads without a
  bike lane (`metrics.main_road_share`, BRouter only), headwind and precipitation. A
  difference must clear both a relative and an absolute bar to be mentioned, a metric a
  route does not have is skipped (never read as zero), and nothing claims a route is safer
  or better for you. `metrics.main_road_share` is the length share on
  trunk/primary/secondary/tertiary roads without a mapped cycle lane/track or
  `bicycle=designated`: a proxy for exposure to motor traffic, not a measurement. Segments
  without way tags are not counted as quiet road: with fewer than 80 % of the length tagged
  the share is `null`.
- **Traffic estimate** (`metrics.traffic_shares`: `busy` / `some` / `quiet` / `off_road`, summing
  to 1; BRouter only; never feeds the score). Every way segment gets an exposure from what is
  mapped, then the length is split into bands. The base is the road class (motorway/trunk 1.0,
  primary 0.9, secondary 0.75, tertiary 0.5, unclassified 0.25, residential 0.15, service and
  living street 0.05; paths, tracks and cycleways 0). It is then scaled: a separate cycle track
  beside the road x0.15, a painted lane x0.5, a way closed to cars (`access` / `motor_vehicle` =
  no, agricultural, forestry ...) x0.1, a speed limit of 30 km/h or less x0.6, 60-79 km/h x1.2,
  80+ x1.4; a way designated for bikes is 0; `bicycle=use_sidepath` is not discounted because the
  route is on the road itself. Bands: `busy` from 0.6, `some` from 0.2, `quiet` above 0,
  `off_road` = 0. The weights are judgement, not measurement: there are no traffic counts in OSM,
  so this is an estimate and is worded as one everywhere ("Traffic (estimate): ..."). On
  Braunschweig - Hamburg (195 km) the gravel route came out at 0 % busy, 9 % some traffic, 19 %
  quiet streets, 72 % paths and tracks; BRouter's `fastbike` route over the same trip at 27 %
  busy, 54 % some traffic, 12 % quiet, 8 % paths and tracks (where the older main-road share
  said 72 %, because it did not tell a secondary road with a cycle track from one without).
  With fewer than 80 % of the length tagged the field is empty. `metrics.traffic_index` is the
  length-weighted mean of the same per-segment exposure (0..1), one number to sort or optimise by.
- **`optimize`** (request) and **`optimization`** (response): fetch several routes and select the
  best fit for goals such as `traffic` and `paved` instead of the best fit for one bike type;
  `optimization` carries the goals, how many routes were compared, the choice with its numbers and
  the trade-offs, and a table of every route. `candidates` are then ranked by the fit and `route`
  is the best one; the `pois` goal counts well-known places near each route (`optimize.poi`:
  `categories`, `corridor_m`, `min_fame`) and names the ones the winner passes;
  `optimize.detours: true` also tries routes bent to either side of the direct line (off by
  default, a few more requests) ([optimizer.md](optimizer.md)). A bad spec (unknown metric, a metric twice,
  `paved` with `unpaved`, weight outside 0..10, unknown field) is a `422`.
- **Surface is a pro or a con depending on who rides** (`metrics.engine_surface_shares`:
  paved / unpaved / cobbles / unknown from the OSM `surface` tags BRouter routed over;
  never feeds the score). Paved counts as a pro for `road`, `city`, `commuter` and
  `recumbent`, unpaved for `mountain`; `gravel`, `touring` and `ebike` are neutral ("easy
  gravel": no surface line either way). Your own `prefer_surfaces` / `avoid_surfaces` win over
  the bike type, so a gravel request that prefers `compacted`/`loose` (off-road gravel) is
  treated like a mountain bike, and contradictory wishes count as neutral. Lines:
  `Most paved (90%)` / `Less paved (77% vs 90%)`, or `Most off-road (87% unpaved)` /
  `Less off-road (2% unpaved vs 75%)`. Only differences of 10 points or more are stated, and
  a route with more than 30 % of its length without a surface tag is not judged at all:
  unknown is never read as paved or unpaved.
- The web UI shows them as *Alternative route* cards under the result (click one to see it
  on the map); near-copies are left out of the cards. Under the cards, *Download* saves the
  route you are viewing as GPX or GeoJSON: built in the browser from the candidate already in
  the response (`frontend/export.js`, the same two formats as the server's exporters, which a
  test compares), so any alternative can be taken on the road. The files linked at the bottom
  of the result are the server's and always the top-ranked route.
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

An `errors` entry with `code: "routing_area_not_covered"` means a routing engine has no map data
for the trip (BRouter: a missing tile). It also appears next to a `ready` result when another
engine answered. `detail` names the tiles and the official download location --
[providers.md](providers.md#brouter-map-coverage).

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
  Weather is best effort and never changes `status` ([weather.md](weather.md)). `weather.wind_effect`
  (`still_air_s`, `adjusted_s`, `effect_s`, `range_s`) is an estimate of what the wind does to the
  travel time for the same effort as in still air, `null` for e-bikes and without an engine duration;
  each `departure_options[]` has `wind_effect_s` for its own hour.
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

### Where the time went, the cache and abandoned requests

- **`?trace=true`** adds `trace` to the response: one entry per step of the planner, in the order it
  ran -- `{"node": "route_with_provider", "ms": 7859.6, "status": "in_progress", "ok": true}`. The steps are
  `parse_request`, `validate_request`, `geocode_locations`, `select_poi_stops`, `route_with_provider`,
  `enrich_candidates`, `score_candidates`, `optimize_candidates`, `weather_candidates`,
  `annotate_alternatives`, `explain_and_export`. Without the parameter `trace` is `null`. Every response
  carries a **`Server-Timing`** header with the same numbers (`route_with_provider;dur=7859.6, ...,
  total;dur=...`), which a browser's network panel shows, and the service log has one line per step
  (`plan node=... ms=... status=...`).
- **A short-lived plan cache** (`PLAN_CACHE_TTL_S`, default 300 s, `0` turns it off): an identical request
  is answered from the cache -- `served_from_cache: true`, `cache_age_s` -- without routing again. The
  key is everything the planner is given (places, constraints, departure, engines, stops, optimiser spec);
  a plan "for now" (no `departure_time`) is shared for at most five minutes, so "now" never means "whenever
  the first one ran". Only `ready` plans are kept; a question, an error or "no route" is asked again. The
  cache uses the shared backend (`CACHE_BACKEND`, memory or Redis).
- **A request nobody waits for is cancelled.** When the client closes the connection, the plan stops: the
  routing requests still queued are never sent (BRouter does one at a time, so an abandoned plan used to
  delay everyone behind it; one that BRouter has already started it finishes on its own). The server logs
  `client went away` and the request ends with `499`.

### POST /v1/route/plan/stream

The same plan, told as it happens (server-sent events, `text/event-stream`), for a page that wants to show
progress:

| event | data |
| --- | --- |
| `step` | after every node: `{"node", "label", "ms", "status"}` |
| `route` | as soon as there is a best route (after scoring, again after the optimiser): `{"provider", "provider_profile", "geometry_geojson", "distance_m", "duration_s"}` -- draw it while the forecast, the notes on the alternatives and the files are still made |
| `result` | once, last: exactly the body `POST /v1/route/plan?trace=true` returns |
| `error` | the plan crashed: `{"detail"}` (no internals); the stream ends |

The body is validated like `/v1/route/plan` (an invalid request is a plain `422` before any event). A client
that disconnects cancels the plan.

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

`{"text_planning": bool, "weather": bool, "history": bool, "pois": bool, "segment_downloads":
bool, "chat": bool, "optimizer": bool, "engines": ["brouter", "ors"]}` -- which optional features this instance has, used by the
web form to show or hide controls; `engines` are the names a plan request may give in
`routing_engines` (the configured engines plus optional ones the server can use).

## POST /v1/chat

One turn of the chat ([chat.md](chat.md)). Body: `{"message": "...", "session_id": "<from the last reply>",
"timezone": "Europe/Berlin", "context": {...}}` -- `message` 1-500 characters; `session_id` (32 hex
characters), `timezone` and `context` optional; an unknown or missing session starts a new conversation. Response:

```json
{"session_id": "...", "reply": "Here is your route: 30.6 km, ...", "suggestions": ["Alternatives", "..."],
 "plan": { "...": "a RoutePlanResponse, only when there is one to draw" }, "focus_rank": null,
 "intent": "plan", "awaiting": null}
```

`context` is what the web form holds -- `{"request": <a plan request>, "plan": <a ready plan response>}`,
either part optional. The chat takes `request` as the trip "make it shorter" refers to and `plan` as the
route on screen ([chat.md](chat.md#the-form-and-the-chat)); each part is validated like `POST
/v1/route/plan` and its response, and a part that does not pass is ignored (never a `422`).

`suggestions` are quick answers; `plan` is present when the turn produced (or re-showed) a route,
`request` is then the request it was planned from (`origin`, `destination`, `via`, `constraints` ...,
as `POST /v1/route/plan` takes it -- a place the person chose is a `{lon, lat}`) so a client can fill
its form, and `focus_rank` names the alternative that was asked for; `awaiting` is `place_choice` (an ambiguous place
waits for an answer) or `guided` (a question of the step-by-step dialogue is open). `503` when
`CHAT_ENABLED` is off; `/v1/capabilities` has `"chat": bool`. A message that arrives while the
conversation's previous message is still being planned **replaces** it: the earlier plan is cancelled and
its request answers `409` (`a newer message in this conversation replaced this one`) -- a person who
corrects themselves does not wait behind a plan they no longer want. A client that disconnects stops its
turn.

## POST /v1/chat/summaries

Titles for the conversations a client already knows. Body: `{"session_ids": ["<32 hex>", ...]}` (at most
30). Response: `{"sessions": [{"session_id": "...", "title": "from Braunschweig to Goslar", "updated":
1760950000.0, "messages": 4}]}`, most recently used first. `title` is the first thing that was asked,
shortened to 60 characters; `updated` is seconds since the epoch. An id the service does not know
(never existed, expired, dropped, deleted) is left out, so a client can drop it from its own list. The
service only answers for the ids it is given: there is no way to list conversations nobody named.
`422` for a malformed id or more than 30; `503` when `CHAT_ENABLED` is off.

## DELETE /v1/chat/{session_id}

Forgets a conversation: its messages, its last route and (with `CHAT_STORE_PATH`) its file entry. `204`
when it was deleted, `404` when it is unknown or the id is malformed, `503` when the chat is off.

## GET /v1/chat/{session_id}

A conversation as it stands, for a client that was closed and opened again: `{"session_id": "...",
"messages": [{"role": "user" | "bot", "text": "..."}], "suggestions": [...], "plan": {...} | null,
"focus_rank": null, "request": {...} | null, "awaiting": null}`. A question that is still open is the last `bot` message.
`404` when the session is unknown (never existed, expired after `CHAT_SESSION_DAYS`, dropped for newer
ones, or the service keeps sessions in memory and was restarted); `503` when `CHAT_ENABLED` is off.
Sessions survive restarts only with `CHAT_STORE_PATH` ([chat.md](chat.md#conversations-that-survive-a-restart)).

`/v1/capabilities` also has `"optimizer": bool` -- true when a configured engine can return many routes
with road tags (BRouter); the web form shows its Optimize tab only then ([optimizer.md](optimizer.md)).

## BRouter map tiles

For the *Map data missing* card ([providers.md](providers.md#brouter-map-coverage)). All three
answer `503` unless `BROUTER_SEGMENTS_DIR` names a writable tile folder.

- `GET /v1/routing/segments?names=W5_N50,E10_N50` -- `{"segments": [{"name", "present",
  "size_bytes"}], "free_bytes"}`; the size is the source's `Content-Length` (null if it could not
  be asked). Nothing is downloaded.
- `POST /v1/routing/segments/download` `{"segments": ["W5_N50"]}` (1-4 well-formed tile names,
  else `422`) -> `202` with a job `{"id", "state": "running|done|failed", "segments": [{"name",
  "state": "pending|downloading|done|present|failed", "bytes", "total_bytes", "error"}], "error"}`;
  `409` while another download runs.
- `GET /v1/routing/segments/download/{id}` -- the job (`404` if unknown).

## Points of interest

Details and sources: [pois.md](pois.md). All three answer `503` when `POI_ENABLED`
is off and `502` when the map-data service could not be reached.

### `GET /v1/pois/categories`

`[{"key": "viewpoint", "label": "Viewpoints", "kind": "sight"}, ...]`

### `POST /v1/pois/along-route`

```json
{"coordinates": [[10.70, 47.56], [10.73, 47.56]], "categories": ["viewpoint", "historic"],
 "buffer_m": 1500, "limit_per_category": 40}
```

`coordinates` are `[lon, lat]` pairs (2..20000; a candidate's `geometry_geojson.coordinates`);
`categories` defaults to all; `buffer_m` to `POI_DEFAULT_BUFFER_M`, capped at
`POI_MAX_BUFFER_M` (services: 500 m regardless). Response:

```json
{"pois": [{"id": "way/123", "name": "Schloss Neuschwanstein", "category": "historic",
           "kind": "sight", "lon": 10.749, "lat": 47.557, "wikidata": "Q4152",
           "wikipedia": "de:Schloss Neuschwanstein", "website": null, "opening_hours": null,
           "fame": 93, "distance_from_route_m": 410.2, "along_route_km": 12.7}],
 "truncated": false, "fame_status": "ok", "timezone": "Europe/Berlin"}
```

Best-known first (`fame` = Wikidata sitelinks; `null` = not measured, never "obscure"),
then the rest by distance. `fame_status`: `ok`, `partial`, `unavailable` (Wikidata
unreachable: unranked), `skipped` (nothing to look up). `timezone` is the IANA zone at the middle of
the route (what OSM opening hours are written in; `null` when not looked up, see
[pois.md](pois.md#opening-hours)); `in-bbox` never has one.

### `GET /v1/pois/in-bbox`

`?bbox=min_lon,min_lat,max_lon,max_lat[&categories=a,b][&limit_per_category=n]` -- the
same, for a map view; at most 0.5 degrees wide (`422` otherwise).

### `GET /v1/pois/info`

`?wikidata=Q4152&wikipedia=de:Title&osm_id=way/123&website=https://...&lang=de` (all
optional, validated) -- `title`, `description`, `extract` (Wikipedia opening),
`language`, `thumbnail_url`, `sitelinks` and `links` (`wikipedia`, `wikivoyage`,
`commons`, `wikidata`, `osm`, `website`) plus `attribution`. Best effort: parts the
open services cannot deliver right now are left out.

### Plan responses with `poi_stops`

`poi_stops` (the POIs the route was sent past, same shape as above) and
`poi_stops_status` (`ok`, `none_found`, `unavailable`, `unsupported`, `dropped`);
both `null` when no stops were requested.

## GET /healthz

Liveness probe for the API process itself: `{"status": "ok"}`. It does not
probe the routing or geocoding providers (use `/readyz` for that); restarting
the process would not fix a dead upstream, so this is what container
healthchecks should call.

## GET /readyz

Readiness (issue #25): *can this instance plan right now?* It probes every
configured routing engine, the geocoder, the artifact store, the weather
service, the sight lookup (`overpass`, kind `pois`) and, when a database is
configured, the database.

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

- The **sight lookup** (`overpass`, `kind: "pois"`) is optional like the weather: it probes the status
  endpoint of each configured Overpass instance (`.../api/status`, a cheap request that answers while
  queries queue up) with a 4 s budget per instance. `ok`: the first instance answers; `degraded`: only a
  later one does, or it answers with an error code (`HTTP 502`); `unavailable`: none answers. An outage
  degrades the instance (`200`), it never makes it unready: plans work without sights. The public
  instances are often overloaded, so this line is the one most likely to be amber.
- The web page shows these as a dot next to the title (below).
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
| The client closed the connection while the plan was made | `499` (nobody receives it) |
| A chat message replaced by a newer one in the same conversation | `409` |

The workflow guarantees a terminal status on every path, so a well-formed
request should never produce an unhandled `500` from planning logic itself.
