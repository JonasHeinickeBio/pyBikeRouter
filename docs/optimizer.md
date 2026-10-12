# Route optimiser

Ask for the **best of several routes** instead of the best fit for one bike type: "less traffic, but
as much asphalt as possible". The planner fetches a broad set of candidate routes for the same trip,
measures every one (traffic, surface, length, time, climb), and selects the route that serves your
goals best -- and says what the choice costs.

It is the same plan as `POST /v1/route/plan` (geocoding, weather, export, history ...) with one
extra field, `optimize`. Without it nothing changes.

## Asking

| Where | How |
| --- | --- |
| **API** | `"optimize": {"goals": [{"metric": "traffic"}, {"metric": "paved", "weight": 0.7}], "max_detour_pct": 30, "effort": "normal"}` on `POST /v1/route/plan` ([api.md](api.md)) |
| **CLI** | `bike-router route plan --origin Braunschweig --destination Wernigerode --optimize traffic --optimize paved=0.7 --max-detour-pct 25 --effort thorough` |
| **Chat** (also the web panel) | `Braunschweig to Wernigerode with less traffic but as much asphalt as possible` · on the route on screen: `optimise for the quietest and smoothest` ([chat.md](chat.md)) |

### Goals

| `metric` | Wants | Read from |
| --- | --- | --- |
| `traffic` | the lowest traffic index | `metrics.traffic_index` -- the length-weighted mean exposure of the road tags (0 = nothing motorised, 1 = main roads all the way); see [api.md](api.md) |
| `busy_roads` | the smallest share of busy roads | `traffic_shares.busy` |
| `paved` | the largest paved share | `engine_surface_shares.paved` ("unknown" is never counted as paved) |
| `unpaved` | the largest unpaved share (off-road riding); not with `paved` | `engine_surface_shares.unpaved` |
| `cobbles` | the smallest cobble share | `engine_surface_shares.cobbles` |
| `distance` / `duration` / `ascent` | the shortest / fastest / least climbing | `metrics.distance_m` / `duration_s` / `ascent_m` |

Each goal has a `weight` (0 < w <= 10, default 1): the first goal in the chat counts 1.0, the next
0.7, 0.5, 0.4 ("less traffic **but** ..." is what to get as well, not instead). `max_detour_pct`
(default 30) bars any route more than that much longer than the shortest one found. The request's own
`max_distance_km` / `max_ascent_m` also bar routes -- unless no route meets them, which is said.

## What is fetched

For BRouter (the engine that returns the road tags the measures need), besides the main route, the
requests below, most useful first and cut by `effort` (`quick` 4, `normal` 8, `thorough` 12):

1. other profiles that tend to serve the goals: for traffic `commuter`, `touring`, `gravel`,
   `trekking`; for asphalt `trekking`, `fastbike`, `commuter`, `touring`; for speed `fastbike`,
   `trekking`; for off-road `mtb`, `gravel`, `touring`. The BRouter `shortest` profile is always
   included as the baseline the detour is measured against;
2. BRouter's own **second and third routes** (`alternativeidx` 1 and 2) for the leading profiles;
3. when traffic is not a goal, the traffic-avoidance switch turned **off** for one profile (it may be
   more direct).

BRouter answers one request at a time, so they queue; the allowance is 20 s plus
`BROUTER_OPTIMIZE_TIMEOUT_PER_KM_S` (default 1.2) per km between the places, at most
`BROUTER_OPTIMIZE_TIMEOUT_MAX_S` (default 300). What has finished by then is used, the rest is
dropped. Near-identical routes (the same geometry within 50 m) are collapsed.

## How the choice is made

Deterministic, no model, no randomness (`optimizer/engine.py`):

1. Every candidate's value for every goal is read from its metrics. A missing value counts 0 for
   that goal (no benefit of the doubt); a goal nobody has a value for is **dropped and reported**.
2. Value -> *goodness* in 0..1, measured from the **best** candidate: 1 for the best, falling
   linearly to 0 at a fixed distance from it (traffic index +0.30, paved share -40 points, cobbles
   +15 points, length or time +25 %, climb +50 % or +100 m). Measuring from the best, not the worst,
   keeps one absurd candidate from flattening everyone else's differences (BRouter's `shortest`
   profile reported 11.7 h for a 58 km route).
3. Utility = weighted mean of the goodness values.
4. Candidates over the detour cap or the hard limits cannot win while any candidate is within them.
5. The highest utility wins; a tie goes to the shorter route. Candidates that no other one beats on
   every goal are marked `pareto`.

## The response

`optimization` on the plan response: the goals (and dropped ones), how many routes were compared, the
`selected` label, a `summary` sentence, `tradeoffs` (what the best of each goal had instead and
what it cost), and a `table` -- every route best fit first with its source ("2nd route", "traffic not
avoided"), length, detour, the measured values (`values` raw, `shown` formatted), goodness, utility,
`pareto`, `feasible`. `candidates` are re-ranked (rank 1 = the best fit) and `route` is that
candidate; the exported GPX/GeoJSON is it too; `explanation` starts with the summary.

## Example (live, real BRouter, 2026-10-12)

Braunschweig -> Wernigerode (gravel bike), `traffic` 1.0 + `paved` 0.7, 39 s, 9 routes (7 distinct):

| Route | km | detour | traffic index | paved | fit |
| --- | --- | --- | --- | --- | --- |
| **gravel, BRouter's 2nd route** | 65.1 | +12 % | 0.05 | 69 % | 0.85 |
| gravel (the usual route) | 69.7 | +20 % | 0.02 | 62 % | 0.85 |
| commuter, 2nd route | 65.7 | +13 % | 0.12 | 80 % | 0.81 |
| commuter | 61.3 | +6 % | 0.10 | 74 % | 0.81 |
| fastbike | 64.3 | +11 % | 0.53 | 98 % | 0.41 |
| shortest | 58.1 | 0 % | 0.41 | 0 % | 0.14 |

The winner and the usual gravel route tie on fit; the winner is 4.6 km shorter with 7 more points
paved and a slightly higher traffic index -- and the table says so. With `duration` 1.0 + `ascent`
0.7 the same trip selects the 65.1 km route that takes 2.7 h (the usual gravel route needs 2.9 h, the
commuter route 3.2 h).

## Limits

- **An estimate of an estimate.** The traffic index comes from OSM tags and judgement weights, not
  from counts ([api.md](api.md)); "as much asphalt as possible" is as good as the
  `surface` tags (10-30 % of a route have none and stay "unknown").
- **Only candidates BRouter can produce.** The optimiser chooses among them; it does not draw new
  roads. A route that exists nowhere in the set cannot be selected.
- **BRouter only.** Other engines contribute their main route, but give no road tags, so the traffic
  and surface goals have no data for them (reported as dropped goals when nobody has any).
- **Time.** About 30-40 s for a 65 km trip, 1-5 min for 200 km (`BROUTER_OPTIMIZE_*`); the longest
  profiles (`mtb`) may not finish on very long trips and are then absent from the comparison.
- **The weights are yours.** Utility is a weighted mean; two reasonable weightings can pick different
  routes, which is why the table with the numbers is part of the answer.
