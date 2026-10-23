# OSM surface enrichment (issue #3)

Routing engines report geometry and metrics, not map quality. The
enrichment step decorates `RouteCandidate` metrics with surface/access
evidence from OpenStreetMap so downstream steps can reason about it --
under one inviolable rule: **unknown stays unknown**. Missing or
contradictory tags are never converted into a favorable category.

The pipeline position is fixed: `route_with_provider -> enrich_candidates
-> score_candidates`. The node is a pass-through when no enricher is
configured (`osm_enrichment_enabled=false`, the default), so enrichment can
be switched on per deployment without touching routing or scoring code.

## Architecture

| Piece | Role |
| --- | --- |
| `enrichment/base.py` | `SurfaceEnricher` protocol + `SurfaceSummary` (coverage, unknown/inferred/conflict fractions, highway/access fractions). The stable contract every implementation shares. |
| `enrichment/overpass.py` | Prototype implementation: Overpass `way(around:<buffer>)[highway]` queries around the (decimated) route shape, cached by route-shape hash. The production replacement (PostGIS spatial joins, issue #7) changes this class only -- not the node, not the summary, not the policy. |
| `enrichment/quality.py` | The single data-quality policy: raw tags in, `WayClass` (category + evidence strength) out. |
| `enrichment/geometry.py` | Length-weighted attribution of way classes onto the route polyline. |
| `nodes/enrich.py` | Graph node: applies the enricher to each candidate (deduplicated by geometry), writes `metrics.surface_coverage` / `metrics.unknown_surface_fraction` and an `enrichment` provenance record; a failed enrichment leaves metrics untouched (`unknown` stays `None`) and records `"status": "failed"`. |

## Data-quality policy (quality.py)

Evidence order per OSM way:

1. explicit `surface` tag (category from `config.SURFACE_TAXONOMY`;
   colon-suffixed values like `paving_stones:30` normalise to the base);
2. `tracktype` grade (fixed `TRACKTYPE_TAXONOMY`) -- recorded as *inferred*;
3. boolean `paved=yes` -> paved, `native=yes` -> natural_soft -- inferred.
   `paved=no`/`native=no` say only what a way is *not* and stay unknown.

Contradictory combinations (`paved=yes` + `native=yes`, `paved=yes` +
soft surface, `paved=no` + hard surface, `native=yes` + hard surface) are
**conflicts** and counted as unknown, never resolved by guesswork.
`compacted` never conflicts -- compacted gravel genuinely straddles hard
and loose in OSM usage. Values outside the taxonomy are unknown.

Categories: `paved`, `masonry` (hard/bound), `compacted`, `loose`
(unbound firm), `natural_soft` (soft natural).

## Summary semantics

`SurfaceSummary` fractions are length-weighted shares of the route's
geometric length; `coverage.values()` sums with `unknown_fraction` to 1.
`coverage` deliberately has no "unknown" key -- unknown is tracked only by
`unknown_fraction`, so it can never masquerade as a favorable category.
`access` tags are reported verbatim in `access_fractions` and never
influence the surface category.

## Scoring and calibration interaction

Enrichment output feeds the surface fit of the scorer (issue #23):
`surface_coverage` is consumed through this same unknown-is-unknown policy
(re-based onto the known length, never counting `unknown_fraction` for or
against a route) -- see
[scoring-and-exports.md](scoring-and-exports.md#surface-preferences-issue-23).
The component is active (`SURFACE_WEIGHT = 1.0`, from a calibration run) for requests that state
surface preferences, and it does not need enrichment: without it the scorer reads BRouter's own surface
shares (coarser: paved / unpaved / cobbles). Enrichment, when it is on, has the finer categories and is
used instead; it is also the evidence base for the calibration harness in `benchmarks/`.

## Configuration

`OSM_ENRICHMENT_ENABLED` (default false -- public Overpass instances
cannot carry production load), `OVERPASS_BASE_URL`, `OVERPASS_TIMEOUT_S`,
`OVERPASS_MAX_RETRIES`, `OVERPASS_BUFFER_M` (default 25 m),
`OVERPASS_CACHE_TTL_S`. Live tests: `tests/live/test_live_overpass.py`
(`pytest -m live`).

**What it costs.** The query asks for every way within `OVERPASS_BUFFER_M` of the whole route, so it
grows with the route's length. Measured against a *local* Overpass holding Lower Saxony
([self-hosted.md](self-hosted.md#self-hosted-overpass-the-sight-search)): short routes (1-14 km) answered in
seconds, a 25-58 km route in a minute or two, and a 66-70 km route in **4-7 minutes** -- three of those
timed out at the 400 s used for a calibration run. Keep `OVERPASS_TIMEOUT_S` generous (it is 20 s by
default) and the enrichment off for interactive use on long routes; the scorer works without it
(BRouter's own shares), as it did in the calibration runs that could not be enriched.
