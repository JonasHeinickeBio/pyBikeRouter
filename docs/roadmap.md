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
- **Multi-candidate scoring** -- the state, the `score_candidates` node,
  and the scorer handle N candidates, and `routing_provider="all"` now
  feeds them from ors + brouter + valhalla in parallel. Still open:
  (optionally) returning ranked alternatives instead of a single winner.

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
- **Surface/traffic scoring** -- `prefer_surfaces`/`avoid_surfaces` are
  still validated but unscored. The data question is answered (issue #3
  populates `surface_coverage` under an explicit unknown-is-unknown
  policy); what remains is a surface component in the score with weights
  justified by calibration evidence from `scripts/calibrate.py`, not
  chosen arbitrarily.
- **`return_to_origin`** -- done: loop requests synthesize two way-points
  around the origin and route back to the start (`loop_direction` picks
  handedness; caller-supplied `via` is honored verbatim). Provider-native
  round trips were rejected: only ORS has one and it is standalone-only,
  so way-points keep the routing engines symmetric.

## LLM integration

- **LLM parser** -- `parse_request` accepts an `llm_parser`
  (`Callable[[str], dict]`); none is wired. Contract: free text in, the same
  structured shape as the API request out -- never coordinates, geometry, or
  metrics. Provider choice (model, framework) is open; LangChain messages
  and the `messages` state channel already exist for it.
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

- **Shared cache backend** -- `CacheBackend` protocol exists; a Redis (or
  similar) implementation is needed for multi-instance deployments to share
  geocode results.
- **Provider health over HTTP** -- adapters have `health()`; exposing a
  combined readiness endpoint (distinct from the process-level `/healthz`)
  is open.
- **Artifact lifecycle** -- exports accumulate under `EXPORT_DIR` with no
  cleanup/quota today; needs TTL-based pruning or object storage.
- **Self-hosted ORS stack** -- a compose profile running ORS (+ Pelias)
  locally would remove public-API rate limits and unlock boundary-pinned
  geocoding.

## Explicit non-goals

- **Safety guarantees.** Explanations stay hedged ("aligned with available
  map metadata"); no scoring or wording will claim a route is safe.
- **LLM-generated geography.** The LLM may parse, ask, and explain. It will
  not produce coordinates, geometry, or metrics -- those come from routing
  engines only.
