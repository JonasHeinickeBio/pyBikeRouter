# Architecture

## Design principle

The service separates **facts** from **language**. Everything geographic --
coordinates, geometry, distance, ascent, suitability -- comes from
OSM-derived routing engines and is carried in typed, provider-neutral models.
An LLM, where used, only touches free text: parsing a request, asking a
clarification question, explaining a route. It never invents a coordinate, a
geometry, or a metric.

The second principle is **fail loudly and specifically**. Every path through
the workflow ends in an explicit terminal `status`; a missing route
(`no_route`) is never conflated with an infrastructure failure
(`provider_failure`), and an ambiguous place is never silently resolved to a
guess.

## Layers

```
FastAPI (api.py)
   |  validates RoutePlanAPIRequest, invokes the graph, shapes RoutePlanResponse
   v
LangGraph workflow (graph.py)
   |  six nodes, conditional edges, always terminates with a status
   v
Nodes (nodes/*)              one module per step, provider-agnostic
   |
   +-- providers/*            RoutingProvider / GeocodeProvider adapters
   +-- scoring/*              deterministic candidate scoring
   +-- exporters/*            GeoJSON / GPX serialization
```

Dependencies point downward only. Nodes depend on the provider **protocols**
in `providers/base.py`, never on a concrete adapter, so ORS can be swapped
for Valhalla or BRouter without touching orchestration. Provider-specific
strings (ORS profile names, supported `avoid_features`) live in `config.py`
and the adapter, never in nodes or scoring.

## The workflow

```
START -> parse_request
      -> validate_request      -> (invalid) -> END
      -> geocode_locations     -> (awaiting_clarification | provider_failure) -> END
      -> route_with_provider   -> (no_route | provider_failure) -> END
      -> score_candidates      -> (no_route) -> END
      -> explain_and_export    -> END
```

`graph.build_graph` wires the six nodes and their conditional edges. Each
edge inspects `status` and either advances or ends the run -- there is no
path that falls off the end without a status. The graph never calls a routing
provider unless validation **and** geocoding have both succeeded.

### Nodes

| Node | Module | Responsibility |
| --- | --- | --- |
| `parse_request` | `nodes/parse.py` | Reshape the already-validated API payload into state. Free-text input goes through an injectable `llm_parser` (see below). |
| `validate_request` | `nodes/validate.py` | Re-validate constraints with Pydantic (defense in depth for direct graph calls). Malformed constraints -> `invalid`; a missing origin/destination -> `awaiting_clarification`. |
| `geocode_locations` | `nodes/geocode.py` | Resolve free-text places to coordinates via a `GeocodeProvider`. Direct coordinates bypass it. Ambiguity/emptiness -> clarification, never a guess. |
| `route_with_provider` | `nodes/route.py` | Build an engine-neutral `RoutingRequest`, call the `RoutingProvider`. `no_route` vs `provider_failure` distinguished here. |
| `score_candidates` | `nodes/score.py` | Score and rank candidates deterministically; select the best. |
| `explain_and_export` | `nodes/export.py` | Facts-only explanation + GeoJSON/GPX artifacts written to `EXPORT_DIR`. |

### The parse step and the LLM boundary

`parse_request` is deterministic by default: `api.py` has already validated a
`RoutePlanAPIRequest` before the graph runs, so the node mostly reshapes that
payload. Free-text (`{"text": "..."}`) input requires an `llm_parser`
callable, injected at graph build time. It is a plain
`Callable[[str], dict]` so the extraction step can be swapped or mocked
without touching graph wiring -- and its contract is to return the *same
structured shape* the API would, never coordinates or geometry. No LLM parser
is wired up in this milestone; free-text requests without one return
`invalid` with code `nl_parsing_unavailable`.

## State contract

`RouteAgentState` (`state.py`) is a `TypedDict`. Every field is
JSON-serializable apart from `messages` (LangChain messages for the
LLM-assisted parse step and any future clarification dialogue). Domain
objects -- `Coordinate`, `RouteCandidate`, ... -- are stored as
`.model_dump(mode="json")` dicts and rehydrated with `.model_validate(...)`
inside nodes; **state never holds a Pydantic instance**. This keeps the state
checkpoint-friendly and keeps nodes honest about what crosses between them.

Nodes return **partial updates** (only the keys they change), not the whole
state, and must not mutate the `state` mapping they receive.

## Terminal statuses

Defined as a `Literal` in both `state.py` and `models.py`:

| Status | Meaning | Set by |
| --- | --- | --- |
| `in_progress` | transient, not terminal | parse/geocode/route/score on the happy path |
| `invalid` | request failed validation, or free text with no parser | parse, validate |
| `awaiting_clarification` | a place is missing, ambiguous, or unresolved; `clarification` lists candidates | validate, geocode |
| `provider_failure` | timeout / rate limit / unavailable / bad response | geocode, route |
| `no_route` | no path exists, or nothing to score | route, score, export |
| `ready` | a scored route with artifacts | export |

`api.py` maps the final state to a `RoutePlanResponse`. It only populates
`route`/`explanation`/`artifacts` when status is `ready`; the raw provider
payload is stripped from the response (`raw_provider_response=None`) so
internal provider detail never leaks to callers.

## Error model

`errors.py` defines two structured hierarchies:

- `ProviderError` (+ `Timeout`, `RateLimit`, `Unavailable`, `BadResponse`,
  `NoRoute`) -- raised by adapters, carrying `code`, `provider`, `detail`.
- `GeocodingError` (+ `NotFound`, `Ambiguous`) -- carrying `code`, `query`.

Adapters raise these; **nodes catch them** and turn them into structured
`errors` entries via `.to_dict()`. They must never surface to callers as
uncaught exceptions. `ProviderNoRouteError` (no path exists) is deliberately
distinct from the other `ProviderError`s (infrastructure failure) so the
graph can route them to different terminal statuses.

## Tests

Unit tests mock all HTTP with `respx`; graph-branch tests drive the compiled
graph with fake providers; live tests hit real services behind a `live`
marker. See [testing.md](testing.md).
