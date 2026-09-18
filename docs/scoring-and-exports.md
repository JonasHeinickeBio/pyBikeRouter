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

`score_candidates` (the graph node) scores every candidate, writes
`score`/`score_breakdown` back onto each, and selects the maximum as
`selected_candidate`. Today there is exactly one candidate (single provider);
the list structure is deliberate so multi-engine comparison can be added
without changing the node ([roadmap.md](roadmap.md)).

### What is deliberately *not* scored

Surface preferences (`prefer_surfaces`/`avoid_surfaces`) are accepted and
validated but do not influence the score yet -- ORS's GeoJSON response does
not carry per-segment surface coverage in the shape the model anticipates
(`metrics.surface_coverage` stays empty, `unknown_surface_fraction` stays
`None`). Scoring on absent data would be arbitrary; this is tracked in
[roadmap.md](roadmap.md).

### Uncertainty notes

`uncertainty_notes(candidate)` reports honest data gaps as text:

- `"surface type is unknown for X% of this route"` when
  `unknown_surface_fraction` is set;
- `"elevation data was not available for this route"` when `ascent_m` is
  `None`.

Missing data is represented as uncertainty, never treated as favorable or
penalized as if it were known.

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
`EXPORT_DIR` under one `route_id` (uuid4 hex) and the API serves them at
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
