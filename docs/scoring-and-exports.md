# Scoring, explanation, and exports

## Deterministic scoring

`scoring/basic.py` scores each `RouteCandidate` against the request
constraints. The scorer is pure and deterministic: no LLM, no randomness --
the same candidate and constraints always produce the same score, and the
components are returned alongside it as `score_breakdown` so a score is
always explainable.

```
score = 0.65 * distance_fit + 0.35 * elevation_fit - warning_penalty
```

| Component | Rule |
| --- | --- |
| `distance_fit` | With `target_distance_km` set: `max(0, 1 - abs(distance - target) / target)` -- fully off-target scores 0. Without a target: 1.0. |
| `elevation_fit` | 1.0 unless both `constraints.max_ascent_m` and `metrics.ascent_m` are present; then `max(0, 1 - excess / max_ascent_m)` where `excess` is the ascent above the limit. If ascent data is missing, it stays 1.0 -- and the gap is surfaced as an uncertainty note instead of a fake penalty. |
| `warning_penalty` | `min(0.3, 0.05 * len(candidate.warnings))` -- provider warnings cost 0.05 each, capped so warnings can never dominate the score. |
| `surface_fit` | Reported (not necessarily scored) when surface preferences were stated and the candidate has OSM surface evidence -- see [Surface preferences](#surface-preferences-issue-23). |
| `traffic_fit` | `1 - traffic_index / 0.4` (floor 0) from the engine's traffic estimate, when the request avoids high-traffic roads (the default) and the candidate has one; scored with `TRAFFIC_WEIGHT` -- see [Traffic](#traffic-issue-23). |

`score_candidates` (the graph node) scores every candidate, writes
`score`/`score_breakdown` back onto each, then ranks them and selects rank 1
as `selected_candidate` (with several engines configured, that is the
multi-engine comparison).

### Ranking and alternatives (issue #24)

`scoring/alternatives.py` orders candidates by score (ties: shorter
distance, then provider/profile; unscored last) and numbers them from 1.
Each candidate gets a `rank_rationale` assembled purely from metric deltas
against rank 1 -- e.g. `rank 2: score 0.80 (0.10 below rank 1); 1.2 km
longer; 40 m less ascent` -- so it states how routes differ and never
claims one is safer.

Engines often return the same street-level route, snapped a few metres
apart, so *near-identical* routes are recognised: both lines are resampled
to 64 points by arc length (vertex density does not matter) and compared by
discrete Frechet distance; within `ALTERNATIVE_DEDUP_THRESHOLD_M` (50 m) they
are one alternative, and the better-scored member is kept, which is why rank
1 is never a duplicate. Geometries that cannot be read are never merged.
Without `max_alternatives` every candidate is still returned (duplicates
annotated via `duplicate_of`/`duplicates`); with it the duplicates are
dropped and the list capped. The 50 m default is a prior, not a calibrated
value: tune it from real multi-engine outputs ([roadmap.md](roadmap.md)).

### Surface preferences (issue #23)

`prefer_surfaces` / `avoid_surfaces` accept the surface categories
(`paved`, `masonry`, `compacted`, `loose`, `natural_soft`) and raw OSM
`surface` values (`asphalt`, `cobblestone`, `paving_stones:30`, ...), which
resolve to a category through `SURFACE_TAXONOMY`. Tokens that resolve to
nothing (a typo, an engine word like `unpaved`) are accepted but unscored --
unknown stays unknown. Preferring and avoiding the same *category* (e.g.
`asphalt` and `concrete`) is rejected as contradictory.

`surface_fit` is computed from `metrics.surface_coverage` (populated by the
[OSM enricher](enrichment.md), which is off by default) or, when there is none, from the engine's own
`metrics.engine_surface_shares` (BRouter's way tags; see
[the engine evidence](#evidence-from-the-engines-own-surface-shares) below) **re-based onto the known length**: coverage
shares are of the whole route and sum with `unknown_surface_fraction` to 1,
so an unmapped stretch is neither credited nor held against the route; it
lowers only the *evidence* (`surface_known_share`). `surface_fit` is the mean
of the terms that were stated: the share of known length on preferred
categories, and one minus the share on avoided categories. Both land in
`score_breakdown` whenever they can be computed.

The component enters the score only when it is **active**: the surface
weight is above zero, and *every* candidate in the comparison has
`surface_fit` with at least `MIN_KNOWN_SURFACE_SHARE` (0.5) of its length
known. All-or-nothing over the set, so an enriched and an unenriched
candidate are never ranked by different formulas (which would let missing
data win or lose by accident). When active:

```
score = (0.65 * distance_fit + 0.35 * elevation_fit + SURFACE_WEIGHT * surface_fit)
        / (1 + SURFACE_WEIGHT) - warning_penalty
```

so scores stay in [0, 1], and a request without surface preferences scores
exactly as before.

#### Evidence from the engine's own surface shares

OSM enrichment is off by default, so on its own the surface term would almost never have had data.
BRouter already tells how much of each route is on which surface (`surface` tags of the ways it used),
reduced to four classes: `paved`, `unpaved`, `cobbles` and `unknown`. When `surface_coverage` is
empty, `surface_fit` is computed from those shares, with the same re-basing onto the known length and
the same all-or-nothing rule over the candidate set (a candidate from an engine without shares -- ORS,
Valhalla -- switches the term off for everyone). The categories map onto the engine's classes like this:

| Category stated | Engine class |
| --- | --- |
| `paved` | `paved` |
| `masonry` (setts, paving stones, cobbles) | `cobbles` |
| `compacted`, `loose`, `natural_soft` | `unpaved` |

The engine's resolution is coarser than the categories, which has two consequences, both deliberate:

- Asking for `gravel` (`loose`) is read as asking for the engine's whole `unpaved` class, which also
  holds compacted and soft ground: it can say "more off-road", not "more gravel".
- Preferring one category and avoiding another that share an engine class (prefer `compacted`, avoid
  `loose`) is a question the engine cannot answer, so there is **no fit** -- the term is off for that
  request -- rather than a guess. OSM enrichment, when it ran, has the finer data and is used instead.

`masonry` is named "cobbles" by the engine but also covers flat paving stones; the fit counts it only
when you prefer or avoid `masonry` (or a raw value such as `cobblestone`, `sett`, `paving_stones`).

#### Surface weight: evidence

`SURFACE_WEIGHT` was 0.0 until this run (the project rule: weights come from evidence). Run:
`scripts/calibrate.py --engines brouter` against the live BRouter, no enrichment, on the benchmark's
seven preference cases (four older ones, four added with this change; the control cases are there to
catch a preference pushing a route somewhere it should not go). The ranking is the production one,
including the traffic term and the margin an alternative profile needs, swept over
`SURFACE_WEIGHT_GRID = 0, 0.1, 0.2, 0.3, 0.5, 1.0`:

| Case | Result |
| --- | --- |
| `bs-hannover-gravel-prefer-paved` (gravel bike, paved) | gravel profile (26 % paved) until **~0.72**, then the touring profile (65 % paved, same length). Flips at 0.75-1.0 |
| `hannover-road-prefer-paved` (road bike, paved; control) | commuter (65 % paved, quiet) at 0-0.1, `fastbike` (95 % paved, busy) from 0.2: the stated surface preference outweighs the default traffic preference |
| `goslar-gravel-prefer-paved` (gravel bike, paved) | gravel profile throughout: the touring route is 81 % paved against 50 %, but the gravel route's 28 % unknown length is left out of the share, so the fits are 0.87 against 0.70 and never clear the margin |
| `goslar-brocken-gravel-prefer-gravel`, `bs-okerradweg-prefer-unpaved`, `bs-city-hop-prefer-paved`, `bs-oldtown-avoid-cobble` | no change at any weight |

**1.0** is the shipped weight: the clearest case flips at about 0.72 (found with a finer search on the
measured routes) and 1.0 leaves headroom, while no other case moves between 0.5 and 1.0. It only affects
requests that state surface preferences (the form's "Prefer surfaces" / "Avoid surfaces", a chat "prefer
paved"); everyone else is scored exactly as before. `SURFACE_WEIGHT = 0.0` switches it off.

What this does not show: one region, BRouter only, seven cases, and the engine's coarse classes. The
case that does not flip (`goslar-gravel-prefer-paved`) shows a limit rather than a success: when a lot of
a route's surface is unknown, the fit rests on a smaller share and discriminates less, which is the
unknown-is-unknown policy working as designed. Re-run it for your region:
`BROUTER_BASE_URL=... python scripts/calibrate.py --engines brouter --out report.json`, and read
`surface_sensitivity` and `surface_evidence` per case.

### Traffic (issue #23)

`avoid_high_traffic_roads` (on by default, the "Avoid high-traffic roads" box in the web form) is now
read by the scorer. The input is the engine's own estimate, `metrics.traffic_index` (BRouter's road
tags: motorway/trunk/primary exposure, reduced by cycle tracks and speed limits; 0 = no exposure to cars,
[providers.md](providers.md)):

```
traffic_fit = max(0, 1 - traffic_index / 0.4)         # TRAFFIC_INDEX_AT_ZERO_FIT
score = (0.65 * distance_fit + 0.35 * elevation_fit + TRAFFIC_WEIGHT * traffic_fit) / (1 + TRAFFIC_WEIGHT) - penalty
```

(`TRAFFIC_WEIGHT = 0.5`; with a surface term active, its weight joins the numerator and the divisor.)
The same discipline as the surface term applies:

- **All or nothing over the set.** The term enters the score only when *every* candidate carries an
  index; a set with one candidate from another engine (no index) is scored without it, so no two
  candidates are ever compared on different formulas. An unknown estimate is never "quiet".
- **Off when not asked for.** `avoid_high_traffic_roads: false` leaves the score as it was.
- **No credit for a long detour.** A candidate more than 50 % longer than the shortest one in the set
  gets `traffic_fit = 0` (`traffic_detour_capped` in the breakdown): quietness cannot buy an absurd
  route. Choosing a longer, quieter route on purpose is what the [optimiser](optimizer.md) is for.
- **Alternatives still need a margin.** An alternative profile must lead by `ALTERNATIVE_RANK_MARGIN`
  (0.10) to outrank the route for the bike type, so a small traffic difference never reshuffles the
  ranking.

`traffic_fit` is reported in `score_breakdown` whenever it can be computed; `traffic_weight_applied`
says when it counted. `TRAFFIC_WEIGHT = 0.0` switches the term off.

#### Traffic weight: evidence

Eight real trips around Braunschweig and the Harz (collected 2026-10), planned with BRouter (the candidate sets the service
returns: the bike type's own profile plus the other profiles as alternatives), scored by the production
scorer with and without the term:

| Trip (bike) | Best route today | With traffic (weight 0.5) |
| --- | --- | --- |
| Braunschweig - Goslar (gravel) | gravel (58 km, index 0.04) | unchanged |
| Braunschweig - Wernigerode (gravel) | gravel (76 km, 0.02) | unchanged |
| Braunschweig - Hannover (gravel) | gravel (67 km, 0.01) | unchanged |
| Braunschweig - Hannover (road) | `fastbike` (69.8 km, **0.23**, 34 % busy roads) | **commuter** (66.0 km, 0.08): shorter *and* quieter |
| Braunschweig - Wolfsburg (touring) | touring (27.9 km, 0.02) | unchanged |
| Braunschweig city hop (city) | trekking (1.3 km, 0.00) | unchanged |
| Goslar - Brocken (gravel) | gravel (33.9 km, 0.02) | unchanged |
| Braunschweig - Gifhorn (commuter) | commuter (26.2 km, 0.05) | unchanged |

The same sweep on the benchmark set (13 cases; `scripts/calibrate.py --engines brouter`, the ranking is
the production one, including the margin an alternative profile needs) changes the best route in **two**
cases, both with a large traffic gain and neither with a long detour:

| Case | Weight 0 - 0.25 | Weight 0.5 - 1.0 | Why |
| --- | --- | --- | --- |
| `bs-hannover-road-quiet` (road bike) | `fastbike`, 69.8 km, index 0.23 | commuter, 66.0 km, 0.08 | Shorter *and* quieter; flips between 0.25 and 0.5 |
| `harz-climb` (touring bike, climb) | touring, 26.7 km, index **0.33** | gravel profile, 31.4 km (+18 %), **0.035** | The touring profile's route is busy; flips already at 0.25. The gravel profile's route is on tracks, so it may be rougher than a touring bike likes: the alternative's own profile notes say so |

The other eleven cases do not change at any weight from 0 to 1 -- including the three other `-quiet`
cases (touring, commuter and the gravel control, written to look for a flip) and the city, cobble and
Okerradweg cases. A weight of **0.5** is the
smallest in the grid at which both changes happen and is not at an edge of the range where anything
else starts to move. On the eight trips above alone, with the fit reaching 0 at 0.4, the road-bike trip
is the only change from weight 0.4 upward; with 0.3 or 0.5 as the end of the scale the same trip changes
(from weight 0.3 / 0.5), so the scale of the fit is not what decides it.

What this evidence does **not** show: it is one region (the benchmark overlaps the eight trips), one engine (BRouter) and 13 cases at most. Other
terrain (a corridor with no alternative to the main road), other bike types and engines without an
estimate are untested; the guards above (all-or-nothing, the detour cap, the margin) are there because
of that. The benchmark set has four traffic cases (`-quiet`, `max_traffic_index` 0.15) for the same
purpose; run `scripts/calibrate.py` against your own region and read the `traffic_sensitivity` sweep per
case before trusting the default there. Re-running the numbers above:

```
BROUTER_BASE_URL=http://127.0.0.1:17777 python scripts/calibrate.py --out report.json
```

### Uncertainty notes

`uncertainty_notes(candidate)` reports honest data gaps as text:

- `"surface type is unknown for X% of this route"` when
  `unknown_surface_fraction` is set;
- `"elevation data was not available for this route"` when `ascent_m` is
  `None`.

Missing data is represented as uncertainty, never treated as favorable or
penalized as if it were known.

## Score calibration (issue #4)

The weights above (`0.65/0.35`) are a reasonable prior, not a calibrated
value. `calibration.py` plus `benchmarks/core-v1.json` are the measuring
instrument for revisiting them -- the *measurement*, not an automatic
retuner: production weights change only as a deliberate, evidenced
decision informed by a calibration report.

The benchmark is a small, locally curated set of OD pairs (Braunschweig/
Harz region) whose expectations a rider familiar with the corridor judges
by hand. Each case pins a request plus **plausibility envelopes** -- what
any believable good route through that corridor must look like (distance
band, ascent band, surface profile bounds) -- with a written rationale.
They are deliberately wide envelopes, not golden geometries; the
`harz-climb` case is an inverse judgement (a candidate reporting <250 m
ascent there flags broken elevation data rather than earning a bonus).

Two policies from enrichment carry over into evaluation:

- **unknown is never a failure** -- a check whose data is missing
  (`ascent_m=None`, empty `surface_coverage`, no `unknown_surface_fraction`)
  is reported as *skipped*. A disabled enricher surfaces untested
  judgements; it does not lose the run;
- **judgements constrain, they do not reward** -- expectations bound what
  a good route looks like; passing all of them is plausibility, not
  optimality.

`scripts/calibrate.py` runs every case through all engines
(`routing_provider="all"`, plus enrichment when enabled) and reports, per
case: pass/fail/skip per judgement, the ranking under production weights,
and the ranking under a weight grid (`0.65/0.35`, `0.5/0.5`, `0.8/0.2`,
`0.35/0.65`) plus a surface-weight grid (`0.0` to `0.3`, with per-candidate
surface evidence). A ranking that flips inside those grids is *weight-sensitive*
-- the response is more benchmark cases and scrutiny, not a retuned
constant. `tests/live/test_live_calibration.py` re-checks the envelopes
against live engines; the offline suite validates the schema and evaluation
semantics with synthetic candidates.

## Explanation policy

The `explain_and_export` node builds the explanation string exclusively from
fields on the selected candidate and its score breakdown -- nothing is
invented, and an LLM is not involved. The wording is deliberately hedged:

- selection is described as "better aligned with the requested constraints
  and map metadata", never as "the safest route";
- provider warnings are relayed verbatim;
- uncertainty notes are appended as explicit `Note: ...` sentences;
- the closing sentence states outright that this reflects available map
  metadata, "not a guarantee of safety".

No route from this service is a safety guarantee, and the generated text
never implies one.

## Exports

Both exporters are pure functions over `RouteCandidate`
(`exporters/geojson.py`, `exporters/gpx.py`). The export node writes both to
the artifact store (`EXPORT_DIR` by default, see [persistence.md](persistence.md)) under one `route_id` (uuid4 hex) and the API serves them at
`/v1/routes/{route_id}.{geojson|gpx}`.

### GeoJSON

`to_geojson_str` emits a single GeoJSON `Feature`: the candidate's
`geometry_geojson` as `geometry`, and properties carrying provider, profile,
distance, duration, ascent/descent, score, and warnings -- the same facts the
response body carries, so an exported file is self-describing. Geometry types
other than `LineString`/`MultiLineString` raise `ValueError`, mirroring the
GPX exporter.

### GPX

GPX 1.1 (`http://www.topografix.com/GPX/1/1`) written via ElementTree:

- `<metadata>` carries the route name (the `route_id`) and
  `<extensions>` the `provider` and `provider_profile`;
- one `<trk>` with one `<trkseg>` per geometry line (a `LineString` yields
  one segment, a `MultiLineString` one each);
- `<trkpt lat lon>` with `<ele>` only when the coordinate triple actually
  contains elevation -- 2D geometry exports as 2D points;
- any geometry type other than `LineString`/`MultiLineString` raises
  `ValueError` (surfaced as a structured failure, not a corrupt file).

GPX files are plain XML with no external schema dependency; consumers that
validate against the official schema will find the output conformant for the
fields emitted.
