# Routing profile evaluation

Do the BRouter profiles behind each bike type give good routes, or do they need
adjusting? This is what was measured (2026-10), what changed, and what was left
alone on purpose. Re-run it with `scripts/check_profiles.py` whenever a profile
changes.

## Method

- Every bike type through its mapped profile ([configuration.md](configuration.md#provider-profile-mapping))
  on the five benchmark routes (`benchmarks/core-v1.json`: a city hop, an old
  town with cobbles, Braunschweig -> Wolfenbüttel, the Harz climb, the
  Okerradweg), plus four regional routes (forest, hills, flat farmland) added
  because the benchmark routes are mostly town. 8 x 5 = 40 routes, then more
  for candidate profiles.
- Everything is read from the OSM tags **BRouter returns for each segment it
  chose**, not from Overpass: the public Overpass server answered 500/504 or
  rate limits for 18 of the first 40 lookups, and BRouter's tags are what the
  profile itself saw.
- "Traffic %" is the share of the route on trunk/primary/secondary/tertiary roads
  **without** a mapped cycle lane/track or `bicycle=designated`. It is a proxy
  for exposure to motor traffic, not a measurement of traffic.
- "Unknown" surface is length with no usable `surface` tag; it is never counted
  as paved.
- No ground truth: nobody rode these routes. "Optimal" here means *the profile does
  what its bike type promises, at a reasonable cost in distance, time and climbing*,
  judged against the other profiles on the same route.

## Results: 5 routes x 8 bike types

| route | bike (profile) | km | +% | min | climb m | traffic % | paved | compacted | loose | cobbles |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bs-city-hop | road (fastbike) | 1.37 | 7 | 3.9 | 0 | 44 | 91 | 0 | 0 | 9 |
| bs-city-hop | gravel (gravel-v1) | 1.32 | 3 | 3.0 | 0 | 0 | 51 | 0 | 0 | 49 |
| bs-city-hop | touring (touring-v1) | 1.28 | 0 | 3.6 | 0 | 0 | 42 | 0 | 0 | 58 |
| bs-city-hop | mountain (mtb) | 1.32 | 3 | 3.7 | 0 | 0 | 51 | 0 | 0 | 49 |
| bs-city-hop | city (trekking) | 1.28 | 0 | 3.6 | 0 | 0 | 42 | 0 | 0 | 58 |
| bs-city-hop | ebike (fastbike) | 1.37 | 7 | 3.9 | 0 | 44 | 91 | 0 | 0 | 9 |
| bs-city-hop | commuter (fastbike-verylowtraffic) | 1.37 | 7 | 3.9 | 0 | 44 | 91 | 0 | 0 | 9 |
| bs-city-hop | recumbent (vm-forum-liegerad-schnell) | 1.41 | 10 | 3.1 | 0 | 56 | 91 | 0 | 0 | 9 |
| bs-oldtown-cobble | road (fastbike) | 1.16 | 73 | 3.4 | 1 | 0 | 61 | 0 | 0 | 38 |
| bs-oldtown-cobble | gravel (gravel-v1) | 0.92 | 37 | 2.1 | 1 | 0 | 49 | 0 | 0 | 50 |
| bs-oldtown-cobble | touring (touring-v1) | 1.04 | 55 | 3.0 | 1 | 0 | 47 | 0 | 0 | 52 |
| bs-oldtown-cobble | mountain (mtb) | 0.67 | 0 | 1.9 | 1 | 0 | 64 | 0 | 0 | 36 |
| bs-oldtown-cobble | city (trekking) | 0.8 | 19 | 2.3 | 1 | 0 | 56 | 0 | 0 | 44 |
| bs-oldtown-cobble | ebike (fastbike) | 1.16 | 73 | 3.4 | 1 | 0 | 61 | 0 | 0 | 38 |
| bs-oldtown-cobble | commuter (fastbike-verylowtraffic) | 1.16 | 73 | 3.4 | 1 | 0 | 61 | 0 | 0 | 38 |
| bs-oldtown-cobble | recumbent (vm-forum-liegerad-schnell) | 1.34 | 100 | 3.1 | 2 | 13 | 59 | 0 | 0 | 41 |
| bs-wf-tour | road (fastbike) | 12.94 | 3 | 37.8 | 29 | 20 | 90 | 4 | 0 | 5 |
| bs-wf-tour | gravel (gravel-v1) | 14.18 | 12 | 33.0 | 29 | 0 | 71 | 0 | 0 | 10 |
| bs-wf-tour | touring (touring-v1) | 12.96 | 3 | 37.6 | 22 | 0 | 76 | 8 | 2 | 8 |
| bs-wf-tour | mountain (mtb) | 12.61 | 0 | 37.1 | 30 | 0 | 51 | 14 | 16 | 8 |
| bs-wf-tour | city (trekking) | 12.68 | 1 | 36.8 | 23 | 0 | 77 | 8 | 2 | 6 |
| bs-wf-tour | ebike (fastbike) | 12.94 | 3 | 37.8 | 29 | 20 | 90 | 4 | 0 | 5 |
| bs-wf-tour | commuter (fastbike-verylowtraffic) | 12.91 | 2 | 37.7 | 29 | 30 | 90 | 4 | 0 | 5 |
| bs-wf-tour | recumbent (vm-forum-liegerad-schnell) | 14.71 | 17 | 33.0 | 6 | 69 | 91 | 0 | 0 | 7 |
| harz-climb | road (fastbike) | 23.72 | 4 | 134.7 | 617 | 70 | 89 | 9 | 2 | 0 |
| harz-climb | gravel (gravel-v1) | 35.15 | 55 | 145.8 | 1032 | 4 | 34 | 52 | 3 | 0 |
| harz-climb | touring (touring-v1) | 22.74 | 0 | 131.6 | 617 | 71 | 85 | 11 | 2 | 1 |
| harz-climb | mountain (mtb) | 23.65 | 4 | 148.5 | 754 | 0 | 11 | 59 | 28 | 1 |
| harz-climb | city (trekking) | 23.74 | 4 | 134.8 | 619 | 68 | 88 | 9 | 2 | 0 |
| harz-climb | ebike (fastbike) | 23.72 | 4 | 134.7 | 617 | 70 | 89 | 9 | 2 | 0 |
| harz-climb | commuter (fastbike-verylowtraffic) | 23.72 | 4 | 134.7 | 617 | 70 | 89 | 9 | 2 | 0 |
| harz-climb | recumbent (vm-forum-liegerad-schnell) | 22.73 | 0 | 99.3 | 617 | 74 | 85 | 11 | 2 | 1 |
| bs-okerradweg | road (fastbike) | 6.35 | 5 | 19.2 | 21 | 27 | 93 | 0 | 0 | 7 |
| bs-okerradweg | gravel (gravel-v1) | 6.45 | 6 | 15.3 | 18 | 12 | 86 | 0 | 0 | 13 |
| bs-okerradweg | touring (touring-v1) | 6.06 | 0 | 18.3 | 19 | 12 | 88 | 0 | 0 | 10 |
| bs-okerradweg | mountain (mtb) | 6.7 | 11 | 20.5 | 32 | 0 | 54 | 16 | 0 | 28 |
| bs-okerradweg | city (trekking) | 6.07 | 0 | 18.3 | 19 | 21 | 88 | 0 | 0 | 10 |
| bs-okerradweg | ebike (fastbike) | 6.35 | 5 | 19.2 | 21 | 27 | 93 | 0 | 0 | 7 |
| bs-okerradweg | commuter (fastbike-verylowtraffic) | 6.21 | 2 | 18.7 | 19 | 34 | 94 | 0 | 0 | 6 |
| bs-okerradweg | recumbent (vm-forum-liegerad-schnell) | 6.28 | 4 | 14.8 | 20 | 34 | 89 | 0 | 0 | 10 |

(+% is the detour over the shortest route among the eight profiles; surfaces are
shares of route length, so paved + compacted + loose + cobbles + unknown = 100.)

## Findings

**Needed adjusting (done): `gravel`.** `custom_gravel-v1` was an unmodified copy
of BRouter's stock gravel profile, which is built to *avoid traffic* and ships with
`prefer_unpaved_paths`, `avoid_steep_inclines` and **`consider_elevation` all off**:
climbing costs nothing. In town it is a traffic-avoiding profile (51-86 % paved),
and in the Elm forest it does produce gravel (53 % compacted at +2 % distance), but
on hills it took any track-bound detour: Bad Harzburg -> Braunlage (22.7 km direct)
came out at **35.2 km, 1032 m of climbing, 146 min**. `custom_gravel-v2` turns
`consider_elevation` on: **31.4 km, 777 m, 118 min** on the same route, with more
compacted surface (64 % vs 52 %). Candidates tried on the benchmark routes plus the
Elm ride, with three more routes held out when the choice was made:

| variant | verdict |
| --- | --- |
| `consider_elevation` on (**adopted, v2**) | fixes the Harz case; city, flat farmland and Okerradweg unchanged; held-out Goslar-Clausthal about equal, Schöppenstedt-Elm 0.5 km shorter, 39 m less climbing, 3 min faster; the one regression is the Elm ride (+2.1 km, +10 m, +5 min) |
| `prefer_unpaved_paths` on | rejected: longer routes everywhere (Elm ride 28.5 km vs 22.7, Harz 38.0 km vs 35.2) for little extra unpaved surface (Okerradweg: 72 % paved instead of 86 %, but the difference is cobbles, not gravel) |
| `avoid_steep_inclines` on | rejected: no effect on the failing route alone |
| unpaved + elevation | not adopted: more gravel on the farmland route (31 % compacted vs 0 %) but +17 % distance there and no gain elsewhere over elevation alone |

`custom_gravel-v1` stays in the repository (profiles are never edited in place, so
older plans stay reproducible) and its warnings still apply.

**Left alone, with the evidence:**

- **`commuter` (`fastbike-verylowtraffic`) does not deliver "low traffic".** Against
  plain `fastbike` it has the *same* route on 4 of 9 routes and *more* traffic on 4
  (Wolfenbüttel tour 30 % vs 20 %, Okerradweg 34 % vs 27 %, Elm ride 91 % vs 88 %,
  Goslar 69 % vs 67 %); it is better only on flat farmland (79 % vs 90 %, and
  23.9 km / 73 min against `fastbike`'s 29.6 km / 91 min). A candidate
  (`fastbike` with `consider_traffic = 2`) cut traffic almost everywhere (city hop
  43 % -> 0, tour 20 -> 12, Okerradweg 27 -> 20) for at most +1 % time, but it is
  identical to `fastbike` on the farmland route, where the current profile is much
  better. Mixed evidence, so not changed; a purpose-built commuter profile is the
  open item.
- **`ebike` is `road`.** Identical geometry on all five routes. BRouter has no
  assist model and an e-bike's cost of climbing differs; ORS has a dedicated profile.
  Known and documented, not a regression.
- **`touring` ~ `city`.** `custom_touring-v1` is stock trekking minus steps and
  ferries, so the two return the same or near-identical routes.
- **`recumbent` carries the most traffic.** 69 % on the Wolfenbüttel tour (mostly
  tertiary roads), 56 % on the city hop and 34 % on the Okerradweg, against 0 / 0 / 12
  for `touring`; it is also the fastest and flattest (6 m of climbing vs 29 on the
  tour). The profile is a community one built for smoothness and speed; without a
  domain reason to change it, it is flagged, not edited.
- **`mountain` (`mtb`) behaves as intended**: paths and tracks, up to 28 % loose or
  soft surface in the hills, 0 % traffic.
- **`road` (`fastbike`) has one oddity**: on the farmland route (Wolfenbüttel ->
  Lehre) it is 25 % longer and 25 % slower than `trekking` (29.6 km / 91 min vs 23.7 km / 72 min),
  both about 93-95 % paved. Worth investigating before relying on it for road
  bikes in flat country.
- **Harz climb, all profiles but `gravel` and `mountain`:** 70-74 % of the route is
  on the B4 primary road. There is no calm *paved* alternative; the calm options
  are unpaved (`mountain`: 0 % traffic at +4 % distance, 28 % loose/soft).

**Not a profile problem:** the ORS comparison numbers for climbing are too noisy to
judge a profile by (the same geometry reports 91 to 125 m of ascent under different
ORS profiles, and `cycling-mountain` reports 3.5 km of ascent on the Harz route), so
this evaluation is BRouter-only.

## Limits

One region (Lower Saxony and the Harz, one `.rd5` tile), nine routes, no riders.
`traffic %` and `unknown %` are proxies, and a profile that looks worse on a
metric may be right for reasons the metric cannot see (a smooth cobbled cycleway
counts as 100 % cobbles). The 40-row table above is the raw evidence: check a
conclusion against it before acting on it.
