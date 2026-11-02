## Mean score per persona (0-100, failed route = 0)

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

## What each bike type uses today, against the best profile

The mapped profile is scored as the service runs it (stock profiles from the container image, `@image`); rank is its place among the 11 ranked profiles.

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

## Top three per persona (cycling profiles, with how often they stay there)

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

## Routes won per persona (of 10; ties go to the profile listed first)

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

## Components, mean over the routes (0-100; the weights are not involved)

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

## Post-hoc: the same ranking with components removed

Not part of the rubric fixed before the run. S is the traffic proxy; T compares each profile's own speed model (a velomobil profile assumes a faster vehicle), so it is the weakest component. The remaining weights are scaled up to sum to 1.

| persona | without | 1st | 2nd | 3rd |
|---|---|---|---|---|
| road | S | vm-forum-velomobil-schnell 93 | vm-forum-liegerad-schnell 88 | gravel-v2 80 |
| road | T | gravel-v2 91 | commuter-v1 89 | touring-v1 88 |
| road | S and T | vm-forum-liegerad-schnell 94 | fastbike-verylowtraffic 92 | vm-forum-velomobil-schnell 92 |
| ebike | S | vm-forum-velomobil-schnell 90 | vm-forum-liegerad-schnell 83 | fastbike 74 |
| ebike | T | commuter-v1 86 | touring-v1 85 | trekking 85 |
| ebike | S and T | fastbike 89 | fastbike-verylowtraffic 88 | vm-forum-velomobil-schnell 87 |
| commuter | S | vm-forum-velomobil-schnell 92 | vm-forum-liegerad-schnell 84 | gravel-v2 75 |
| commuter | T | gravel-v2 89 | gravel 87 | gravel-v1 87 |
| commuter | S and T | fastbike 91 | vm-forum-velomobil-schnell 90 | fastbike-verylowtraffic 90 |
| city | S | vm-forum-velomobil-schnell 89 | vm-forum-liegerad-schnell 85 | fastbike 82 |
| city | T | commuter-v1 85 | touring-v1 85 | trekking 85 |
| city | S and T | fastbike 89 | fastbike-verylowtraffic 88 | vm-forum-velomobil-schnell 87 |
| touring | S | trekking 90 | touring-v1 90 | vm-forum-liegerad-schnell 90 |
| touring | S and T | trekking 90 | touring-v1 90 | vm-forum-liegerad-schnell 90 |
| gravel | S | mtb 69 | gravel-v2 56 | gravel 55 |
| gravel | S and T | mtb 69 | gravel-v2 56 | gravel 55 |
| mountain | S | mtb 79 | gravel 62 | gravel-v1 62 |
| mountain | S and T | mtb 79 | gravel 62 | gravel-v1 62 |
| recumbent | S | vm-forum-velomobil-schnell 92 | vm-forum-liegerad-schnell 91 | gravel-v2 83 |
| recumbent | T | commuter-v1 90 | gravel-v2 90 | touring-v1 89 |
| recumbent | S and T | vm-forum-liegerad-schnell 94 | fastbike-verylowtraffic 92 | fastbike 92 |

## What the profiles do (mean over the routes that were found)

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

## Stock profiles: container image vs upstream master

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

## Failures

- eisenach-brotterode / skating: target island detected for section 0
- harz-climb / skating: target island detected for section 0
- berlin-commute / car-vario: no track found at pass=0
- berlin-commute / car-vario-nocost: HTTP Error 500: Internal Server Error
- muenster-warendorf / car-vario-nocost: HTTP Error 500: Internal Server Error
- koeln-bonn / car-vario-nocost: HTTP Error 500: Internal Server Error
- freiburg-schauinsland / car-vario-nocost: HTTP Error 500: Internal Server Error
- garmisch-mittenwald / car-vario-nocost: HTTP Error 500: Internal Server Error
- muenchen-starnberg / car-vario-nocost: HTTP Error 500: Internal Server Error
- eisenach-brotterode / car-vario-nocost: HTTP Error 500: Internal Server Error
- leipzig-halle / car-vario-nocost: HTTP Error 500: Internal Server Error
- bath-bristol / car-vario-nocost: HTTP Error 500: Internal Server Error
- harz-climb / car-vario-nocost: HTTP Error 500: Internal Server Error
- berlin-commute / moped: no track found at pass=0
- bath-bristol / moped: no track found at pass=0
- harz-climb / moped: target island detected for section 0
- freiburg-schauinsland / rail: target island detected for section 0
- leipzig-halle / rail: no track found at pass=0
- harz-climb / rail: no track found at pass=0
- harz-climb / river: target island detected for section 0

## Per route (km, min, climb m, traffic %, forbidden %, paved/compacted/loose/cobbles/unknown %)

### berlin-commute  (shortest cycling 7.66 km, least climb 9 m)

| profile | km | min | climb | traffic | forbidden | paved | cmp | loose | cobbl | unk | mean score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gravel-v2 | 8.70 | 20 | 11 | 0 | 4 | 53 | 0 | 1 | 45 | 1 | 69 |
| gravel | 8.70 | 20 | 11 | 0 | 4 | 53 | 0 | 1 | 45 | 1 | 69 |
| gravel-v1 | 8.70 | 20 | 11 | 0 | 4 | 53 | 0 | 1 | 45 | 1 | 69 |
| commuter-v1 | 8.48 | 25 | 12 | 1 | 2 | 41 | 0 | 1 | 57 | 2 | 64 |
| touring-v1 | 8.40 | 25 | 16 | 1 | 2 | 47 | 0 | 1 | 51 | 2 | 65 |
| trekking | 8.40 | 25 | 16 | 1 | 2 | 47 | 0 | 1 | 51 | 2 | 65 |
| mtb | 7.66 | 22 | 12 | 0 | 27 | 40 | 6 | 1 | 51 | 1 | 47 |
| vm-forum-liegerad-schnell | 8.79 | 20 | 9 | 40 | 2 | 93 | 0 | 1 | 5 | 1 | 63 |
| fastbike | 7.84 | 23 | 11 | 22 | 2 | 91 | 0 | 1 | 7 | 1 | 68 |
| fastbike-verylowtraffic | 7.90 | 24 | 14 | 10 | 2 | 83 | 0 | 1 | 15 | 1 | 70 |
| vm-forum-velomobil-schnell | 7.96 | 14 | 13 | 52 | 2 | 91 | 1 | 1 | 6 | 1 | 67 |
| all | 7.44 | 10 | 10 | 7 | 37 | 56 | 0 | 7 | 32 | 5 | 52 |
| softaccess | 7.44 | 22 | 10 | 7 | 37 | 56 | 0 | 7 | 32 | 5 | 44 |
| river | 15.81 | 21 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 58 |
| shortest | 7.43 | 89 | 14 | 31 | 23 | 76 | 1 | 7 | 15 | 2 | 40 |
| hiking-mountain | 7.60 | 91 | 15 | 0 | 67 | 29 | 6 | 1 | 62 | 1 | 33 |
| rail | 15.61 | 21 | 12 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 58 |
| skating | 8.37 | 29 | 10 | 1 | 45 | 57 | 0 | 0 | 41 | 1 | 35 |
| dummy | 7.42 | 89 | 10 | 7 | 37 | 56 | 0 | 7 | 32 | 4 | 38 |
| car-vario | no track found at pass=0 | | | | | | | | | | 0 |
| moped | no track found at pass=0 | | | | | | | | | | 0 |
| car-vario-nocost | HTTP Error 500: Internal Server Error | | | | | | | | | | 0 |

### muenster-warendorf  (shortest cycling 27.51 km, least climb 15 m)

| profile | km | min | climb | traffic | forbidden | paved | cmp | loose | cobbl | unk | mean score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gravel-v2 | 30.41 | 70 | 27 | 0 | 0 | 87 | 7 | 3 | 2 | 2 | 78 |
| gravel | 30.41 | 70 | 27 | 0 | 0 | 87 | 7 | 3 | 2 | 2 | 78 |
| gravel-v1 | 30.41 | 70 | 27 | 0 | 0 | 87 | 7 | 3 | 2 | 2 | 78 |
| commuter-v1 | 27.76 | 78 | 15 | 1 | 0 | 90 | 0 | 0 | 10 | 0 | 81 |
| touring-v1 | 28.91 | 82 | 20 | 1 | 0 | 89 | 4 | 1 | 6 | 0 | 76 |
| trekking | 28.91 | 82 | 20 | 1 | 0 | 89 | 4 | 1 | 6 | 0 | 76 |
| mtb | 30.44 | 86 | 19 | 0 | 0 | 41 | 20 | 22 | 8 | 8 | 75 |
| vm-forum-liegerad-schnell | 27.96 | 62 | 20 | 83 | 0 | 94 | 0 | 0 | 6 | 0 | 55 |
| fastbike | 27.51 | 78 | 22 | 43 | 0 | 87 | 0 | 0 | 13 | 0 | 66 |
| fastbike-verylowtraffic | 27.83 | 79 | 19 | 89 | 0 | 99 | 0 | 0 | 1 | 0 | 48 |
| vm-forum-velomobil-schnell | 28.01 | 45 | 19 | 95 | 0 | 100 | 0 | 0 | 0 | 0 | 57 |
| all | 26.90 | 36 | 21 | 27 | 1 | 33 | 1 | 0 | 7 | 59 | 72 |
| softaccess | 26.90 | 76 | 21 | 27 | 1 | 33 | 1 | 0 | 7 | 59 | 62 |
| river | 46.80 | 62 | 17 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 60 |
| shortest | 27.11 | 323 | 23 | 13 | 1 | 88 | 2 | 0 | 9 | 0 | 72 |
| hiking-mountain | 27.61 | 329 | 25 | 0 | 4 | 83 | 2 | 1 | 13 | 0 | 72 |
| rail | 27.45 | 37 | 21 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 78 |
| skating | 31.05 | 105 | 21 | 8 | 0 | 91 | 0 | 0 | 8 | 1 | 72 |
| dummy | 27.01 | 322 | 21 | 87 | 3 | 95 | 2 | 0 | 3 | 0 | 43 |
| car-vario | 28.83 | 39 | 22 | 96 | 0 | 100 | 0 | 0 | 0 | 0 | 55 |
| moped | 28.33 | 80 | 21 | 93 | 2 | 100 | 0 | 0 | 0 | 0 | 44 |
| car-vario-nocost | HTTP Error 500: Internal Server Error | | | | | | | | | | 0 |

### koeln-bonn  (shortest cycling 28.44 km, least climb 24 m)

| profile | km | min | climb | traffic | forbidden | paved | cmp | loose | cobbl | unk | mean score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gravel-v2 | 33.65 | 78 | 31 | 0 | 0 | 92 | 2 | 0 | 4 | 2 | 79 |
| gravel | 33.65 | 78 | 31 | 0 | 0 | 92 | 2 | 0 | 4 | 2 | 79 |
| gravel-v1 | 33.65 | 78 | 31 | 0 | 0 | 92 | 2 | 0 | 4 | 2 | 79 |
| commuter-v1 | 28.44 | 83 | 30 | 1 | 0 | 79 | 0 | 0 | 21 | 0 | 80 |
| touring-v1 | 28.55 | 83 | 33 | 2 | 0 | 80 | 0 | 0 | 20 | 0 | 80 |
| trekking | 28.55 | 83 | 33 | 2 | 0 | 80 | 0 | 0 | 20 | 0 | 80 |
| mtb | 30.72 | 89 | 34 | 1 | 0 | 77 | 7 | 2 | 14 | 0 | 77 |
| vm-forum-liegerad-schnell | 29.29 | 66 | 24 | 55 | 0 | 89 | 0 | 0 | 11 | 0 | 66 |
| fastbike | 28.69 | 83 | 25 | 30 | 0 | 80 | 0 | 0 | 19 | 0 | 71 |
| fastbike-verylowtraffic | 32.15 | 93 | 29 | 26 | 0 | 97 | 0 | 0 | 3 | 0 | 65 |
| vm-forum-velomobil-schnell | 28.89 | 49 | 32 | 85 | 0 | 96 | 0 | 0 | 4 | 0 | 59 |
| all | 26.98 | 36 | 25 | 40 | 14 | 88 | 0 | 0 | 9 | 3 | 60 |
| softaccess | 27.57 | 80 | 30 | 48 | 3 | 84 | 4 | 0 | 9 | 3 | 60 |
| river | 33.60 | 45 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 74 |
| shortest | 27.62 | 330 | 34 | 42 | 3 | 85 | 4 | 0 | 11 | 0 | 57 |
| hiking-mountain | 28.38 | 339 | 32 | 1 | 3 | 76 | 5 | 1 | 19 | 0 | 73 |
| rail | 31.28 | 42 | 17 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 76 |
| skating | 30.45 | 105 | 36 | 0 | 0 | 88 | 0 | 0 | 8 | 5 | 75 |
| dummy | 26.96 | 322 | 25 | 40 | 14 | 88 | 0 | 0 | 9 | 3 | 47 |
| car-vario | 29.48 | 38 | 14 | 29 | 72 | 100 | 0 | 0 | 0 | 0 | 55 |
| moped | 29.12 | 84 | 27 | 87 | 4 | 100 | 0 | 0 | 0 | 0 | 46 |
| car-vario-nocost | HTTP Error 500: Internal Server Error | | | | | | | | | | 0 |

### freiburg-schauinsland  (shortest cycling 14.35 km, least climb 925 m)

| profile | km | min | climb | traffic | forbidden | paved | cmp | loose | cobbl | unk | mean score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gravel-v2 | 17.76 | 111 | 942 | 0 | 0 | 19 | 78 | 3 | 0 | 0 | 88 |
| gravel | 18.28 | 116 | 985 | 0 | 0 | 15 | 74 | 3 | 0 | 8 | 86 |
| gravel-v1 | 18.28 | 116 | 985 | 0 | 0 | 15 | 74 | 3 | 0 | 8 | 86 |
| commuter-v1 | 15.75 | 165 | 925 | 0 | 0 | 86 | 6 | 7 | 1 | 0 | 82 |
| touring-v1 | 15.79 | 165 | 925 | 1 | 0 | 86 | 6 | 7 | 1 | 0 | 82 |
| trekking | 15.79 | 165 | 925 | 1 | 0 | 86 | 6 | 7 | 1 | 0 | 82 |
| mtb | 14.35 | 174 | 1056 | 0 | 0 | 18 | 28 | 52 | 0 | 1 | 81 |
| vm-forum-liegerad-schnell | 21.13 | 133 | 969 | 66 | 0 | 100 | 0 | 0 | 0 | 0 | 52 |
| fastbike | 20.23 | 177 | 969 | 72 | 0 | 100 | 0 | 0 | 0 | 0 | 46 |
| fastbike-verylowtraffic | 20.11 | 177 | 969 | 75 | 0 | 100 | 0 | 0 | 0 | 0 | 45 |
| vm-forum-velomobil-schnell | 21.21 | 157 | 969 | 82 | 0 | 100 | 0 | 0 | 0 | 0 | 43 |
| all | 13.29 | 18 | 1021 | 0 | 7 | 30 | 18 | 52 | 0 | 0 | 82 |
| softaccess | 13.29 | 171 | 1021 | 0 | 7 | 30 | 18 | 52 | 0 | 0 | 75 |
| river | 10.60 | 14 | 135 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 79 |
| shortest | 13.29 | 215 | 1021 | 0 | 7 | 30 | 18 | 52 | 0 | 0 | 69 |
| hiking-mountain | 14.24 | 228 | 1066 | 0 | 30 | 16 | 30 | 53 | 0 | 1 | 45 |
| rail | target island detected for section 0 | | | | | | | | | | 0 |
| skating | 25.91 | 193 | 969 | 33 | 0 | 97 | 0 | 0 | 0 | 3 | 56 |
| dummy | 13.29 | 215 | 1021 | 0 | 7 | 30 | 18 | 52 | 0 | 0 | 69 |
| car-vario | 20.65 | 27 | 681 | 95 | 3 | 100 | 0 | 0 | 0 | 0 | 44 |
| moped | 20.61 | 139 | 681 | 82 | 0 | 100 | 0 | 0 | 0 | 0 | 47 |
| car-vario-nocost | HTTP Error 500: Internal Server Error | | | | | | | | | | 0 |

### garmisch-mittenwald  (shortest cycling 17.95 km, least climb 298 m)

| profile | km | min | climb | traffic | forbidden | paved | cmp | loose | cobbl | unk | mean score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gravel-v2 | 21.32 | 71 | 379 | 1 | 0 | 85 | 9 | 1 | 0 | 4 | 83 |
| gravel | 21.32 | 71 | 379 | 1 | 0 | 85 | 9 | 1 | 0 | 4 | 83 |
| gravel-v1 | 21.32 | 71 | 379 | 1 | 0 | 85 | 9 | 1 | 0 | 4 | 83 |
| commuter-v1 | 18.19 | 85 | 323 | 31 | 0 | 95 | 0 | 0 | 3 | 2 | 74 |
| touring-v1 | 18.19 | 85 | 323 | 31 | 0 | 95 | 0 | 0 | 3 | 2 | 74 |
| trekking | 18.19 | 85 | 323 | 31 | 0 | 95 | 0 | 0 | 3 | 2 | 74 |
| mtb | 18.13 | 127 | 670 | 1 | 0 | 26 | 24 | 35 | 0 | 16 | 67 |
| vm-forum-liegerad-schnell | 18.53 | 62 | 302 | 36 | 0 | 99 | 0 | 0 | 1 | 0 | 79 |
| fastbike | 17.95 | 84 | 321 | 67 | 0 | 96 | 0 | 0 | 3 | 1 | 62 |
| fastbike-verylowtraffic | 18.32 | 85 | 321 | 58 | 0 | 96 | 0 | 0 | 3 | 1 | 63 |
| vm-forum-velomobil-schnell | 18.33 | 62 | 298 | 68 | 0 | 100 | 0 | 0 | 0 | 0 | 67 |
| all | 17.90 | 24 | 319 | 50 | 1 | 87 | 0 | 0 | 2 | 11 | 70 |
| softaccess | 17.90 | 84 | 319 | 50 | 1 | 87 | 0 | 0 | 2 | 11 | 65 |
| river | 163.68 | 218 | 333 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 51 |
| shortest | 17.85 | 224 | 320 | 61 | 1 | 96 | 0 | 0 | 2 | 2 | 54 |
| hiking-mountain | 19.41 | 276 | 782 | 1 | 7 | 16 | 41 | 17 | 0 | 26 | 59 |
| rail | 19.21 | 26 | 271 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 77 |
| skating | 18.57 | 89 | 302 | 25 | 1 | 99 | 0 | 0 | 0 | 1 | 76 |
| dummy | 17.90 | 224 | 319 | 62 | 1 | 96 | 0 | 0 | 2 | 2 | 53 |
| car-vario | 18.07 | 26 | 298 | 90 | 0 | 100 | 0 | 0 | 0 | 0 | 59 |
| moped | 18.07 | 81 | 298 | 90 | 0 | 100 | 0 | 0 | 0 | 0 | 55 |
| car-vario-nocost | HTTP Error 500: Internal Server Error | | | | | | | | | | 0 |

### muenchen-starnberg  (shortest cycling 26.16 km, least climb 131 m)

| profile | km | min | climb | traffic | forbidden | paved | cmp | loose | cobbl | unk | mean score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gravel-v2 | 27.95 | 69 | 138 | 0 | 2 | 79 | 14 | 0 | 2 | 4 | 86 |
| gravel | 27.95 | 69 | 138 | 0 | 2 | 79 | 14 | 0 | 2 | 4 | 86 |
| gravel-v1 | 27.95 | 69 | 138 | 0 | 2 | 79 | 14 | 0 | 2 | 4 | 86 |
| commuter-v1 | 28.11 | 89 | 131 | 6 | 1 | 92 | 3 | 0 | 2 | 3 | 80 |
| touring-v1 | 27.34 | 87 | 131 | 6 | 1 | 92 | 3 | 0 | 2 | 3 | 81 |
| trekking | 27.34 | 87 | 131 | 6 | 1 | 92 | 3 | 0 | 2 | 3 | 81 |
| mtb | 27.32 | 88 | 141 | 0 | 4 | 47 | 3 | 34 | 3 | 14 | 75 |
| vm-forum-liegerad-schnell | 27.20 | 66 | 132 | 59 | 0 | 99 | 0 | 0 | 0 | 0 | 67 |
| fastbike | 26.16 | 83 | 133 | 61 | 1 | 98 | 0 | 0 | 1 | 1 | 62 |
| fastbike-verylowtraffic | 27.58 | 87 | 132 | 60 | 1 | 97 | 0 | 0 | 3 | 0 | 58 |
| vm-forum-velomobil-schnell | 26.95 | 54 | 133 | 83 | 0 | 100 | 0 | 0 | 0 | 0 | 61 |
| all | 25.17 | 34 | 125 | 12 | 59 | 92 | 3 | 0 | 3 | 2 | 57 |
| softaccess | 25.30 | 81 | 145 | 53 | 7 | 84 | 3 | 6 | 3 | 3 | 58 |
| river | 59.76 | 80 | 96 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 59 |
| shortest | 25.31 | 305 | 145 | 47 | 9 | 83 | 3 | 6 | 5 | 3 | 51 |
| hiking-mountain | 27.12 | 328 | 164 | 0 | 15 | 62 | 4 | 11 | 9 | 14 | 59 |
| rail | 28.66 | 38 | 106 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 76 |
| skating | 27.39 | 101 | 132 | 17 | 2 | 96 | 0 | 0 | 3 | 2 | 72 |
| dummy | 25.17 | 303 | 125 | 12 | 59 | 92 | 3 | 0 | 4 | 2 | 43 |
| car-vario | 26.03 | 34 | 124 | 19 | 71 | 98 | 0 | 0 | 2 | 0 | 56 |
| moped | 26.05 | 83 | 133 | 86 | 0 | 100 | 0 | 0 | 0 | 0 | 53 |
| car-vario-nocost | HTTP Error 500: Internal Server Error | | | | | | | | | | 0 |

### eisenach-brotterode  (shortest cycling 20.67 km, least climb 279 m)

| profile | km | min | climb | traffic | forbidden | paved | cmp | loose | cobbl | unk | mean score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gravel-v2 | 22.20 | 72 | 393 | 5 | 0 | 70 | 3 | 3 | 4 | 20 | 82 |
| gravel | 22.31 | 92 | 708 | 6 | 0 | 16 | 18 | 32 | 2 | 31 | 71 |
| gravel-v1 | 22.31 | 92 | 708 | 6 | 0 | 16 | 18 | 32 | 2 | 31 | 71 |
| commuter-v1 | 24.78 | 98 | 290 | 23 | 0 | 62 | 0 | 3 | 2 | 33 | 69 |
| touring-v1 | 24.74 | 98 | 290 | 24 | 0 | 63 | 0 | 3 | 1 | 33 | 69 |
| trekking | 24.74 | 98 | 290 | 24 | 0 | 63 | 0 | 3 | 1 | 33 | 69 |
| mtb | 20.67 | 130 | 661 | 2 | 0 | 3 | 7 | 74 | 6 | 9 | 63 |
| vm-forum-liegerad-schnell | 25.29 | 74 | 279 | 57 | 0 | 62 | 0 | 1 | 1 | 36 | 60 |
| fastbike | 24.62 | 96 | 279 | 78 | 0 | 73 | 0 | 1 | 1 | 24 | 51 |
| fastbike-verylowtraffic | 24.50 | 96 | 284 | 46 | 0 | 60 | 0 | 1 | 1 | 37 | 60 |
| vm-forum-velomobil-schnell | 25.15 | 69 | 281 | 78 | 0 | 74 | 0 | 1 | 1 | 24 | 55 |
| all | 19.23 | 26 | 775 | 1 | 0 | 11 | 9 | 54 | 2 | 24 | 77 |
| softaccess | 19.23 | 135 | 775 | 1 | 0 | 11 | 9 | 54 | 2 | 24 | 64 |
| river | 26.27 | 35 | 188 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 72 |
| shortest | 19.23 | 259 | 778 | 1 | 0 | 10 | 9 | 54 | 4 | 24 | 63 |
| hiking-mountain | 20.53 | 264 | 630 | 1 | 0 | 25 | 12 | 41 | 5 | 18 | 65 |
| rail | 4.35 | 6 | 14 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 79 |
| skating | target island detected for section 0 | | | | | | | | | | 0 |
| dummy | 19.23 | 259 | 775 | 1 | 0 | 11 | 9 | 54 | 2 | 24 | 63 |
| car-vario | 24.74 | 36 | 333 | 97 | 0 | 99 | 0 | 0 | 1 | 0 | 50 |
| moped | 21.34 | 89 | 306 | 71 | 0 | 93 | 1 | 0 | 2 | 4 | 59 |
| car-vario-nocost | HTTP Error 500: Internal Server Error | | | | | | | | | | 0 |

### leipzig-halle  (shortest cycling 37.02 km, least climb 31 m)

| profile | km | min | climb | traffic | forbidden | paved | cmp | loose | cobbl | unk | mean score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gravel-v2 | 43.46 | 99 | 33 | 0 | 0 | 54 | 29 | 7 | 8 | 2 | 82 |
| gravel | 43.46 | 99 | 33 | 0 | 0 | 54 | 29 | 7 | 8 | 2 | 82 |
| gravel-v1 | 43.46 | 99 | 33 | 0 | 0 | 54 | 29 | 7 | 8 | 2 | 82 |
| commuter-v1 | 43.17 | 122 | 31 | 2 | 0 | 70 | 15 | 5 | 9 | 1 | 77 |
| touring-v1 | 41.74 | 118 | 32 | 4 | 0 | 79 | 12 | 4 | 4 | 1 | 79 |
| trekking | 41.74 | 118 | 32 | 4 | 0 | 79 | 12 | 4 | 4 | 1 | 79 |
| mtb | 39.41 | 112 | 51 | 2 | 5 | 37 | 27 | 16 | 6 | 14 | 74 |
| vm-forum-liegerad-schnell | 40.25 | 91 | 57 | 64 | 0 | 98 | 0 | 0 | 2 | 1 | 58 |
| fastbike | 39.10 | 112 | 67 | 63 | 0 | 96 | 0 | 0 | 2 | 2 | 53 |
| fastbike-verylowtraffic | 37.02 | 106 | 64 | 60 | 0 | 90 | 0 | 0 | 6 | 3 | 57 |
| vm-forum-velomobil-schnell | 40.92 | 69 | 61 | 65 | 0 | 98 | 0 | 0 | 1 | 1 | 61 |
| all | 34.73 | 46 | 66 | 67 | 8 | 84 | 2 | 1 | 7 | 5 | 54 |
| softaccess | 34.73 | 100 | 66 | 67 | 8 | 84 | 2 | 1 | 7 | 5 | 48 |
| river | 47.15 | 63 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 72 |
| shortest | 35.13 | 418 | 56 | 52 | 9 | 77 | 8 | 4 | 9 | 2 | 46 |
| hiking-mountain | 36.81 | 438 | 40 | 4 | 15 | 48 | 11 | 13 | 14 | 14 | 59 |
| rail | no track found at pass=0 | | | | | | | | | | 0 |
| skating | 42.01 | 143 | 66 | 17 | 7 | 80 | 0 | 0 | 6 | 14 | 55 |
| dummy | 34.73 | 413 | 67 | 68 | 8 | 86 | 2 | 1 | 7 | 3 | 40 |
| car-vario | 39.01 | 55 | 53 | 35 | 61 | 100 | 0 | 0 | 0 | 0 | 52 |
| moped | 37.16 | 106 | 67 | 84 | 32 | 99 | 0 | 0 | 1 | 0 | 45 |
| car-vario-nocost | HTTP Error 500: Internal Server Error | | | | | | | | | | 0 |

### bath-bristol  (shortest cycling 20.89 km, least climb 68 m)

| profile | km | min | climb | traffic | forbidden | paved | cmp | loose | cobbl | unk | mean score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gravel-v2 | 26.07 | 60 | 72 | 1 | 1 | 98 | 0 | 0 | 0 | 1 | 79 |
| gravel | 26.07 | 60 | 72 | 1 | 1 | 98 | 0 | 0 | 0 | 1 | 79 |
| gravel-v1 | 26.07 | 60 | 72 | 1 | 1 | 98 | 0 | 0 | 0 | 1 | 79 |
| commuter-v1 | 25.69 | 75 | 68 | 1 | 0 | 99 | 0 | 0 | 1 | 0 | 76 |
| touring-v1 | 25.69 | 75 | 68 | 1 | 0 | 99 | 0 | 0 | 1 | 0 | 76 |
| trekking | 25.67 | 74 | 68 | 1 | 1 | 99 | 0 | 0 | 1 | 0 | 76 |
| mtb | 22.54 | 70 | 104 | 0 | 14 | 63 | 1 | 4 | 1 | 31 | 62 |
| vm-forum-liegerad-schnell | 22.48 | 54 | 91 | 24 | 1 | 74 | 0 | 0 | 1 | 25 | 74 |
| fastbike | 20.89 | 65 | 113 | 34 | 1 | 74 | 0 | 0 | 1 | 26 | 68 |
| fastbike-verylowtraffic | 21.43 | 66 | 113 | 27 | 1 | 75 | 0 | 0 | 1 | 24 | 70 |
| vm-forum-velomobil-schnell | 22.63 | 54 | 173 | 68 | 1 | 56 | 0 | 0 | 0 | 43 | 51 |
| all | 19.80 | 26 | 83 | 54 | 5 | 68 | 0 | 1 | 2 | 29 | 62 |
| softaccess | 19.80 | 60 | 83 | 54 | 5 | 68 | 0 | 1 | 2 | 29 | 61 |
| river | 27.01 | 36 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 71 |
| shortest | 19.62 | 234 | 89 | 59 | 3 | 73 | 0 | 1 | 1 | 25 | 49 |
| hiking-mountain | 22.04 | 262 | 96 | 0 | 38 | 60 | 0 | 22 | 6 | 12 | 35 |
| rail | 18.45 | 25 | 56 | 0 | 0 | 0 | 0 | 0 | 0 | 100 | 79 |
| skating | 25.52 | 88 | 69 | 0 | 6 | 98 | 0 | 0 | 2 | 1 | 67 |
| dummy | 19.62 | 234 | 89 | 59 | 3 | 73 | 0 | 1 | 1 | 25 | 48 |
| car-vario | 21.46 | 40 | 109 | 89 | 0 | 71 | 0 | 0 | 0 | 29 | 53 |
| moped | no track found at pass=0 | | | | | | | | | | 0 |
| car-vario-nocost | HTTP Error 500: Internal Server Error | | | | | | | | | | 0 |

### harz-climb  (shortest cycling 22.69 km, least climb 612 m)

| profile | km | min | climb | traffic | forbidden | paved | cmp | loose | cobbl | unk | mean score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gravel-v2 | 31.42 | 118 | 777 | 0 | 0 | 22 | 64 | 3 | 1 | 10 | 79 |
| gravel | 35.15 | 146 | 1032 | 4 | 1 | 34 | 52 | 3 | 0 | 12 | 67 |
| gravel-v1 | 35.15 | 146 | 1032 | 4 | 1 | 34 | 52 | 3 | 0 | 12 | 67 |
| commuter-v1 | 27.82 | 148 | 664 | 28 | 0 | 50 | 39 | 6 | 1 | 4 | 71 |
| touring-v1 | 22.74 | 132 | 617 | 71 | 0 | 85 | 11 | 2 | 1 | 1 | 62 |
| trekking | 22.74 | 132 | 617 | 71 | 0 | 85 | 11 | 2 | 1 | 1 | 62 |
| mtb | 23.65 | 148 | 754 | 0 | 0 | 11 | 59 | 28 | 1 | 1 | 82 |
| vm-forum-liegerad-schnell | 22.73 | 99 | 617 | 72 | 0 | 85 | 11 | 2 | 1 | 1 | 67 |
| fastbike | 23.72 | 135 | 617 | 70 | 0 | 89 | 9 | 2 | 0 | 0 | 62 |
| fastbike-verylowtraffic | 23.72 | 135 | 617 | 70 | 0 | 89 | 9 | 2 | 0 | 0 | 62 |
| vm-forum-velomobil-schnell | 22.69 | 109 | 612 | 74 | 0 | 87 | 11 | 2 | 0 | 0 | 65 |
| all | 23.16 | 31 | 685 | 52 | 4 | 62 | 16 | 14 | 4 | 4 | 68 |
| softaccess | 23.16 | 140 | 685 | 52 | 4 | 62 | 16 | 14 | 4 | 4 | 63 |
| river | target island detected for section 0 | | | | | | | | | | 0 |
| shortest | 21.89 | 285 | 672 | 57 | 4 | 67 | 17 | 14 | 1 | 0 | 54 |
| hiking-mountain | 23.10 | 300 | 707 | 0 | 23 | 6 | 41 | 41 | 6 | 6 | 52 |
| rail | no track found at pass=0 | | | | | | | | | | 0 |
| skating | target island detected for section 0 | | | | | | | | | | 0 |
| dummy | 21.89 | 285 | 672 | 57 | 4 | 67 | 17 | 14 | 1 | 0 | 54 |
| car-vario | 22.12 | 25 | 614 | 94 | 1 | 100 | 0 | 0 | 0 | 0 | 57 |
| moped | target island detected for section 0 | | | | | | | | | | 0 |
| car-vario-nocost | HTTP Error 500: Internal Server Error | | | | | | | | | | 0 |
