# Roadmap / planned work

Deliberate boundaries of this milestone, and the follow-up work each one
anticipates. The seams (protocols, config tables, injectable dependencies)
already exist in the code; the items below are the implementations and
decisions still open.

## Routing engines

- **Valhalla adapter** -- `providers/valhalla.py` is a stub (protocol +
  health probe). Implement `route()` against a Valhalla `route` endpoint,
  map `BikeType` -> Valhalla costing options in `config.py`, and classify
  no-path vs infrastructure failures like the ORS adapter does.
- **BRouter adapter** -- done (issue #1): full adapter, local compose
  service, versioned custom profiles for gravel/touring. Open follow-ups:
  serving the profile-backed engine beyond local dev (production hosting)
  and per-request profile overrides instead of the config-table mapping.
- **Multi-candidate scoring** -- the state, the `score_candidates` node, and
  the scorer already handle N candidates; today one provider yields one.
  With a second engine wired, score across engines and (optionally) return
  ranked alternatives.

## Scoring and map-metadata quality

- **Surface/traffic scoring** -- `prefer_surfaces`/`avoid_surfaces` are
  validated but unscored; `metrics.surface_coverage`/
  `unknown_surface_fraction` are not populated by the ORS adapter yet.
  Needs an enrichment source (e.g. ORS `extra_info` for trail difficulty /
  surface, or per-segment OSM lookups) **and** an explicit data-quality
  policy for how unknowns influence the score (unknown must stay unknown,
  not silently favorable).
- **`return_to_origin`** -- accepted in `RouteConstraints`, not yet acted
  on. Natural implementation: a loop request (destination snapped back to
  origin) or a route-to-route composition, engine-dependent.

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
