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
[OSM enricher](enrichment.md)) **re-based onto the known length**: coverage
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

#### Enabling the surface component

`SURFACE_WEIGHT` (`scoring/basic.py`) ships at **0.0**: the component is
computed and reported but does not move scores, because no calibration run
has yet justified a value (the project rule: weights come from evidence).
To produce that evidence:

1. start the engines you want compared and set `OSM_ENRICHMENT_ENABLED=true`;
2. `poetry run python scripts/calibrate.py --out calibration-report.json`;
3. read, per surface-preference case (`*-prefer-*`, `*-avoid-*`,
   `*-hard-surfaces`): the `[surface evidence: n/m candidates]` count (an
   inactive component needs evidence on *every* candidate), and whether the
   ranking is `surface-weight-sensitive` across `surface=0.00 .. 0.30`
   (`SURFACE_WEIGHT_GRID`);
4. if rankings change only where surface evidence is real and the change
   matches local judgement, set `SURFACE_WEIGHT` to the smallest weight that
   achieves it and record the report in the PR; if nothing changes, more
   cases are the answer, not a bigger weight.

Traffic / road-class quality (`avoid_high_traffic_roads`) is still not
scored: highway shares exist in the enrichment summary but are not yet on
`RouteMetrics`, and no benchmark case exercises them.

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
