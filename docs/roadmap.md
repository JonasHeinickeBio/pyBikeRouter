# Roadmap / planned work

Deliberate boundaries of this milestone, and the follow-up work each one
anticipates. The seams (protocols, config tables, injectable dependencies)
already exist in the code; the items below are the implementations and
decisions still open.

## Routing engines

- **Valhalla adapter** -- done (issue #2): full adapter against a
  self-hosted Valhalla server, all bike types on the single `bicycle`
  costing, no-path vs infrastructure classification, and
  `routing_provider="all"` polling all three engines in parallel. Open
  follow-ups: a compose profile for serving Valhalla alongside the app and
  a real per-bike-type costing once Valhalla offers one (e-assist).
- **BRouter adapter** -- done (issue #1): full adapter, local compose
  service, versioned custom profiles for gravel/touring. Open follow-ups:
  serving the profile-backed engine beyond local dev (production hosting)
  and per-request profile overrides instead of the config-table mapping.
- **Multi-candidate scoring and ranked alternatives** -- done (issue #24):
  the score node ranks every candidate (`rank`, facts-only `rank_rationale`),
  recognises near-identical routes across engines (discrete Frechet distance
  within `ALTERNATIVE_DEDUP_THRESHOLD_M`) and, on request
  (`max_alternatives`), returns only N distinct alternatives
  ([api.md](api.md#alternatives)). Open follow-ups: tuning the dedup
  threshold on real multi-engine outputs, asking a single engine for its own
  alternatives (ORS `alternative_routes`, Valhalla `alternates`), and
  exporting every alternative rather than only rank 1.

## Scoring and map-metadata quality

- **OSM surface enrichment** -- done (issue #3): `SurfaceEnricher`
  protocol, Overpass prototype, the unknown-is-unknown data-quality
  policy, and the `enrich_candidates` graph node
  ([enrichment.md](enrichment.md)). Open follow-ups: production-scale
  PostGIS enrichment (issue #7) and a compose profile that serves it.
- **Score calibration** -- done (issue #4): curated benchmark set
  (`benchmarks/core-v1.json`), evaluation harness and weight-sensitivity
  report (`calibration.py`, `scripts/calibrate.py`, live tests). The
  0.65/0.35 weights stay as an evidence-free prior until a calibration
  run justifies a change. Open follow-ups: more cases (different regions,
  bike types), and surface-profile cases once enrichment is enabled by
  default.
- **Surface/traffic scoring** -- traffic half done (issue #23): the scorer reads `avoid_high_traffic_roads`
  (on by default) through the engine's `traffic_index`, with `TRAFFIC_WEIGHT = 0.5` chosen from a
  calibration run (all-or-nothing over the candidate set, a 50 % detour cap, the alternative-profile
  margin; four `-quiet` benchmark cases, the calibration harness sweeps the weight). See
  [scoring-and-exports.md](scoring-and-exports.md#traffic-issue-23) for the evidence and its limits (one
  region, BRouter only). Surface half done too: `prefer_surfaces`/`avoid_surfaces` resolve to categories and
  a known-length-based `surface_fit` is computed from the OSM enrichment or, without it, from the
  engine's own surface shares, scored with `SURFACE_WEIGHT = 1.0` chosen from a calibration run
  ([evidence](scoring-and-exports.md#surface-weight-evidence)). Still open: other regions for both
  weights, a calibration run with OSM enrichment (finer categories, needs a stable Overpass), and traffic
  and surface from engines that give no road tags.
- **`return_to_origin`** -- done: loop requests synthesize two way-points
  around the origin and route back to the start (`loop_direction` picks
  handedness; caller-supplied `via` is honored verbatim). Provider-native
  round trips were rejected: only ORS has one and it is standalone-only,
  so way-points keep the routing engines symmetric. The loop is sized against a real route (the road distance
  lands within about 10 % of `target_distance_km`; [api.md](api.md#request)); open: the same for loops with sight stops.

## LLM integration

- **LLM parser** -- done (issue #30, opt-in): free text in, the same
  structured shape as the API request out -- never coordinates, geometry, or
  metrics. Anthropic structured outputs behind the `llm` extra, with a
  labelled benchmark; see [llm-parser.md](llm-parser.md). Open: running the
  benchmark against a live model in CI, and more languages in the benchmark.
- **Reshaping by dragging** -- done in the web page: drag the active route to a new place and it is
  planned again through it ([web-ui.md](web-ui.md#drag-the-route-to-reshape-it)). Dragging a place marker (A, B, a
  stop) moves that place and plans again, with touch too. A long press puts the route handle on a
  touch screen (not tried on a real phone), and a dashed preview line shows the stops the new one sits
  between while dragging. Open: previewing the real route (it is only planned on release).
- **Route colours** -- done for BRouter: the active route on the map coloured by surface or by traffic
  exposure, with a legend and the longest unpaved/busy stretch ([web-ui.md](web-ui.md#colour-the-route)).
  Also by gradient, from the elevation (any engine that returns it). Open: surface/traffic from other engines
  (they give no tags), and the OSM-enrichment surface (finer
  categories) once Overpass is stable.
- **Turn-by-turn directions** -- done for BRouter: its turn instructions on each route, a directions
  list with a cue sheet in the web page, and the cues in the GeoJSON download
  ([web-ui.md](web-ui.md#directions)). Open: street names (BRouter has none; would need an OSM lookup
  per turn), cues from ORS and Valhalla, and a GPX/FIT form that devices read as turns.
- **Route optimiser** -- done for BRouter: several routes fetched (profiles, its own 2nd/3rd routes,
  a traffic variant), measured, and the best fit for weighted goals selected with the numbers shown
  ([optimizer.md](optimizer.md)). Also: optional sideways detours (`detours`, off by default: on two trips they
  were valid but never beat a profile route). Open: candidates from other engines (they give no road
  tags), detours that are chosen where the road data shows a busy stretch rather than by geometry
  alone, and learning the weights from what people pick (done as far as looking: an opt-in pick log and
  `bike-router route picks` show what riders take, [optimizer.md](optimizer.md#what-people-pick); no weight
  is changed from it until there is enough data to trust).
- **Chat** -- done: a LangGraph conversation (`POST /v1/chat`, web panel, CLI) with several ways to get
  a route, changes, alternatives, sights, and the clarification dialogue (pause/resume with the
  checkpointer), and conversations that survive a restart (`CHAT_STORE_PATH`, SQLite) ([chat.md](chat.md)).
  Done since: a chat benchmark (`bike-router chat eval`: the rules score 87 % on 126 messages) and an opt-in local
  miss log ([chat.md](chat.md#what-it-could-not-read-and-how-well-it-reads)). A model-based intent step for the
  messages the rules give up on is built (`CHAT_INTENT_MODEL`, off by default, `chat eval --intent-model`); open: its
  score against a real model, then whether to recommend switching it on.
  Done since: the chat fills the web form with the request behind each route, and the form tells the chat
  what it holds (a route planned there, an edited request: [chat.md](chat.md#the-form-and-the-chat)), and the web panel has New chat, a list of this
  browser's earlier conversations, and delete.
- **MCP server** -- done: `bike-router mcp serve --api URL` (stdio) gives another agent four facts-only
  tools -- `plan_route`, `explain_route`, `route_weather`, `sights_along_route` -- over a running API
  ([mcp.md](mcp.md)). Prepared for the MCP Registry (`server.json`, PyPI package and workflow; publishing is the owner's step). Open: an HTTP transport with authentication, and trying it with more MCP hosts.
- **Plan lifecycle** -- done: every step of a plan is timed (`?trace=true`, `Server-Timing`, the log, and
  "Where the time went" on the page), a plan nobody waits for any more is cancelled (a closed connection, or
  a newer chat message in the same conversation: measured on a Braunschweig-Hannover gravel plan dropped after
  one second -- 3 BRouter requests ran on afterwards, now 1: the one already started), progress is streamed
  (`POST /v1/route/plan/stream`: the best route is drawn before the forecast and the files are done) and an
  identical request is answered from a short cache ([api.md](api.md#where-the-time-went-the-cache-and-abandoned-requests)).
  Open: check from real traces whether the forecast (about 2.4 s of 9 s on that trip) can run beside the
  road-data step, which needs the ranking to be independent of it.

## Geocoding

- **Pelias confidence re-tuning** -- defaults
  (`GEOCODER_AMBIGUITY_MARGIN=0.05`, `GEOCODER_MIN_CONFIDENCE=0.3`) are
  calibrated to Nominatim's importance distribution; Pelias confidence means
  something different and will over-clarify at the defaults. See
  [geocoding.md](geocoding.md).
- **Per-request boundaries for Pelias** -- currently constructor-level;
  request-scoped use would require extending the `GeocodeProvider` protocol.

## Operations

- **Persistence** -- done (issue #7): PostGIS route history, an
  `ArtifactStore` abstraction (local disk or database), and provenance
  queries and an evaluation dashboard ([persistence.md](persistence.md)), and
  (issue #28) versioned schema migrations (`bike-router db migrate`) and history
  recording from the CLI. Open follow-ups: dashboard depth, count/size
  retention.

- **Shared cache backend** -- done (issue #29): `CACHE_BACKEND=redis` shares
  geocode and Overpass results across instances, fail-open with a circuit
  breaker, JSON-only, reported by `/readyz`, `cache` compose profile
  ([providers.md](providers.md#shared-cache-redis)). Open follow-ups:
  negative caching of not-found geocodes, and metrics on hit rates.
- **Provider health over HTTP** -- done (issue #25): `GET /readyz`
  aggregates cached, time-bounded probes of the routing engines, geocoder,
  artifact store, weather, the sight lookup (Overpass) and database
  ([api.md](api.md#get-readyz)), and the web page shows it as a status dot
  with a list ([web-ui.md](web-ui.md#service-status)). Open follow-ups:
  reporting the shared cache once it exists, and a real Pelias probe if
  Pelias is ever served on its own.
- **Artifact lifecycle** -- done (issue #27): `ArtifactStore` gained
  `list_artifacts`/`delete`, an S3-compatible backend (`ARTIFACT_BACKEND=s3`,
  `s3` dev compose profile) and `bike-router retention prune` expire plans and
  the artifacts only they reference by age, dry run by default
  ([persistence.md](persistence.md#retention)). Open follow-ups: count/size
  quotas, and running the prune on a schedule inside the compose stack.
- **Weather along the route** -- done: `departure_time` plus a forecast per
  candidate from free keyless providers (Open-Meteo, MET Norway as fallback),
  wind resolved against the direction of travel, advisories, and a weather
  card/markers/wind column in the UI ([weather.md](weather.md)). Also: "feels
  like", where and when the rain is along the route, and computed
  sunrise/sunset against arrival, and a comparison of departure times around the
  requested one. Open follow-ups: letting weather influence ranking
  (needs calibration evidence), validating the wind-adjusted travel time against measured rides (it is an
  estimate for the same effort as in still air, see [weather.md](weather.md)). Done since: the forecast
  is in the GPX/GeoJSON files ([scoring-and-exports.md](scoring-and-exports.md)).
- **Points of interest** -- done (issue #55): Overpass search along a route or in
  a map view, fame from Wikidata sitelinks, Wikipedia/Wikivoyage/Commons details,
  a map filter, *add to route*, and routing past the most famous sights
  ([pois.md](pois.md)). Stops for loops are done (searched along the circuit, which is drawn smaller to make room; see
  [pois.md](pois.md#loops)), and so is the opening-hours check in a sight's popup against the time you pass it
  ([pois.md](pois.md#opening-hours)). A self-hosted Overpass is done too (opt-in compose profile
  `self-hosted-pois`, [self-hosted.md](self-hosted.md#self-hosted-overpass-the-sight-search)), since the
  public instances are often at capacity. Open follow-ups: POI-aware scoring
  (needs calibration evidence), using the opening hours when choosing route stops, a fuller reading of the
  hours language (months, sunrise, holidays), pageview counts as a second
  fame signal.
- **Self-hosted ORS stack** -- done (issue #26): the `self-hosted` compose
  profile runs a pinned openrouteservice (bicycle profiles) and a local
  Nominatim from one regional extract, with a bootstrap script
  ([self-hosted.md](self-hosted.md)). Finding: openrouteservice itself has no
  geocoder, so boundary-pinned *Pelias* geocoding is still open (it needs a
  real Pelias deployment, which the `pelias` adapter has never been run
  against) -- as is Pelias confidence re-tuning below.

## Explicit non-goals

- **Safety guarantees.** Explanations stay hedged ("aligned with available
  map metadata"); no scoring or wording will claim a route is safe.
- **LLM-generated geography.** The LLM may parse, ask, and explain. It will
  not produce coordinates, geometry, or metrics -- those come from routing
  engines only.
