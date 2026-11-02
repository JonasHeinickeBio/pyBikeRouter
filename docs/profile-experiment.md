# Routing profile experiment: every BRouter profile on ten routes

`docs/profile-evaluation.md` asked whether the profiles behind our eight bike types
behave. This experiment widens it: **every profile in BRouter's upstream
`misc/profiles2` on GitHub plus our four custom ones, on ten new routes, scored with a
rubric that was written down before the first route was run.** Re-run it with
`scripts/profile_experiment.py`; raw data is `benchmarks/profile-experiment-v1/raw.json`,
every per-route table is in `benchmarks/profile-experiment-v1/report.md`.

## What was tested

- **30 profiles.** The 18 stock profiles of upstream master (`abrensch/brouter`,
  `misc/profiles2`, fetched 2026-11-02): 7 meant for cycling (`trekking`, `fastbike`,
  `fastbike-verylowtraffic`, `gravel`, `mtb`, `vm-forum-liegerad-schnell`,
  `vm-forum-velomobil-schnell`) and 11 that are not (`shortest`, `all`, `dummy`,
  `softaccess`, `skating`, `car-vario`, `car-vario-nocost`, `moped`, `rail`, `river`,
  `hiking-mountain`), our four (`touring-v1`, `commuter-v1`, `gravel-v1`, `gravel-v2`), and the
  8 stock profiles that the **container image** ships in an older version (marked `@image`;
  this is what the running service uses for `road`, `ebike`, `city`, `mountain`, `recumbent`).
  The non-cycling ones run through the same rubric as a reference and are left out of the ranking.
- **The engine** is the installed BRouter 1.7.8 (build of 2025-07-12; upstream is at 1.7.10) on
  the project's own segments (rd5 files of 2026-09/10). `car-vario-nocost` needs a class this
  engine does not have (`KinematicNoCostModel`), so it fails on every route; everything else loads.
- **Ten routes**, chosen for variety before any profile was run (km and climb are those of
  the shortest cycling profile):

| route | km | climb m |
|---|---:|---:|
| berlin-commute | 7.66 | 9 |
| muenster-warendorf | 27.51 | 15 |
| koeln-bonn | 28.44 | 24 |
| freiburg-schauinsland | 14.35 | 925 |
| garmisch-mittenwald | 17.95 | 298 |
| muenchen-starnberg | 26.16 | 131 |
| eisenach-brotterode | 20.67 | 279 |
| leipzig-halle | 37.02 | 31 |
| bath-bristol | 20.89 | 68 |
| harz-climb | 22.69 | 612 |

  City, flat farmland, river valley, a 1000 m climb, an alpine valley, city-to-lake, forest
  hills, city-to-city, a UK railway path, and the Harz climb of the earlier evaluation as a
  cross-check (its gravel-v1/gravel-v2/mtb numbers reproduce `profile-evaluation.md` exactly).
- **Measurement.** Everything is read from the OSM tags BRouter returns for each segment it chose
  (no Overpass). BRouter only returns tags that the profile itself reads, so every profile was
  copied with `processUnusedTags = true` (`scripts/profile_experiment.py prepare`). The first
  run, without that, is the reason: the `vm-forum-*` profiles never read `cycleway:left/right/both`
  and looked 6-9 points more exposed to traffic than they are (liegerad 62 % -> 56 %, velomobil
  84 % -> 75 %; the other cycling profiles moved by at most 0.5 points). The routes themselves do
  not change (`same-routes`: 273 of 280 identical in km, minutes and ascent; the 7 that differ are
  `softaccess`, a non-cycling profile whose cost uses an otherwise unread tag).

## How it was scored

Fixed before the first route was run; the weights are judgment, not measurement. Each route and
profile gets seven components from 0 to 1, a route that could not be found scores 0, and each
bike type is one weighted sum:

| component | meaning |
|---|---|
| D directness | 1 at the shortest cycling route, 0 at +50 % length |
| T time | 1 at the fastest cycling route by BRouter's own estimate, 0 at +100 % |
| S safety | 1 - (share on unprotected trunk/primary/secondary/tertiary + 3 x share where cycling is forbidden: motorway, steps, `bicycle=no/dismount`, footway without bicycle access) |
| C climbing | 1 at the least ascent of any cycling route, 0 at that plus (itself + 50 m) |
| P smoothness | paved 1, compacted .7, cobbles .3, loose .2, unknown .5 |
| G gravel fit | (compacted + half of loose) share, 1 from 50 % |
| O off-road | share of track + path ways, 1 from 60 % |

| bike type | weights |
|---|---|
| road | T .30, S .30, P .30, C .10 |
| ebike | S .35, T .25, P .20, D .20 |
| commuter | S .40, T .30, P .20, D .10 |
| city | S .40, P .25, D .25, T .10 |
| touring | S .35, P .25, D .15, C .25 |
| gravel | G .35, S .25, D .20, C .20 |
| mountain | O .40, G .15, S .25, D .20 |
| recumbent | P .35, S .30, C .20, T .15 |

Robustness: 2000 random draws with every weight scaled by U(0.5, 1.5) say how often the winner stays
first. Three views do not depend on the weights at all: the component means, the routes won, and the
post-hoc removals of S and T further down.

## Results

### Mean score per persona (0-100, failed route = 0)

| profile | group | ok | road | ebike | commuter | city | touring | gravel | mountain | recumbent | mean | worst route |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gravel-v2 | ours | 10/10 | 85 | 82 | 84 | 83 | 87 | 67 | 70 | 87 | **81** | 69 |
| gravel | cycling | 10/10 | 81 | 79 | 81 | 81 | 83 | 66 | 71 | 83 | **78** | 67 |
| gravel-v1 | ours | 10/10 | 81 | 79 | 81 | 81 | 83 | 66 | 71 | 83 | **78** | 67 |
| commuter-v1 | ours | 10/10 | 75 | 75 | 74 | 81 | 89 | 62 | 66 | 83 | **76** | 64 |
| touring-v1 | ours | 10/10 | 75 | 75 | 73 | 81 | 88 | 60 | 61 | 82 | **74** | 62 |
| trekking | cycling | 10/10 | 75 | 75 | 73 | 81 | 88 | 60 | 61 | 82 | **74** | 62 |
| mtb | cycling | 10/10 | 60 | 68 | 65 | 75 | 76 | 73 | 80 | 65 | **70** | 47 |
| vm-forum-liegerad-schnell | cycling | 10/10 | 75 | 69 | 68 | 68 | 74 | 46 | 36 | 77 | **64** | 52 |
| fastbike | cycling | 10/10 | 65 | 63 | 60 | 67 | 74 | 47 | 40 | 71 | **61** | 46 |
| fastbike-verylowtraffic | cycling | 10/10 | 65 | 63 | 59 | 67 | 74 | 47 | 33 | 71 | **60** | 45 |
| vm-forum-velomobil-schnell | cycling | 10/10 | 73 | 67 | 65 | 63 | 65 | 40 | 25 | 72 | **59** | 43 |
| all | other | 10/10 | 73 | 74 | 71 | 69 | 68 | 54 | 46 | 69 | **65** | 52 |
| softaccess | other | 10/10 | 58 | 62 | 57 | 66 | 69 | 55 | 49 | 63 | **60** | 44 |
| river | other | 9/10 | 69 | 63 | 68 | 61 | 69 | 46 | 28 | 70 | **59** | 0 |
| shortest | other | 10/10 | 46 | 52 | 44 | 63 | 71 | 56 | 55 | 57 | **56** | 40 |
| hiking-mountain | other | 10/10 | 41 | 50 | 43 | 60 | 65 | 62 | 71 | 51 | **55** | 33 |
| rail | other | 7/10 | 58 | 59 | 60 | 57 | 59 | 42 | 28 | 57 | **52** | 0 |
| skating | other | 8/10 | 50 | 48 | 46 | 56 | 64 | 39 | 43 | 59 | **51** | 0 |
| dummy | other | 10/10 | 41 | 47 | 38 | 57 | 66 | 52 | 44 | 53 | **50** | 38 |
| car-vario | other | 9/10 | 63 | 56 | 53 | 51 | 55 | 32 | 16 | 62 | **48** | 0 |
| moped | other | 7/10 | 40 | 37 | 34 | 39 | 45 | 26 | 14 | 45 | **35** | 0 |
| car-vario-nocost | other | 0/10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** | 0 |

### What each bike type uses today, against the best profile

The mapped profile is scored as the service runs it (stock profiles from the container image, `@image`); rank is its place among the 11 cycling profiles of upstream master and ours.

| bike type | mapped profile | its score | rank | best | best score | gap | best is #1 in weight draws |
|---|---|---:|---:|---|---:|---:|---:|
| road | fastbike@image | 65.2 | 9 | gravel-v2 | 84.9 | +19.8 | 99% |
| ebike | fastbike@image | 63.2 | 11 | gravel-v2 | 81.7 | +18.6 | 99% |
| commuter | commuter-v1 | 73.5 | 4 | gravel-v2 | 83.8 | +10.3 | 100% |
| city | trekking@image | 81.8 | 2 | gravel-v2 | 83.3 | +1.5 | 84% |
| touring | touring-v1 | 88.1 | 2 | commuter-v1 | 88.8 | +0.7 | 83% |
| gravel | gravel-v2 | 66.5 | 2 | mtb | 73.0 | +6.5 | 98% |
| mountain | mtb@image | 80.0 | 1 | mtb | 80.0 | +0.0 | 100% |
| recumbent | vm-forum-liegerad-schnell@image | 76.6 | 7 | gravel-v2 | 87.2 | +10.5 | 100% |

`rank` counts the served profile among the 11 cycling profiles of upstream master and ours.

### Top three per persona (cycling profiles, with how often they stay there)

| persona | 1st | 2nd | 3rd |
|---|---|---|---|
| road | gravel-v2 85 (100% top-3) | gravel 81 (90% top-3) | gravel-v1 81 (87% top-3) |
| ebike | gravel-v2 82 (100% top-3) | gravel 79 (92% top-3) | gravel-v1 79 (89% top-3) |
| commuter | gravel-v2 84 (100% top-3) | gravel 81 (100% top-3) | gravel-v1 81 (98% top-3) |
| city | gravel-v2 83 (92% top-3) | touring-v1 81 (53% top-3) | trekking 81 (46% top-3) |
| touring | commuter-v1 89 (100% top-3) | touring-v1 88 (100% top-3) | trekking 88 (78% top-3) |
| gravel | mtb 73 (100% top-3) | gravel-v2 67 (82% top-3) | gravel 66 (85% top-3) |
| mountain | mtb 80 (100% top-3) | gravel 71 (100% top-3) | gravel-v1 71 (100% top-3) |
| recumbent | gravel-v2 87 (100% top-3) | commuter-v1 83 (44% top-3) | gravel 83 (60% top-3) |

### Routes won per persona (of 10; ties go to the profile listed first)

| persona | wins |
|---|---|
| road | gravel 6, gravel-v2 3, vm-forum-velomobil-schnell 1 |
| ebike | gravel 5, gravel-v2 2, vm-forum-velomobil-schnell 1, commuter-v1 1, mtb 1 |
| commuter | gravel 6, gravel-v2 3, vm-forum-velomobil-schnell 1 |
| city | commuter-v1 3, gravel 3, fastbike-verylowtraffic 1, gravel-v2 1, trekking 1, mtb 1 |
| touring | commuter-v1 3, trekking 2, fastbike-verylowtraffic 1, gravel 1, gravel-v2 1, touring-v1 1, mtb 1 |
| gravel | mtb 7, fastbike-verylowtraffic 1, gravel 1, touring-v1 1 |
| mountain | mtb 7, trekking 1, commuter-v1 1, fastbike-verylowtraffic 1 |
| recumbent | gravel 5, gravel-v2 3, vm-forum-velomobil-schnell 1, commuter-v1 1 |

### Components, mean over the routes (0-100; the weights are not involved)

| profile | D direct | T time | S safe | C climb | P smooth | G gravel | O off-road |
|---|---:|---:|---:|---:|---:|---:|---:|
| gravel-v2 | 64 | 72 | 97 | 87 | 85 | 34 | 69 |
| gravel | 61 | 66 | 96 | 77 | 82 | 40 | 72 |
| gravel-v1 | 61 | 66 | 96 | 77 | 82 | 40 | 72 |
| commuter-v1 | 78 | 42 | 89 | 97 | 87 | 15 | 64 |
| touring-v1 | 83 | 44 | 84 | 96 | 89 | 9 | 54 |
| trekking | 83 | 44 | 84 | 96 | 89 | 9 | 54 |
| mtb | 91 | 32 | 84 | 68 | 62 | 58 | 80 |
| vm-forum-liegerad-schnell | 77 | 80 | 43 | 93 | 94 | 3 | 22 |
| fastbike | 85 | 48 | 45 | 89 | 93 | 2 | 28 |
| fastbike-verylowtraffic | 82 | 46 | 47 | 89 | 94 | 2 | 11 |
| vm-forum-velomobil-schnell | 80 | 95 | 24 | 84 | 95 | 3 | 6 |

### What the profiles do (mean over the routes that were found)

| profile | km vs shortest cycling | traffic % | forbidden % | paved | compacted | loose | cobbles | unknown | track+path | climb m |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gravel-v2 | +18 % | 1 | 1 | 66 | 21 | 2 | 7 | 5 | 48 | 280 |
| gravel | +20 % | 1 | 1 | 61 | 20 | 5 | 6 | 7 | 51 | 342 |
| gravel-v1 | +20 % | 1 | 1 | 61 | 20 | 5 | 6 | 7 | 51 | 342 |
| commuter-v1 | +11 % | 9 | 0 | 76 | 6 | 2 | 11 | 5 | 40 | 249 |
| touring-v1 | +9 % | 14 | 0 | 81 | 4 | 2 | 9 | 4 | 33 | 246 |
| trekking | +9 % | 14 | 0 | 81 | 4 | 2 | 9 | 4 | 33 | 246 |
| mtb | +4 % | 1 | 5 | 36 | 18 | 27 | 9 | 10 | 67 | 350 |
| vm-forum-liegerad-schnell | +11 % | 56 | 0 | 89 | 1 | 0 | 3 | 6 | 13 | 250 |
| fastbike | +7 % | 54 | 0 | 88 | 1 | 0 | 5 | 6 | 17 | 256 |
| fastbike-verylowtraffic | +9 % | 52 | 0 | 88 | 1 | 0 | 3 | 7 | 7 | 256 |
| vm-forum-velomobil-schnell | +10 % | 75 | 0 | 90 | 1 | 0 | 1 | 7 | 3 | 259 |
| all | -4 % | 31 | 14 | 61 | 5 | 13 | 7 | 14 | 24 | 313 |
| softaccess | -4 % | 36 | 7 | 60 | 5 | 13 | 7 | 14 | 25 | 316 |
| river | +133 % | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 0 | 87 |
| shortest | -4 % | 36 | 6 | 68 | 6 | 14 | 6 | 6 | 35 | 315 |
| hiking-mountain | +2 % | 1 | 20 | 42 | 15 | 20 | 13 | 9 | 61 | 356 |
| rail | +6 % | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 0 | 71 |
| skating | +19 % | 13 | 8 | 88 | 0 | 0 | 8 | 3 | 35 | 201 |
| dummy | -4 % | 39 | 14 | 69 | 5 | 13 | 6 | 6 | 23 | 312 |
| car-vario | +9 % | 71 | 23 | 96 | 0 | 0 | 0 | 3 | 0 | 250 |
| moped | +8 % | 85 | 5 | 99 | 0 | 0 | 0 | 1 | 0 | 219 |
| car-vario-nocost | no route on any of the 10 | | | | | | | | | |

### Stock profiles: container image vs upstream master

| profile | mean score image | mean score master | change | km image | km master | traffic % image | master | routes whose geometry differs |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| fastbike | 60.9 | 60.8 | -0.1 | 24.65 | 23.67 | 44 | 54 | 9/10 |
| fastbike-verylowtraffic | 58.8 | 59.8 | +1.0 | 23.90 | 24.06 | 54 | 52 | 3/10 |
| gravel | 77.9 | 77.9 | +0.0 | 26.73 | 26.73 | 1 | 1 | 0/10 |
| mtb | 70.1 | 70.1 | +0.0 | 23.49 | 23.49 | 1 | 1 | 0/10 |
| trekking | 75.3 | 74.3 | -0.9 | 24.19 | 24.21 | 14 | 14 | 6/10 |
| shortest | 54.0 | 55.5 | +1.5 | 21.43 | 21.45 | 42 | 36 | 4/10 |
| vm-forum-liegerad-schnell | 64.0 | 64.0 | +0.0 | 24.36 | 24.36 | 56 | 56 | 0/10 |
| vm-forum-velomobil-schnell | 58.6 | 58.6 | +0.0 | 24.28 | 24.28 | 75 | 75 | 0/10 |


## Findings

1. **`gravel-v2` is the best all-round profile here** (81 against 78 for stock `gravel`, 74 for
   `trekking`/`touring-v1`, 61 for `fastbike`), by being almost traffic-free (1 % exposure), smooth
   (66 % paved) and light on climbing (280 m mean against 342), at +18 % distance. It wins the
   road, ebike, commuter, city and recumbent personas outright and is second for gravel; only
   touring (where `commuter-v1` and `touring-v1` lead) and mountain are not its. That is a
   statement about the rubric as much as about the profile: see the limits.
2. **`road` and `ebike` have the widest gap to the best profile** (65 and 63 against 85 and 82),
   then `recumbent` and `commuter` (77 and 74 against 87 and 84). For `road`, `ebike` and `recumbent`
   the cause is one component: S is 43-45 for `fastbike` and `vm-forum-liegerad-schnell` against
   84-97 for the trekking and gravel family, because they put 44-56 % of the route on main roads
   without a mapped cycle lane (the velomobil profile 75 %). That is what a fast profile is for,
   and with S removed the two `vm-forum` profiles take the top two places for road, ebike,
   commuter, city and recumbent. Whether `road` should mean "fast" or "fast but not on the B-road" is a product decision this
   experiment cannot make; it only shows the size of the trade.
3. **`commuter-v1` holds up on routes it was not tuned on**: 9 % exposure against 54 % for `fastbike`
   (the earlier evaluation: 14 % against 56 %), at +11 % distance against +7 %. It is the best touring
   profile (89) and fourth for its own persona; the three above it are the gravel profiles, which are less
   direct (D 61-64 against 78) but whose own speed model estimates much faster times (T 66-72
   against 42). T compares speed models, not routes, so this ranking is soft: with T removed
   `commuter-v1` is first for ebike, city and recumbent, and the gravel profiles still lead for
   road and commuter.
4. **No profile is good at gravel by this measure.** The best gravel fit is `mtb` (58 of 100: 18 %
   compacted, 27 % loose); `gravel-v2` gets 34 (21 % compacted, 2 % loose) and stock `gravel` 40. `mtb` wins
   the gravel persona (73 against 67 for `gravel-v2`) and 7 of 10 routes, but with 5 % of the route
   where cycling is forbidden or to be pushed and 27 % loose surface; `gravel-v2` is the better
   "gravel bike" profile if loose surface and legality count more than the share of unpaved.
   `gravel-v2` against `gravel-v1`: +18 % against +20 % distance, 280 m against 342 m of climbing,
   81 against 78; the gap is concentrated in the hill routes (Harz: 31.4 km, 777 m against 35.2 km,
   1032 m). Stock `gravel` and `gravel-v1` give identical routes on all ten.
5. **`touring-v1` and `trekking` are the same profile in practice** (identical routes on nine of ten;
   on Bath-Bristol they differ by 23 m). `fastbike-verylowtraffic` takes a different route from
   `fastbike` on nine of ten, but on average only 2 points less main-road exposure (52 % against
   54 %): the name is not earned, as the earlier evaluation found.
6. **The image's stock profiles are out of date, mostly harmlessly.** Of the 18 stock files in the
   image, 10 differ from upstream master and 2 (`car-vario-nocost`, `skating`) are missing. For the
   routes it makes little difference: `gravel`, `mtb` and both `vm-forum` profiles give identical
   routes; `trekking` changes on 6 of 10 routes, `shortest` on 4, `fastbike-verylowtraffic` on 3.
   `fastbike` is the exception: 9 of 10 routes change, 4 % shorter and **10 points more exposure to
   main roads** (44 % -> 54 %). Upgrading the image would move `road` and `ebike` toward more traffic.
7. **Non-cycling profiles behave as designed.** The car and moped profiles put 71 % and 85 % of the
   route on main roads, the car profile 23 % where cycling is forbidden and `hiking-mountain` 20 %;
   `rail`, `river` and `moped` find a route on only 7, 9 and 7 of 10, `skating` fails on the two hill
   routes (Eisenach, Harz). None of them is usable for a bicycle.
8. **Earlier numbers to treat with care:** the recumbent rows of `profile-evaluation.md` (traffic 69 %
   on the Wolfenbüttel tour and so on) were measured without `processUnusedTags` and very likely
   overstate the exposure of `vm-forum-liegerad-schnell`; `scripts/check_profiles.py` has the same
   blind spot (it does not set the switch, so it is only reliable for profiles that read every tag it
   counts).

## Limits

- **Ten routes**, nine in Germany and one in England; one run each (BRouter is deterministic).
- **No ground truth**: nobody rode them. The scores are proxies built from OSM tags, with weights I chose;
  the sensitivity draws vary the weights by up to 50 %, not the components' definitions.
- **S counts every main road without a mapped cycle lane as exposure**, however quiet; **T compares each
  profile's own speed model**, which flatters profiles that assume a faster rider; **unknown surface** is
  worth half a point of smoothness, and tracks with no surface tag are counted as neither gravel nor paved.
- Only the main route is used (`alternativeidx=0`), with no via points and none of the per-request
  profile options (`avoid_high_traffic_roads` and the like).
- The post-hoc removal tables were added after seeing the first results, to show how much each finding
  depends on S and T; they are not part of the rubric.

## Reproduce

```bash
# profiles: upstream (gh api repos/abrensch/brouter/contents/misc/profiles2), the image's own
# (docker cp <brouter container>:/profiles2/<name>.brf) and docker/brouter/profiles
python scripts/profile_experiment.py prepare --upstream UP --image IMG --ours docker/brouter/profiles --out T
# a second BRouter on the same segments with T as its custom profile directory (relative paths, as
# server.sh uses them: an absolute --customprofiles path is not found by this engine)
docker run -d --network host -v $PWD/docker/brouter/segments:/segments4:ro -v T:/customprofiles:ro \
  -v $PWD/docker/brouter/lookups.dat:/profiles2/lookups.dat:ro -w /bin --entrypoint java \
  ghcr.io/abrensch/brouter -Xmx128M -cp ../brouter.jar btools.server.RouteServer \
  ../segments4 ../profiles2 ../customprofiles 17778 1 127.0.0.1
python scripts/profile_experiment.py run   --out benchmarks/profile-experiment-v1/raw.json
python scripts/profile_experiment.py score --raw benchmarks/profile-experiment-v1/raw.json
```
