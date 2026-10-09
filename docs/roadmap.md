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
- **Surface/traffic scoring** -- surface half built (issue #23):
  `prefer_surfaces`/`avoid_surfaces` resolve to categories, a
  known-length-based `surface_fit` is computed from the enrichment and
  reported in `score_breakdown`, the all-or-nothing activation rule keeps
  unenriched candidates comparable, and the calibration harness sweeps a
  surface-weight grid over four new preference cases. It ships **inactive**
  (`SURFACE_WEIGHT = 0.0`): switching it on needs a calibration run with
  engines and enrichment ([how](scoring-and-exports.md#enabling-the-surface-component)).
  Still open: that calibration run and the weight it justifies, and traffic
  scoring (highway shares onto `RouteMetrics`, plus cases that exercise
  them).
- **`return_to_origin`** -- done: loop requests synthesize two way-points
  around the origin and route back to the start (`loop_direction` picks
  handedness; caller-supplied `via` is honored verbatim). Provider-native
  round trips were rejected: only ORS has one and it is standalone-only,
  so way-points keep the routing engines symmetric.

## LLM integration

- **LLM parser** -- done (issue #30, opt-in): free text in, the same
  structured shape as the API request out -- never coordinates, geometry, or
  metrics. Anthropic structured outputs behind the `llm` extra, with a
  labelled benchmark; see [llm-parser.md](llm-parser.md). Open: running the
  benchmark against a live model in CI, and more languages in the benchmark.
- **Clarification dialogue** -- `awaiting_clarification` responses already
  carry candidate lists; a conversational layer could ask about them and
  resubmit with a chosen coordinate. The graph supports resumption via
  `checkpointer` (LangGraph checkpointers) -- unexercised so far.

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
  artifact store and database ([api.md](api.md#get-readyz)). Open
  follow-ups: a UI status badge, reporting the shared cache once it exists,
  and a real Pelias probe if Pelias is ever served on its own.
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
  (needs calibration evidence), wind-adjusted travel times, and exporting the
  forecast in the GPX/GeoJSON files.
- **Points of interest** -- done (issue #55): Overpass search along a route or in
  a map view, fame from Wikidata sitelinks, Wikipedia/Wikivoyage/Commons details,
  a map filter, *add to route*, and routing past the most famous sights
  ([pois.md](pois.md)). Open follow-ups: stops for loops, POI-aware scoring
  (needs calibration evidence), opening-hours awareness against the arrival time,
  a self-hosted Overpass in the compose stack, and pageview counts as a second
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
