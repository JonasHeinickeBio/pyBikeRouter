# Backend comparison: ORS vs BRouter (real-world test)

Measured 2026-09-18 with the repo's own adapters (`OpenRouteServiceAdapter`,
`BRouterAdapter`) so profile maps, normalisation and error semantics are
identical to production traffic:

- **ORS**: public `api.openrouteservice.org` (free key, cycling profiles).
- **BRouter**: `ghcr.io/abrensch/brouter:latest` via `docker compose
  --profile brouter up`, segment `E10_N50.rd5` + pinned `lookups.dat` (v11).
- 3 real cases x all 8 bike types. `dev med/p95` = median / 95th-percentile
  distance from each engine's route (densified) to the other engine's
  polyline; `shared@50m` = share of sampled points within 50 m of the other
  route, i.e. how much of the two routes is literally the same street.

Reproduce with `poetry run python scripts/compare_backends.py` (needs the
ORS key in `.env` and the BRouter container from `docker/brouter`).

## Results

### Braunschweig Hbf -> Wolfenbüttel (~13 km, flat)

| bike | ORS profile -> km/min | BRouter profile -> km/min | Δdist | shared@50m |
| --- | --- | --- | --- | --- |
| road | cycling-road 14.2/37.2 | fastbike 12.9/37.8 | -8.5% | 35% |
| gravel | cycling-regular 12.7/43.5 | custom_gravel-v1 14.2/33.0 | +11.7% | 39% |
| touring | cycling-regular 12.7/43.5 | custom_touring-v1 13.0/37.6 | +2.2% | 55% |
| mountain | cycling-mountain 13.0/43.6 | mtb 12.6/37.1 | -2.6% | 54% |
| city | cycling-regular 12.7/43.5 | trekking 12.7/36.8 | -0.1% | 61% |
| ebike | cycling-electric 12.9/35.8 | fastbike 12.9/37.8 | +0.5% | 89% |
| commuter | cycling-regular 12.7/43.5 | fastbike-verylowtraffic 12.9/37.7 | +1.8% | 93% |
| recumbent | cycling-regular 12.7/43.5 | vm-forum-liegerad-schnell 14.7/33.0 | +15.9% | 14% |

### Bad Harzburg -> Braunlage (~24 km, ~600 m climb, Harz)

| bike | ORS profile -> km/min/asc | BRouter profile -> km/min/asc | Δdist | shared@50m |
| --- | --- | --- | --- | --- |
| road | cycling-road 28.3/131/911 | fastbike 23.7/135/617 | -16.2% | 70% |
| gravel | cycling-regular 41.6/157/1291 | custom_gravel-v1 35.2/146/1032 | -15.4% | 3% |
| touring | cycling-regular 41.6/157/1291 | custom_touring-v1 22.7/132/617 | -45.3% | 8% |
| mountain | cycling-mountain 23.6/93/3502 | mtb 23.7/149/754 | +0.3% | 35% |
| city | cycling-regular 41.6/157/1291 | trekking 23.7/135/619 | -42.9% | 3% |
| ebike | cycling-electric 26.2/85/927 | fastbike 23.7/135/617 | -9.6% | 9% |
| commuter | cycling-regular 41.6/157/1291 | fastbike-verylowtraffic 23.7/135/617 | -42.9% | 4% |
| recumbent | cycling-regular 41.6/157/1291 | vm-forum-liegerad-schnell 22.7/99/617 | -45.3% | 8% |

(The 1.4 km city-hop case agreed everywhere: 78-92% shared@50m, |Δdist| < 11%.)

## Findings

1. **Profile collapse is real.** 8 bike types -> 4 ORS profiles, 7 BRouter
   profiles. In the Harz case five bike types share one identical 41.6 km
   ORS route, while BRouter gives each a genuinely different route
   (22.7-35.2 km). ORS `cycling-regular` answers steep requests with a long
   valley detour that riders of touring/commuter bikes did not ask for;
   BRouter's per-type cost models go direct.
2. **ORS models things BRouter cannot.** `cycling-electric` produces a
   distinct assisted route and roughly half the duration (85 vs 135 min in
   the Harz). BRouter has no e-assist term, so `ebike` = `fastbike` there.
   Same for ORS extras (isochrones, matrix, surface/track summaries).
3. **Durations are not comparable across engines.** BRouter mtb is ~1.6x
   slower than ORS cycling-mountain on the same climb (cost-model penalty vs
   speed model); ORS ascent values inflate (3502 m "ascent" on a 600 m
   mountain road -- ORS sums local bumps). Never let a score mix raw
   durations from both engines without normalising per engine.
4. **Where they agree, they really agree.** ebike-vs-fastbike and
   commuter-vs-fastbike are near-identical routes (89-93% shared);
   short urban trips agree 78-92%. The disagreements are semantic (which
   "kind" of route a bike type means), not random noise -- which makes
   cross-engine agreement a usable confidence signal.
5. **Operations differ**: ORS public = quota + no offline, zero maintenance;
   BRouter = local, no quota, per 5° tile offline (~125 MB segment),
   maintenance = keep `lookups.dat` and segments version-matched (the image's
   v10 lookups rejected the v11 segments; we pin our own via a compose mount).

## Using both engines together

Current wiring is single-select (`build_routing_provider`, no fallback -- a
deliberate issue-#1 scope decision). The graph already accommodates more:
`nodes/route.py` writes into a **list** `state["candidates"]` and the scoring
node ranks lists of candidates. Recommended increments:

1. **Engine-per-bike-type policy (config, near-zero code).** A
   `PROVIDER_POLICY: dict[str, str]` in `config.py` (e.g. `ebike -> ors`,
   `recumbent/commuter/gravel -> brouter`, fallback `ors`) applied in
   `build_routing_provider`. Deterministic, keeps single-candidate flow,
   plays to each engine's finding above. This is the smallest honest step.
2. **Fallback chain via a composite provider.** A `FallbackRoutingProvider`
   implementing the `RoutingProvider` protocol: try primary on
   `ProviderUnavailableError`/`ProviderTimeoutError` only (not
   `ProviderNoRouteError`), record a candidate warning "served by backup
   engine, profile semantics differ" -- matching the existing honesty rule
   for unsupported constraints. No graph changes; provenance already flows.
3. **Fan-out for alternatives (real multi-backend).** A variant of the route
   node that gathers `route()` from both engines concurrently (already
   async) and appends all candidates; provenance/profile key makes them
   distinguishable and the scorer picks per user preference. Doubles engine
   requests per plan; normalise durations per engine before scoring
   (finding 3). This is the path to "route options, one per engine".
4. **Agreement as a confidence signal (later).** The `shared@50m` metric
   above is cheap to compute and meaningful (finding 4): two-engine
   agreement could feed `score_breakdown` or flag "only one engine found a
   route" to `validate`.

Not recommended: blind averaging/merging of geometries (creates non-existent
paths), and cross-engine distance *selection* without per-engine
normalisation (finding 3).
